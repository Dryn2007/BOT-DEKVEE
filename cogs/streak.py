import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import os
import asyncio

# --- IMPORT PLUGIN DARI LUAR ---
from easy_pil import Editor, Canvas, Font

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

        yesterday = datetime.now(WIB).date() - timedelta(days=1)
        await self.cog.bot.pool.execute('''
            UPDATE prodi_streaks
            SET current_streak = lost_streak, last_active_date = $1, lost_streak = 0
            WHERE prodi_name = $2
        ''', yesterday, self.prodi_name)

        try:
            await interaction.message.unpin(reason="Streak telah dipulihkan")
            await interaction.message.delete()
        except:
            pass

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
    # ENGINE GAMBAR EASY-PIL UNTUK PENGUMUMAN STREAK
    # ====================================================================
    async def kirim_kartu_pengumuman(self, ann_channel, prodi_name, new_streak, filename):
        if not os.path.exists(filename):
            await ann_channel.send(f"🔥 **WOW!** Prodi **{prodi_name}** berhasil mencapai **{new_streak} Hari Streak Api!**\n*(Gambar {filename} belum diupload Admin)*")
            return

        try:
            # 1. Buat Canvas / Latar Belakang Gelap Khas Discord
            background = Editor(Canvas((850, 300), color="#121212"))
            
            # 2. Kotak Panel Utama & Garis Hiasan Orange
            background.rectangle((15, 15), width=820, height=270, color="#1E1F22", radius=25)
            background.rectangle((15, 15), width=30, height=270, color="#FF4500", radius=25)

            # 3. Masukkan Maskot (Dari file lokal)
            mascot = Editor(filename).resize((250, 250))
            background.paste(mascot, (60, 25))

            # 4. Pengaturan Font
            font_title = Font.poppins(size=35, variant="bold")
            font_prodi = Font.poppins(size=60, variant="black")
            font_desc = Font.poppins(size=25, variant="bold")

            # 5. Tulis Teks ke dalam Canvas
            background.text((330, 60), "STREAK MILESTONE!", font=font_title, color="#FFD700")
            background.text((330, 110), f"PRODI {prodi_name}", font=font_prodi, color="#FFFFFF")
            background.text((330, 200), f"🔥 {new_streak} DAYS OF FIRE STREAK 🔥", font=font_desc, color="#FF4500")

            # 6. Jadikan file gambar Discord
            file = discord.File(fp=background.image_bytes, filename="milestone.png")

            # 7. Bungkus dalam Discord Embed agar UI sangat mewah
            embed = discord.Embed(
                title=f"🎉 PENGUMUMAN STREAK KAMPUS 🎉",
                description=f"Kerja bagus **{prodi_name}**! Kalian berhasil membuktikan kekompakan yang luar biasa. Terus bakar semangat kalian! 🚀",
                color=discord.Color.orange()
            )
            embed.set_image(url="attachment://milestone.png")
            embed.set_footer(text=f"Telkom University Jakarta • {new_streak} Hari Streak Api")

            # Kirim Pesan
            await ann_channel.send(embed=embed, file=file)
            
        except Exception as e:
            print(f"Error Easy-Pil: {e}")
            # Fallback jika EasyPil Error (Gunakan Embed biasa)
            file = discord.File(filename, filename="maskot.png")
            embed = discord.Embed(
                title=f"🔥 PRODI {prodi_name} MENCAPAI {new_streak} HARI STREAK! 🔥",
                color=discord.Color.orange()
            )
            embed.set_image(url="attachment://maskot.png")
            await ann_channel.send(embed=embed, file=file)

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

        await self.bot.pool.execute('''
            INSERT INTO daily_chatters (prodi_name, user_id, chat_date)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
        ''', prodi_name, user_id, today)

        count = await self.bot.pool.fetchval('''
            SELECT COUNT(*) FROM daily_chatters
            WHERE prodi_name = $1 AND chat_date = $2
        ''', prodi_name, today)

        # TRIGGER STREAK (SET 5)
        if count == 5:
            record = await self.bot.pool.fetchrow('SELECT current_streak, last_active_date FROM prodi_streaks WHERE prodi_name = $1', prodi_name)
            new_streak = 1
            lost_streak_value = 0
            streak_mati = False

            if record:
                last_date = record['last_active_date']
                if last_date == today:
                    return 
                elif last_date == yesterday:
                    new_streak = record['current_streak'] + 1
                    lost_streak_value = record.get('lost_streak', 0)
                else:
                    lost_streak_value = record['current_streak']
                    new_streak = 1 
                    if lost_streak_value > 0:
                        streak_mati = True

            await self.bot.pool.execute('''
                INSERT INTO prodi_streaks (prodi_name, current_streak, last_active_date, lost_streak)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (prodi_name) DO UPDATE
                SET current_streak = EXCLUDED.current_streak,
                    last_active_date = EXCLUDED.last_active_date,
                    lost_streak = EXCLUDED.lost_streak
            ''', prodi_name, new_streak, today, lost_streak_value)

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
                try: await msg_pin.pin(reason="Pemberitahuan Kematian Streak")
                except: pass
            else:
                notif_embed = discord.Embed(
                    title="🔥 API STREAK MENYALA! 🔥",
                    description=f"Kalian luar biasa! Target ngobrol harian tercapai.\nStreak **{prodi_name}** hari ini aman di angka **{new_streak} Hari**!",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=notif_embed)

                # PEMANGGILAN FUNGSI EASY-PIL DI SINI
                if new_streak in MILESTONES:
                    ann_channel = self.bot.get_channel(STREAK_ANNOUNCEMENT_ID)
                    if ann_channel:
                        filename = f"{prodi_name.lower()}_{new_streak}.png" 
                        await self.kirim_kartu_pengumuman(ann_channel, prodi_name, new_streak, filename)

    # ====================================================================
    # COMMAND: SET STREAK (UNTUK TESTING MILESTONE INSTAN)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setstreak(self, ctx, prodi: str = None, jumlah: int = None):
        if not prodi or jumlah is None:
            await ctx.send("⚠️ Format salah! Gunakan: `!setstreak <NamaProdi> <Jumlah>`")
            return
            
        prodi = prodi.upper()
        if prodi not in PRODI_ROOMS.values():
            await ctx.send(f"⚠️ Prodi **{prodi}** tidak valid.")
            return

        today = datetime.now(WIB).date()

        await self.bot.pool.execute('''
            INSERT INTO prodi_streaks (prodi_name, current_streak, last_active_date, lost_streak)
            VALUES ($1, $2, $3, 0)
            ON CONFLICT (prodi_name) DO UPDATE
            SET current_streak = EXCLUDED.current_streak,
                last_active_date = EXCLUDED.last_active_date
        ''', prodi, jumlah, today)

        await ctx.send(f"✅ Streak **{prodi}** berhasil disuntik menjadi **{jumlah} Hari**.")

        # PEMANGGILAN FUNGSI EASY-PIL DI SINI (Buat Testing)
        if jumlah in MILESTONES:
            ann_channel = self.bot.get_channel(STREAK_ANNOUNCEMENT_ID)
            if ann_channel:
                filename = f"{prodi.lower()}_{jumlah}.png" 
                await self.kirim_kartu_pengumuman(ann_channel, prodi, jumlah, filename)

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
        try: await msg.pin()
        except: pass

    # ====================================================================
    # COMMAND: MATIKAN STREAK (UNTUK TESTING)
    # ====================================================================
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def matikanstreak(self, ctx, prodi: str = None):
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

        await self.bot.pool.execute('''
            UPDATE prodi_streaks
            SET lost_streak = $1, current_streak = 0, last_active_date = $2
            WHERE prodi_name = $3
        ''', current_streak, dua_hari_lalu, prodi)

        await ctx.send(f"✅ Streak **{prodi}** berhasil dimatikan paksa. Pesan pemulihan telah dikirim ke room mereka.")

        target_channel_id = None
        for cid, pname in PRODI_ROOMS.items():
            if pname == prodi:
                target_channel_id = cid
                break

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
                try: await msg_pin.pin(reason="Pemberitahuan Kematian Streak")
                except: pass

async def setup(bot):
    await bot.add_cog(StreakSystem(bot))