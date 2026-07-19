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
        super().__init__(timeout=86400.0) # Timeout 1 hari
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
            await interaction.channel.send(f"❌ Role {self.prodi_name} tidak ditemukan di server ini.")
            return

        # 1. POTONG 50% XP & KALKULASI ULANG LEVEL SECARA AKURAT
        members_affected = 0
        for member in role.members:
            if member.bot: continue
            try:
                record = await self.cog.bot.pool.fetchrow("SELECT xp FROM levels WHERE user_id = $1", member.id)
                if record:
                    new_xp = int(record['xp'] / 2)
                    new_level = 1
                    while 50 * (new_level ** 2) <= new_xp:
                        new_level += 1
                    
                    await self.cog.bot.pool.execute('''
                        UPDATE levels 
                        SET xp = $1, level = $2 
                        WHERE user_id = $3
                    ''', new_xp, new_level, member.id)
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

        # 3. UNPIN & HAPUS PESAN LAMA
        try:
            await interaction.message.unpin(reason="Streak telah dipulihkan")
            await interaction.message.delete()
        except:
            pass

        # 4. KIRIM PENGUMUMAN SUKSES
        embed = discord.Embed(
            title="🔥 STREAK BERHASIL DIPULIHKAN! 🔥",
            description=f"Ritual berhasil, {interaction.user.mention}! Streak **{self.prodi_name}** telah kembali ke angka **{self.lost_streak} Hari**.\n\n"
                        f"💀 *Sebagai bayarannya, {members_affected} mahasiswa {self.prodi_name} telah kehilangan 50% XP mereka (Level telah disesuaikan).* \n\n"
                        f"*(Kalian bisa mengecek rank baru kalian dengan menggunakan command !rank)*",
            color=discord.Color.brand_red()
        )
        embed.set_footer(text="Ayo cepat chat di room prodi hari ini sebelum streak mati lagi!")
        await interaction.channel.send(embed=embed)

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        # UNPIN & HAPUS PESAN
        try:
            await interaction.message.unpin(reason="Pemulihan dibatalkan")
            await interaction.message.delete()
        except:
            pass
            
        await interaction.channel.send(f"❌ Ritual pemulihan dibatalkan oleh {interaction.user.mention}. XP aman, tapi streak tetap hangus.")


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
            print("✅ Sistem Streak API siap!")

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

        # 3. Trigger saat mencapai pas 5 orang
        if count == 5:
            record = await self.bot.pool.fetchrow('SELECT current_streak, last_active_date FROM prodi_streaks WHERE prodi_name = $1', prodi_name)

            new_streak = 1
            lost_streak_value = 0
            streak_mati = False

            if record:
                last_date = record['last_active_date']
                
                if last_date == today:
                    return # Sudah nambah hari ini
                elif last_date == yesterday:
                    new_streak = record['current_streak'] + 1
                    lost_streak_value = record.get('lost_streak', 0)
                else:
                    # STREAK MATI!
                    lost_streak_value = record['current_streak']
                    new_streak = 1 
                    if lost_streak_value > 0:
                        streak_mati = True

            # Simpan data streak ke DB
            await self.bot.pool.execute('''
                INSERT INTO prodi_streaks (prodi_name, current_streak, last_active_date, lost_streak)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (prodi_name) DO UPDATE
                SET current_streak = EXCLUDED.current_streak,
                    last_active_date = EXCLUDED.last_active_date,
                    lost_streak = EXCLUDED.lost_streak
            ''', prodi_name, new_streak, today, lost_streak_value)

            # JIKA STREAK TERNYATA MATI, MUNCULKAN TOMBOL PIN OTOMATIS
            if streak_mati:
                embed_mati = discord.Embed(
                    title="💔 STREAK API MATI!",
                    description=f"Oh tidak! Kalian tidak mencapai target harian kemarin, sehingga Streak **{lost_streak_value} Hari** kalian hangus!\n\n"
                                "Tapi tenang, kalian bisa **PULIHKAN STREAK** ini sekarang juga.\n"
                                f"⚠️ **Syarat:** Seluruh member {prodi_name} akan kehilangan **50% XP dan Level**.\n"
                                "Silakan klik tombol di bawah jika kalian berani berkorban!",
                    color=discord.Color.dark_red()
                )
                view = RestoreConfirmView(self, prodi_name, lost_streak_value)
                msg_pin = await message.channel.send(embed=embed_mati, view=view)
                try:
                    await msg_pin.pin(reason="Pemberitahuan Kematian Streak")
                except:
                    pass
            else:
                # JIKA AMAN, BERI NOTIFIKASI NYALA
                notif_embed = discord.Embed(
                    title="🔥 API STREAK MENYALA! 🔥",
                    description=f"Kalian luar biasa! Target ngobrol harian tercapai.\nStreak **{prodi_name}** hari ini aman di angka **{new_streak} Hari**!",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=notif_embed)

                # Cek Milestone Pengumuman Gambar (Di Room Khusus)
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
    # COMMAND: SET STREAK (UNTUK TESTING MILESTONE INSTAN)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setstreak(self, ctx, prodi: str = None, jumlah: int = None):
        """Command mengubah angka streak secara instan untuk testing Milestone"""
        if not prodi or jumlah is None:
            await ctx.send("⚠️ Format salah! Gunakan: `!setstreak <NamaProdi> <Jumlah>` (Contoh: `!setstreak DKV 3`)")
            return
            
        prodi = prodi.upper()
        if prodi not in PRODI_ROOMS.values():
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak valid.")
            return

        today = datetime.now(WIB).date()

        # Update database streak
        await self.bot.pool.execute('''
            INSERT INTO prodi_streaks (prodi_name, current_streak, last_active_date, lost_streak)
            VALUES ($1, $2, $3, 0)
            ON CONFLICT (prodi_name) DO UPDATE
            SET current_streak = EXCLUDED.current_streak,
                last_active_date = EXCLUDED.last_active_date
        ''', prodi, jumlah, today)

        await ctx.send(f"✅ Streak **{prodi}** berhasil disuntik menjadi **{jumlah} Hari**.")

        # Cek jika angka yang disuntik adalah angka Milestone, langsung kirim gambarnya!
        if jumlah in MILESTONES:
            ann_channel = self.bot.get_channel(STREAK_ANNOUNCEMENT_ID)
            if ann_channel:
                filename = f"{prodi.lower()}_{jumlah}.png" 
                teks = f"🔥 **WOW!** Prodi **{prodi}** berhasil mencapai **{jumlah} Hari Streak Api!** Terus pertahankan kekompakan kalian! 🚀"

                if os.path.exists(filename):
                    file = discord.File(filename, filename=filename)
                    await ann_channel.send(content=teks, file=file)
                else:
                    await ann_channel.send(content=f"{teks}\n*(Gambar {filename} belum diupload Admin)*")

    # ====================================================================
    # COMMAND: PULIHKAN STREAK (MANUAL OLEH ADMIN JIKA DIPERLUKAN)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def pulihkanstreak(self, ctx, prodi: str = None):
        if not prodi:
            await ctx.send("⚠️ Format salah! Gunakan: `!pulihkanstreak <NamaProdi>`")
            return
            
        prodi = prodi.upper()
        if prodi not in PRODI_ROOMS.values():
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak valid.")
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
        msg = await ctx.send(embed=embed, view=view)
        try:
            await msg.pin()
        except:
            pass

    # ====================================================================
    # COMMAND: MATIKAN STREAK (UNTUK TESTING)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def matikanstreak(self, ctx, prodi: str = None):
        """Mematikan streak dan langsung mengirimkan Pin Pemulihan ke Room Prodi"""
        if not prodi:
            await ctx.send("⚠️ Format salah! Gunakan: `!matikanstreak <NamaProdi>`")
            return
            
        prodi = prodi.upper()
        if prodi not in PRODI_ROOMS.values():
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak valid.")
            return

        record = await self.bot.pool.fetchrow("SELECT current_streak FROM prodi_streaks WHERE prodi_name = $1", prodi)
        
        if not record or record['current_streak'] == 0:
            await ctx.send(f"⚠️ Prodi **{prodi}** saat ini tidak memiliki streak yang aktif (Streak sudah 0).")
            return

        current_streak = record['current_streak']
        dua_hari_lalu = datetime.now(WIB).date() - timedelta(days=2)

        # Simpan ke lost_streak
        await self.bot.pool.execute('''
            UPDATE prodi_streaks
            SET lost_streak = $1, current_streak = 0, last_active_date = $2
            WHERE prodi_name = $3
        ''', current_streak, dua_hari_lalu, prodi)

        await ctx.send(f"✅ Streak **{prodi}** berhasil dimatikan paksa. Pesan pemulihan telah dikirim ke room mereka.")

        # Cari ID Room Prodi
        target_channel_id = None
        for cid, pname in PRODI_ROOMS.items():
            if pname == prodi:
                target_channel_id = cid
                break

        # Kirim Tombol Pemulihan ke Room Mereka
        if target_channel_id:
            target_channel = self.bot.get_channel(target_channel_id)
            if target_channel:
                embed_mati = discord.Embed(
                    title="💔 STREAK API MATI!",
                    description=f"Oh tidak! Streak **{current_streak} Hari** kalian telah putus!\n\n"
                                "Tapi tenang, kalian bisa **PULIHKAN STREAK** ini sekarang juga.\n"
                                f"⚠️ **Syarat:** Seluruh member {prodi} akan kehilangan **50% XP dan Level**.\n"
                                "Silakan klik tombol di bawah jika kalian berani berkorban!",
                    color=discord.Color.dark_red()
                )
                view = RestoreConfirmView(self, prodi, current_streak)
                msg_pin = await target_channel.send(embed=embed_mati, view=view)
                try:
                    await msg_pin.pin(reason="Pemberitahuan Kematian Streak")
                except:
                    pass

    # ====================================================================
    # COMMAND DARURAT: KEMBALIKAN XP (FIX BUG)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def kembalikanxp(self, ctx, prodi: str = None):
        """Command darurat untuk mengembalikan XP"""
        if not prodi:
            await ctx.send("⚠️ Format salah! Gunakan: `!kembalikanxp <NamaProdi>`")
            return
            
        prodi = prodi.upper()
        if prodi not in PRODI_ROOMS.values():
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak valid.")
            return

        role = discord.utils.get(ctx.guild.roles, name=prodi)
        if not role:
            await ctx.send(f"❌ Role {prodi} tidak ditemukan di server ini.")
            return

        members_affected = 0
        for member in role.members:
            if member.bot: continue
            try:
                record = await self.bot.pool.fetchrow("SELECT xp FROM levels WHERE user_id = $1", member.id)
                if record:
                    restored_xp = record['xp'] * 2
                    restored_level = 1
                    while 50 * (restored_level ** 2) <= restored_xp:
                        restored_level += 1
                    
                    await self.bot.pool.execute('''
                        UPDATE levels 
                        SET xp = $1, level = $2 
                        WHERE user_id = $3
                    ''', restored_xp, restored_level, member.id)
                    members_affected += 1
            except Exception as e:
                print(f"[Error Refund] Gagal mengembalikan XP user {member.id}: {e}")

        embed = discord.Embed(
            title="🩹 XP BERHASIL DIPULIHKAN!",
            description=f"Kompensasi berhasil diberikan! XP milik **{members_affected} mahasiswa {prodi}** telah dikali 2 (dikembalikan seperti semula), dan Level mereka telah diperbaiki secara otomatis.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(StreakSystem(bot))