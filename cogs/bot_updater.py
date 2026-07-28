import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Button
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
ANNOUNCEMENT_CHANNEL_ID = 1526219303714820186  # Ganti dengan ID Channel Pengumuman Publik
ADMIN_DASHBOARD_CHANNEL_ID = 1529106931577520189  # Ganti dengan ID Room Khusus Admin (Dashboard)

# ==========================================
# UI MODALS (Form Isian)
# ==========================================

class NewFeatureModal(Modal, title="Fitur Baru / Hapus Fitur"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    jenis_tindakan = TextInput(
        label="Status (Ketik: Baru / Dihapus)", 
        placeholder="Contoh: Fitur Baru", 
        max_length=20
    )
    nama_fitur = TextInput(
        label="Nama Fitur", 
        placeholder="Contoh: Sistem Leveling"
    )
    deskripsi_fitur = TextInput(
        label="Deskripsi / Kegunaan", 
        style=discord.TextStyle.paragraph, 
        placeholder="Jelaskan secara detail mengenai fitur ini..."
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await send_announcement_embed(
            interaction, self.bot, 
            update_type="new_or_remove", 
            jenis=self.jenis_tindakan.value, 
            nama=self.nama_fitur.value, 
            deskripsi=self.deskripsi_fitur.value
        )


class UpdateFeatureModal(Modal, title="Update / Pembaruan 1 Fitur"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    nama_fitur = TextInput(
        label="Nama Fitur yang Diupdate", 
        placeholder="Contoh: Economy System"
    )
    kondisi_sebelum = TextInput(
        label="Sebelum Diperbarui", 
        style=discord.TextStyle.paragraph, 
        placeholder="Bagaimana sistem bekerja sebelumnya..."
    )
    kondisi_sesudah = TextInput(
        label="Sesudah Diperbarui", 
        style=discord.TextStyle.paragraph, 
        placeholder="Perubahan apa yang terjadi sekarang..."
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await send_announcement_embed(
            interaction, self.bot, 
            update_type="update", 
            nama=self.nama_fitur.value, 
            sebelum=self.kondisi_sebelum.value, 
            sesudah=self.kondisi_sesudah.value
        )

# --- MODAL BARU: MULTI UPDATE / PATCH NOTES ---
class MultiUpdateModal(Modal, title="Patch Notes (Multi Update)"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    judul_update = TextInput(
        label="Judul Rangkuman Update", 
        placeholder="Contoh: Pembaruan Besar-besaran v2.0!",
        max_length=100
    )
    isi_update = TextInput(
        label="Isi Pembaruan (Gunakan bullet points)", 
        style=discord.TextStyle.paragraph, 
        placeholder="- Koin skrng bisa dipakai untuk Music\n- Dashboard ditambah kategori Sultan\n- Anti-spam ditambahkan",
        max_length=3000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await send_announcement_embed(
            interaction, self.bot, 
            update_type="multi", 
            nama=self.judul_update.value, 
            deskripsi=self.isi_update.value
        )


# ==========================================
# UI VIEW (Dashboard Buttons Persisten)
# ==========================================

class DashboardView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="✨ Fitur Baru / Hapus", style=discord.ButtonStyle.success, custom_id="persistent_btn_new")
    async def btn_new_feature(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(NewFeatureModal(self.bot))

    @discord.ui.button(label="🔄 Update 1 Fitur", style=discord.ButtonStyle.primary, custom_id="persistent_btn_update")
    async def btn_update_feature(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(UpdateFeatureModal(self.bot))

    @discord.ui.button(label="📑 Patch Notes (Multi)", style=discord.ButtonStyle.secondary, custom_id="persistent_btn_multi")
    async def btn_multi_update(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(MultiUpdateModal(self.bot))

    @discord.ui.button(label="⚠️ Toggle Maintenance", style=discord.ButtonStyle.danger, custom_id="persistent_btn_maintenance")
    async def btn_maintenance(self, interaction: discord.Interaction, button: Button):
        self.bot.maintenance_mode = not getattr(self.bot, 'maintenance_mode', False)

        status_text = "Dinyalakan 🔴" if self.bot.maintenance_mode else "Dimatikan 🟢"
        await interaction.response.send_message(f"✅ Pengumuman Maintenance **{status_text}** berhasil dikirim ke publik.", ephemeral=True)

        channel = self.bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="⚠️ PENGUMUMAN SISTEM",
                description=(
                    "Bot sedang dalam masa perbaikan (Maintenance) oleh tim Developer. "
                    "Sebagian fitur mungkin sedang disesuaikan dan berjalan kurang stabil." 
                    if self.bot.maintenance_mode else 
                    "✅ Maintenance selesai! Semua sistem bot sudah kembali normal."
                ),
                color=discord.Color.red() if self.bot.maintenance_mode else discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.set_footer(text=f"Diupdate oleh {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException) as e:
                await interaction.followup.send(f"⚠️ Gagal mengirim ke channel publik: {e}", ephemeral=True)


# ==========================================
# FUNGSI PENGIRIM PENGUMUMAN
# ==========================================

async def send_announcement_embed(interaction, bot, update_type, nama, jenis=None, deskripsi=None, sebelum=None, sesudah=None):
    channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("❌ Error: Channel pengumuman publik tidak ditemukan!", ephemeral=True)
        return

    tanggal_hari_ini = datetime.now().strftime("%d %B %Y")

    color_map = {
        "new_or_remove": discord.Color.brand_green(),
        "update": discord.Color.blue(),
        "multi": discord.Color.purple()
    }

    embed = discord.Embed(
        title=f"🚀 UPDATE BOT TERBARU - {tanggal_hari_ini}",
        color=color_map.get(update_type, discord.Color.gold()),
        timestamp=datetime.now()
    )

    if update_type == "new_or_remove":
        icon = "✨" if "baru" in str(jenis).lower() else "🗑️"
        # Memindahkan deskripsi ke area yang muat 4096 karakter agar tidak error
        embed.description = f"Ada pembaruan sistem bot terbaru dari tim Developer!\n\n**📝 Deskripsi:**\n```\n{deskripsi}\n```"
        embed.add_field(name=f"{icon} Status Fitur", value=f"**{jenis.upper()}**", inline=False)
        embed.add_field(name="🛠️ Nama Fitur", value=f"> {nama}", inline=False)

    elif update_type == "update":
        embed.description = f"Pembaruan dan optimasi sistem telah diterapkan!"
        embed.add_field(name="🔄 Nama Fitur", value=f"**{nama}**", inline=False)
        # Mengamankan batas field 1024 karakter
        embed.add_field(name="❌ Sebelum", value=f"> {sebelum}"[:1020], inline=False)
        embed.add_field(name="✅ Sesudah", value=f"> {sesudah}"[:1020], inline=False)

    elif update_type == "multi":
        # Menggunakan embed.description untuk Patch Notes agar teks di atas 1024 karakter tetap lolos
        embed.description = f"Ada banyak peningkatan dan perbaikan (Patch Notes) yang baru saja diterapkan!\n\n**📌 {nama}**\n\n**Daftar Perubahan:**\n{deskripsi}"

    embed.set_footer(text=f"Diupdate oleh {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    try:
        await channel.send(embed=embed)
        await interaction.followup.send("✅ Pengumuman berhasil dikirim ke channel publik!", ephemeral=True)
    except (discord.Forbidden, discord.HTTPException) as e:
        await interaction.followup.send(f"❌ Gagal mengirim pengumuman ke channel publik: {e}", ephemeral=True)


# ==========================================
# COG CLASS
# ==========================================

class BotUpdater(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if not hasattr(self.bot, 'maintenance_mode'):
            self.bot.maintenance_mode = False

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(DashboardView(self.bot))

        channel = self.bot.get_channel(ADMIN_DASHBOARD_CHANNEL_ID)
        if not channel:
            print("Peringatan: Channel Dashboard Admin tidak ditemukan!")
            return

        try:
            await channel.purge(limit=10)
        except discord.Forbidden:
            print("Peringatan: Bot tidak punya permission 'Manage Messages' di channel admin.")
            return

        embed = discord.Embed(
            title="⚙️ Dashboard Pengumuman Bot",
            description=(
                "Silakan pilih jenis pengumuman yang ingin dikirimkan ke publik.\n\n"
                "**Panduan:**\n"
                "`✨ Fitur Baru / Hapus` : Gunakan untuk 1 fitur yang baru rilis/dihapus.\n"
                "`🔄 Update 1 Fitur` : Gunakan untuk membandingkan (Sebelum/Sesudah) 1 fitur.\n"
                "`📑 Patch Notes (Multi)` : **[BARU]** Gunakan untuk merangkum banyak perubahan sekaligus (Bullet points).\n"
                "`⚠️ Toggle Maintenance` : Kirim pengumuman bahwa bot sedang/selesai maintenance."
            ),
            color=discord.Color.dark_grey()
        )
        if self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        await channel.send(embed=embed, view=DashboardView(self.bot))


async def setup(bot):
    await bot.add_cog(BotUpdater(bot))