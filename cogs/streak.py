import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import os
import asyncio

# ====================================================================
# KONFIGURASI STREAK API
# ====================================================================
STREAK_ANNOUNCEMENT_ID = 1528376317756571648 # Ganti dengan ID Room Pengumuman Streak

# Ganti dengan ID Room Chat masing-masing Prodi
PRODI_ROOMS = {
    1526599646674161736: "DKV",
    1526601262861389964: "TEKINFO",
    1526606411591585932: "SISFOR",
    1526607541310591028: "TEKTEL"
}

MILESTONES = [3, 10, 30, 100, 200, 300, 400]
WIB = timezone(timedelta(hours=7))

# ====================================================================
# UI KONFIRMASI PEMULIHAN STREAK (TUMBAL XP)
# ====================================================================
class RestoreConfirmView(discord.ui.View):
    def __init__(self, cog, prodi_name, lost_streak):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.prodi_name = prodi_name
        self.lost_streak = lost_streak
        self.is_confirmed = False

    @discord.ui.button(label="TUMBALKAN XP & PULIHKAN STREAK", style=discord.ButtonStyle.danger, emoji="🔥")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.is_confirmed = True
        await interaction.response.defer()
        
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=self.prodi_name)
        
        if not role:
            await interaction.followup.send(f"❌ Role {self.prodi_name} tidak ditemukan di server ini.", ephemeral=True)
            return

        # 1. POTONG 50% XP & LEVEL SEMUA MEMBER DI PRODI TERSEBUT
        members_affected = 0
        for member in role.members:
            if member.bot: continue
            try:
                # Membagi XP dan Level menjadi setengah (dibaca dari tabel levels)
                await self.cog.bot.pool.execute('''
                    UPDATE levels 
                    SET xp = xp / 2, level = level / 2 
                    WHERE user_id = $1
                ''', member.id)
                members_affected += 1
            except Exception as e:
                print(f"[Error Restore] Gagal potong XP user {member.id}: {e}")

        # 2. PULIHKAN STREAK KE TANGGAL KEMARIN AGAR BISA DILANJUT HARI INI
        yesterday = datetime.now(WIB).date() - timedelta(days=1)
        await self.cog.bot.pool.execute('''
            UPDATE prodi_streaks
            SET current_streak = lost_streak, last_active_date = $1, lost_streak = 0
            WHERE prodi_name = $2
        ''', yesterday, self.prodi_name)

        embed = discord.Embed(
            title="🔥 STREAK BERHASIL DIPULIHKAN! 🔥",
            description=f"Ritual berhasil! Streak **{self.prodi_name}** telah kembali ke angka **{self.lost_streak} Hari**.\n\n"
                        f"💀 *Sebagai bayarannya, {members_affected} mahasiswa {self.prodi_name} telah kehilangan 50% XP dan Level mereka.*",
            color=discord.Color.brand_red()
        )
        embed.set_footer(text="Ayo cepat chat di room prodi hari ini sebelum streak mati lagi!")
        await interaction.edit_original_response(embed=embed, view=None)

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Ritual pemulihan dibatalkan. XP aman, tapi streak tetap hangus.", embed=None, view=None)


class StreakSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.is_db_ready = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_db_ready:
            await self.bot.pool.execute('''
                CREATE TABLE IF NOT EXISTS prodi_streaks (
                    prodi_name TEXT PRIMARY KEY,
                    current_streak INTEGER DEFAULT 0,
                    last_active_date DATE,
                    lost_streak INTEGER DEFAULT 0
                );
            ''')
            await self.bot.pool.execute('''
                CREATE TABLE IF NOT EXISTS daily_chatters (
                    prodi_name TEXT,
                    user_id BIGINT,
                    chat_date DATE,
                    PRIMARY KEY (prodi_name, user_id, chat_date)
                );
            ''')
            self.is_db_ready = True
            print("✅ Sistem Streak API (Modul Terpisah) siap!")

    # ====================================================================
    # SISTEM DETEKSI CHAT HARIAN & STREAK
    # ====================================================================
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if message.channel.id not in PRODI_ROOMS: return

        prodi_name = PRODI_ROOMS[message.channel.id]
        user_id = message.author.id
        today = datetime.now(WIB).date()
        yesterday = today - timedelta(days=1)

        # 1. Catat user yang chat hari ini
        await self.bot.pool.execute('''
            INSERT INTO daily_chatters (prodi_name, user_id, chat_date)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
        ''', prodi_name, user_id, today)

        # 2. Hitung jumlah orang berbeda
        count = await self.bot.pool.fetchval('''
            SELECT COUNT(*) FROM daily_chatters
            WHERE prodi_name = $1 AND chat_date = $2
        ''', prodi_name, today)

        # 3. Trigger saat mencapai pas 5 orang (Ubah ke 1 jika ingin ditest sendirian)
        if count == 2:
            record = await self.bot.pool.fetchrow('SELECT current_streak, last_active_date FROM prodi_streaks WHERE prodi_name = $1', prodi_name)

            new_streak = 1
            lost_streak_value = 0

            if record:
                last_date = record['last_active_date']
                
                if last_date == today:
                    return #2Sudah nambah hari ini
                elif last_date == yesterday:
                    new_streak = record['current_streak'] + 1
                    lost_streak_value = record.get('lost_streak', 0)
                else:
                    # STREAK MATI! Simpan streak lama ke lost_streak
                    lost_streak_value = record['current_streak']
                    new_streak = 1 

            # Simpan data streak ke DB
            await self.bot.pool.execute('''
                INSERT INTO prodi_streaks (prodi_name, current_streak, last_active_date, lost_streak)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (prodi_name) DO UPDATE
                SET current_streak = EXCLUDED.current_streak,
                    last_active_date = EXCLUDED.last_active_date,
                    lost_streak = EXCLUDED.lost_streak
            ''', prodi_name, new_streak, today, lost_streak_value)

            # --- TAMBAHAN NOTIFIKASI DI ROOM PRODI TERSEBUT ---
            notif_embed = discord.Embed(
                title="🔥 API STREAK MENYALA! 🔥",
                description=f"Kalian luar biasa! Target ngobrol harian tercapai.\nStreak **{prodi_name}** hari ini aman di angka **{new_streak} Hari**!",
                color=discord.Color.orange()
            )
            await message.channel.send(embed=notif_embed)

            # 4. Cek Milestone Pengumuman Gambar (Di Room Khusus)
            if new_streak in MILESTONES:
                ann_channel = self.bot.get_channel(STREAK_ANNOUNCEMENT_ID)
                if ann_channel:
                    filename = f"{prodi_name.lower()}_{new_streak}.png" 
                    teks = f"🔥 **WOW!** Prodi **{prodi_name}** berhasil mencapai **{new_streak} Hari Streak Api!** Terus pertahankan kekompakan kalian! 🚀"

                    if os.path.exists(filename):
                        file = discord.File(filename, filename=filename)
                        await ann_channel.send(content=teks, file=file)
                    else:
                        await ann_channel.send(content=f"{teks}\n*(Gambar {filename} belum diupload Admin)*")

    # ====================================================================
    # COMMAND: PULIHKAN STREAK (KHUSUS ADMIN)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def pulihkanstreak(self, ctx, prodi: str = None):
        """Command untuk memulihkan streak yang mati dengan menumbalkan XP"""
        if not prodi:
            await ctx.send("⚠️ Format salah! Gunakan: `!pulihkanstreak <NamaProdi>` (Contoh: `!pulihkanstreak DKV`)")
            return
            
        prodi = prodi.upper()
        if prodi not in PRODI_ROOMS.values():
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak valid. Pilihan: DKV, TEKINFO, SISFOR, TEKTEL.")
            return

        record = await self.bot.pool.fetchrow("SELECT current_streak, lost_streak FROM prodi_streaks WHERE prodi_name = $1", prodi)
        
        if not record or record['lost_streak'] == 0:
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak memiliki Streak Api yang mati untuk dipulihkan.")
            return

        embed = discord.Embed(
            title="⚠️ PERINGATAN RITUAL PEMULIHAN ⚠️",
            description=f"Kamu akan memulihkan Streak **{prodi}** yang mati di angka **{record['lost_streak']} Hari**.\n\n"
                        f"**KONSEKUENSI:**\nJika kamu melanjutkan, **SELURUH MEMBER** dengan role {prodi} akan mengalami pemotongan **50% XP dan 50% Level** saat ini juga.\n\n"
                        f"Apakah kamu yakin ingin menumbalkan XP mereka untuk menyelamatkan Streak ini?",
            color=discord.Color.dark_red()
        )

        view = RestoreConfirmView(self, prodi, record['lost_streak'])
        await ctx.send(embed=embed, view=view)

    # ====================================================================
    # COMMAND: MATIKAN STREAK (UNTUK TESTING / ADMIN)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def matikanstreak(self, ctx, prodi: str = None):
        """Command untuk mematikan streak secara paksa (Untuk Testing)"""
        if not prodi:
            await ctx.send("⚠️ Format salah! Gunakan: `!matikanstreak <NamaProdi>` (Contoh: `!matikanstreak DKV`)")
            return
            
        prodi = prodi.upper()
        if prodi not in PRODI_ROOMS.values():
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak valid. Pilihan: DKV, TEKINFO, SISFOR, TEKTEL.")
            return

        record = await self.bot.pool.fetchrow("SELECT current_streak FROM prodi_streaks WHERE prodi_name = $1", prodi)
        
        if not record or record['current_streak'] == 0:
            await ctx.send(f"⚠️ Prodi **{prodi}** saat ini tidak memiliki streak yang aktif (Streak sudah 0).")
            return

        current_streak = record['current_streak']
        # Memundurkan last_active_date menjadi 2 hari yang lalu agar dianggap putus
        dua_hari_lalu = datetime.now(WIB).date() - timedelta(days=2)

        # Pindahkan nilai ke lost_streak dan nol-kan current_streak
        await self.bot.pool.execute('''
            UPDATE prodi_streaks
            SET lost_streak = $1, current_streak = 0, last_active_date = $2
            WHERE prodi_name = $3
        ''', current_streak, dua_hari_lalu, prodi)

        embed = discord.Embed(
            title="🛑 STREAK DIMATIKAN PAKSA",
            description=f"Streak **{prodi}** (Sebesar {current_streak} Hari) telah dimatikan secara paksa oleh Admin.\n\n"
                        f"Gunakan command `!pulihkanstreak {prodi}` untuk mengetes fitur pemulihan dan penumbalan XP.",
            color=discord.Color.dark_grey()
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(StreakSystem(bot))