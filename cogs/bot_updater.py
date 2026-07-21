import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Button
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
ANNOUNCEMENT_CHANNEL_ID = 123456789012345678  # Ganti dengan ID Channel Pengumuman Publik
ADMIN_DASHBOARD_CHANNEL_ID = 987654321098765432  # Ganti dengan ID Room Khusus Admin (Dashboard)

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
        await send_announcement_embed(
            interaction, self.bot, 
            update_type="new_or_remove", 
            jenis=self.jenis_tindakan.value, 
            nama=self.nama_fitur.value, 
            deskripsi=self.deskripsi_fitur.value
        )

class UpdateFeatureModal(Modal, title="Update / Pembaruan Fitur"):
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
        await send_announcement_embed(
            interaction, self.bot, 
            update_type="update", 
            nama=self.nama_fitur.value, 
            sebelum=self.kondisi_sebelum.value, 
            sesudah=self.kondisi_sesudah.value
        )

# ==========================================
# UI VIEW (Dashboard Buttons Persisten)
# ==========================================

class DashboardView(View):
    def __init__(self, bot):
        # timeout=None SANGAT PENTING agar tombol tidak pernah mati
        super().__init__(timeout=None)
        self.bot = bot

    # custom_id ditambahkan agar Discord ingat tombol ini milik siapa setelah bot restart
    @discord.ui.button(label="✨ Fitur Baru / Hapus", style=discord.ButtonStyle.success, custom_id="persistent_btn_new")
    async def btn_new_feature(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(NewFeatureModal(self.bot))

    @discord.ui.button(label="🔄 Update Fitur", style=discord.ButtonStyle.primary, custom_id="persistent_btn_update")
    async def btn_update_feature(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(UpdateFeatureModal(self.bot))

# ==========================================
# FUNGSI PENGIRIM PENGUMUMAN
# ==========================================

async def send_announcement_embed(interaction, bot, update_type, nama, jenis=None, deskripsi=None, sebelum=None, sesudah=None):
    channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❌ Error: Channel pengumuman publik tidak ditemukan!", ephemeral=True)
        return

    tanggal_hari_ini = datetime.now().strftime("%d %B %Y")
    
    embed = discord.Embed(
        title=f"🚀 UPDATE BOT TERBARU - {tanggal_hari_ini}",
        color=discord.Color.brand_green() if update_type == "new_or_remove" else discord.Color.blue(),
        timestamp=datetime.now()
    )

    if update_type == "new_or_remove":
        icon = "✨" if "baru" in str(jenis).lower() else "🗑️"
        embed.description = f"Ada pembaruan sistem bot terbaru dari tim Developer!"
        embed.add_field(name=f"{icon} Status Fitur", value=f"**{jenis.upper()}**", inline=False)
        embed.add_field(name="🛠️ Nama Fitur", value=f"> {nama}", inline=False)
        embed.add_field(name="📝 Deskripsi", value=f"```\n{deskripsi}\n```", inline=False)
        
    elif update_type == "update":
        embed.description = f"Pembaruan dan optimasi sistem telah diterapkan!"
        embed.add_field(name="🔄 Nama Fitur", value=f"**{nama}**", inline=False)
        embed.add_field(name="❌ Sebelum", value=f"> {sebelum}", inline=False)
        embed.add_field(name="✅ Sesudah", value=f"> {sesudah}", inline=False)

    embed.set_footer(text=f"Diupdate oleh {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

    await channel.send(embed=embed)
    await interaction.response.send_message("✅ Pengumuman berhasil dikirim ke channel publik!", ephemeral=True)

# ==========================================
# COG CLASS DENGAN SISTEM AUTO-DEPLOY
# ==========================================

class BotUpdater(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # 1. Daftarkan view agar tombol persisten aktif
        self.bot.add_view(DashboardView(self.bot))
        
        # 2. Cari channel khusus Admin untuk dashboard
        channel = self.bot.get_channel(ADMIN_DASHBOARD_CHANNEL_ID)
        if not channel:
            print("Peringatan: Channel Dashboard Admin tidak ditemukan!")
            return

        # 3. Bersihkan pesan sebelumnya di room itu (biar tidak numpuk saat bot restart)
        try:
            await channel.purge(limit=10)
        except discord.Forbidden:
            print("Peringatan: Bot tidak punya permission 'Manage Messages' di channel admin.")
            return

        # 4. Kirim ulang dashboard interaktifnya
        embed = discord.Embed(
            title="⚙️ Dashboard Pengumuman Bot",
            description=(
                "Silakan pilih jenis pengumuman yang ingin dikirimkan ke publik.\n\n"
                "**Panduan:**\n"
                "`✨ Fitur Baru / Hapus` : Gunakan jika ada sistem yang baru ditambahkan atau dihapus.\n"
                "`🔄 Update Fitur` : Gunakan jika ada perubahan sistem (Perbandingan Sebelum & Sesudah)."
            ),
            color=discord.Color.dark_grey()
        )
        if self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        
        await channel.send(embed=embed, view=DashboardView(self.bot))

async def setup(bot):
    await bot.add_cog(BotUpdater(bot))