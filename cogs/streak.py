import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import os
import asyncio
import requests
from io import BytesIO
from PIL import ImageDraw, Image

# --- IMPORT PLUGIN DARI LUAR ---
from easy_pil import Editor, Canvas, Font
from PIL import ImageDraw

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
# HELPER: GAMBAR IKON VEKTOR MANUAL (TIDAK BUTUH FONT EMOJI)
# ====================================================================
def draw_flame_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str):
    """Menggambar ikon api sederhana menggunakan polygon (tidak bergantung pada font emoji)."""
    s = size
    points = [
        (x + s * 0.50, y),
        (x + s * 0.85, y + s * 0.40),
        (x + s * 0.70, y + s * 0.40),
        (x + s * 0.95, y + s * 0.78),
        (x + s * 0.50, y + s * 1.05),
        (x + s * 0.05, y + s * 0.78),
        (x + s * 0.30, y + s * 0.40),
        (x + s * 0.15, y + s * 0.40),
    ]
    draw.polygon(points, fill=color)


def draw_chat_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str):
    """Menggambar ikon balon chat sederhana menggunakan rounded rectangle + ekor segitiga."""
    w = size
    h = size * 0.72
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h * 0.35, fill=color)
    draw.polygon(
        [
            (x + w * 0.18, y + h - 1),
            (x + w * 0.38, y + h - 1),
            (x + w * 0.18, y + h * 1.35),
        ],
        fill=color,
    )


# ====================================================================
# UI KONFIRMASI PEMULIHAN STREAK (TUMBAL XP)
# ====================================================================
class RestoreConfirmView(discord.ui.View):
    def __init__(self, cog, prodi_name, lost_streak):
        super().__init__(timeout=86400.0)
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
            await interaction.channel.send(f"❌ Role {self.prodi_name} tidak ditemukan.")
            return

        members_affected = 0
        for member in role.members:
            if member.bot:
                continue
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
            description=f"Ritual berhasil, {interaction.user.mention}! Streak **{self.prodi_name}** kembali ke **{self.lost_streak} Hari**.\n\n"
                        f"💀 *{members_affected} mahasiswa {self.prodi_name} telah kehilangan 50% XP mereka (Level disesuaikan).* \n\n"
                        f"*(Kalian bisa mengecek rank baru kalian dengan command !rank)*",
            color=discord.Color.brand_red()
        )
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
            # Coba tambahkan kolom total_messages jika tabel lama sudah terlanjur ada
            try:
                await self.bot.pool.execute('ALTER TABLE prodi_streaks ADD COLUMN total_messages INTEGER DEFAULT 0;')
            except:
                pass

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
    # ENGINE GAMBAR EASY-PIL UNTUK PENGUMUMAN STREAK (UI PREMIUM)
    # ====================================================================
    async def kirim_kartu_pengumuman(self, ann_channel, prodi_name, new_streak, total_messages, filename):
        if not os.path.exists(filename):
            await ann_channel.send(f"🔥 **WOW!** Prodi **{prodi_name}** mencapai **{new_streak} Hari Streak Api!**\n*(Gambar {filename} belum diupload Admin)*")
            return

        try:
            # 1. Canvas Utama (Warna Gelap Elegan)
            background = Editor(Canvas((900, 350), color="#1A1C20"))

            # 2. Hiasan Kotak Dalam & Aksen Warna
            background.rectangle((20, 20), width=860, height=310, color="#2B2D31", radius=30)
            
            # Garis aksen oranye di sebelah kiri (Floating Accent)
            # Radius dibuat kecil (6) agar sesuai dengan lebarnya (12)
            background.rectangle((30, 50), width=12, height=250, color="#FF4500", radius=6)

            # 3. Garis Pemisah (Divider)
            background.rectangle((350, 115), width=500, height=3, color="#1A1C20", radius=2)

            # 4. Masukkan Maskot
            mascot = Editor(filename).resize((280, 280))
            background.paste(mascot, (60, 35))

            # 5. Konfigurasi Font
            font_super = Font.poppins(size=25, variant="bold")
            font_title = Font.poppins(size=65, variant="bold")
            font_badge = Font.poppins(size=22, variant="bold")

            # 6. Teks Utama
            background.text((350, 50), "MILESTONE UNLOCKED!", font=font_super, color="#FFD700")
            background.text((350, 80), f"PRODI {prodi_name}", font=font_title, color="#FFFFFF")

            # Ambil PIL ImageDraw langsung dari canvas easy-pil untuk menggambar ikon vektor
            draw = ImageDraw.Draw(background.image)

            # Ambil PIL ImageDraw langsung dari canvas easy-pil untuk menggambar ikon vektor
            draw = ImageDraw.Draw(background.image)

            # Ambil PIL ImageDraw langsung dari canvas easy-pil untuk menggambar ikon vektor
            draw = ImageDraw.Draw(background.image)

            # ==========================================
            # PERHITUNGAN DINAMIS & RENDER IKON EKSTERNAL
            # ==========================================
            draw = ImageDraw.Draw(background.image)
            pil_font = font_badge.font 
            icon_size = 28
            spacing = 10 # Jarak antara ikon dan teks

            # 7. Badge / Pill 1: STREAK API (Kapsul Oranye)
            background.rectangle((350, 150), width=260, height=60, color="#FF4500", radius=30)
            
            text_streak = f"{new_streak} DAYS STREAK"
            text_streak_width = int(draw.textlength(text_streak, font=pil_font))
            total_width_streak = icon_size + spacing + text_streak_width
            
            # Titik tengah X dari kapsul oranye adalah 480 (350 + (260/2))
            start_x_streak = 480 - (total_width_streak / 2)
            
            # Ambil ikon Api dari Twemoji (Eksternal)
            try:
                res_fire = requests.get("https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/1f525.png")
                img_fire = Image.open(BytesIO(res_fire.content)).convert("RGBA").resize((icon_size, icon_size))
                background.image.paste(img_fire, (int(start_x_streak), 166), img_fire)
            except: pass

            # Render Teks Streak (Posisinya bergeser menyesuaikan ikon)
            text_x_streak = start_x_streak + icon_size + spacing
            background.text((text_x_streak, 166), text_streak, font=font_badge, color="#FFFFFF", align="left")


            # 8. Badge / Pill 2: TOTAL MESSAGES (Kapsul Abu-abu)
            background.rectangle((630, 150), width=230, height=60, color="#1A1C20", radius=30)
            
            text_chat = f"{total_messages} CHATS"
            text_chat_width = int(draw.textlength(text_chat, font=pil_font))
            total_width_chat = icon_size + spacing + text_chat_width
            
            # Titik tengah X dari kapsul abu-abu adalah 745 (630 + (230/2))
            start_x_chat = 745 - (total_width_chat / 2)

            # Ambil ikon Chat dari Twemoji (Eksternal)
            try:
                res_chat = requests.get("https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/1f4ac.png")
                img_chat = Image.open(BytesIO(res_chat.content)).convert("RGBA").resize((icon_size, icon_size))
                background.image.paste(img_chat, (int(start_x_chat), 166), img_chat)
            except: pass

            # Render Teks Chat
            text_x_chat = start_x_chat + icon_size + spacing
            background.text((text_x_chat, 166), text_chat, font=font_badge, color="#A5A7AA", align="left")
            
            # --- PANGGIL FUNGSI GAMBAR IKON CHAT ---
            draw_chat_icon(draw, x=655, y=168, size=24, color="#A5A7AA")
            # Teks digeser ke X=695 dan diubah ke rata kiri, tanpa emoji
            background.text((695, 165), f"{total_messages} CHATS", font=font_badge, color="#A5A7AA", align="left")

            # 9. Teks Hiasan Bawah
            background.text((350, 260), "Keep the fire burning and never break the streak!", font=Font.poppins(size=18, variant="italic"), color="#80848E")

            file = discord.File(fp=background.image_bytes, filename="milestone.png")

            embed = discord.Embed(
                title=f"🎉 PENGUMUMAN STREAK KAMPUS 🎉",
                description=f"Kerja bagus **{prodi_name}**! Kalian telah mengumpulkan **{total_messages} pesan** sejauh ini dan berhasil mengamankan streak harian kalian. Terus bakar semangat kalian! 🚀",
                color=discord.Color.orange()
            )
            embed.set_image(url="attachment://milestone.png")
            embed.set_footer(text=f"Telkom University Jakarta")

            await ann_channel.send(embed=embed, file=file)

        except Exception as e:
            print(f"Error Easy-Pil: {e}")
            file = discord.File(filename, filename="maskot.png")
            await ann_channel.send(content=f"🔥 PRODI {prodi_name} MENCAPAI {new_streak} HARI STREAK!", file=file)

    # ====================================================================
    # SISTEM DETEKSI CHAT HARIAN & STREAK
    # ====================================================================
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.channel.id not in PRODI_ROOMS:
            return

        prodi_name = PRODI_ROOMS[message.channel.id]
        user_id = message.author.id
        today = datetime.now(WIB).date()
        yesterday = today - timedelta(days=1)

        # 1. Tambah Total Chat Keseluruhan
        await self.bot.pool.execute('''
            INSERT INTO prodi_streaks (prodi_name, total_messages)
            VALUES ($1, 1)
            ON CONFLICT (prodi_name) DO UPDATE
            SET total_messages = COALESCE(prodi_streaks.total_messages, 0) + 1
        ''', prodi_name)

        # 2. Catat Orang Yang Chat Hari Ini
        await self.bot.pool.execute('''
            INSERT INTO daily_chatters (prodi_name, user_id, chat_date)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
        ''', prodi_name, user_id, today)

        # 3. Hitung Jumlah Orang Berbeda
        count = await self.bot.pool.fetchval('''
            SELECT COUNT(*) FROM daily_chatters
            WHERE prodi_name = $1 AND chat_date = $2
        ''', prodi_name, today)

        # TRIGGER STREAK (SET 5)
        if count == 5:
            # FIX: tambahkan 'lost_streak' ke SELECT, sebelumnya tidak diambil
            # sehingga record.get('lost_streak', 0) selalu balik ke 0.
            record = await self.bot.pool.fetchrow(
                'SELECT current_streak, last_active_date, total_messages, lost_streak FROM prodi_streaks WHERE prodi_name = $1',
                prodi_name
            )

            new_streak = 1
            lost_streak_value = 0
            streak_mati = False
            total_messages_saat_ini = record['total_messages'] if record else 1

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

            # Simpan Streak
            await self.bot.pool.execute('''
                UPDATE prodi_streaks 
                SET current_streak = $1, last_active_date = $2, lost_streak = $3 
                WHERE prodi_name = $4
            ''', new_streak, today, lost_streak_value, prodi_name)

            if streak_mati:
                # Reset total chat kembali ke 0 karena streaknya mati
                await self.bot.pool.execute('UPDATE prodi_streaks SET total_messages = 0 WHERE prodi_name = $1', prodi_name)

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
                notif_embed = discord.Embed(
                    title="🔥 API STREAK MENYALA! 🔥",
                    description=f"Kalian luar biasa! Target ngobrol harian tercapai.\nStreak **{prodi_name}** hari ini aman di angka **{new_streak} Hari**!",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=notif_embed)

                if new_streak in MILESTONES:
                    ann_channel = self.bot.get_channel(STREAK_ANNOUNCEMENT_ID)
                    if ann_channel:
                        filename = f"{prodi_name.lower()}_{new_streak}.png"
                        await self.kirim_kartu_pengumuman(ann_channel, prodi_name, new_streak, total_messages_saat_ini, filename)

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

        # FIX: ambil total_messages ASLI yang sudah tercatat, bukan dummy (jumlah * 125)
        existing = await self.bot.pool.fetchrow(
            'SELECT total_messages FROM prodi_streaks WHERE prodi_name = $1', prodi
        )
        real_total_messages = existing['total_messages'] if existing and existing['total_messages'] else 0

        await self.bot.pool.execute('''
            INSERT INTO prodi_streaks (prodi_name, current_streak, last_active_date, lost_streak, total_messages)
            VALUES ($1, $2, $3, 0, $4)
            ON CONFLICT (prodi_name) DO UPDATE
            SET current_streak = EXCLUDED.current_streak,
                last_active_date = EXCLUDED.last_active_date
        ''', prodi, jumlah, today, real_total_messages)
        # Catatan: total_messages SENGAJA tidak ikut di-overwrite di klausa UPDATE,
        # supaya command ini tidak menimpa angka chat asli yang sudah terkumpul.

        await ctx.send(f"✅ Streak **{prodi}** berhasil disuntik menjadi **{jumlah} Hari** (Total chat asli tercatat: {real_total_messages}).")

        if jumlah in MILESTONES:
            ann_channel = self.bot.get_channel(STREAK_ANNOUNCEMENT_ID)
            if ann_channel:
                filename = f"{prodi.lower()}_{jumlah}.png"
                await self.kirim_kartu_pengumuman(ann_channel, prodi, jumlah, real_total_messages, filename)

    # ====================================================================
    # COMMAND: MATIKAN STREAK & KEMBALIKAN XP (SAMA SEPERTI SEBELUMNYA)
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
            SET lost_streak = $1, current_streak = 0, last_active_date = $2, total_messages = 0
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
                try:
                    await msg_pin.pin(reason="Pemberitahuan Kematian Streak")
                except:
                    pass


async def setup(bot):
    await bot.add_cog(StreakSystem(bot))