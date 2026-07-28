import discord
from discord.ext import commands
from discord.ui import View, Button
import os

# ==========================================
# UI PANEL SOUNDBOARD (TEMPORARY)
# ==========================================
class SoundboardPanel(View):
    def __init__(self, cog):
        # Set timeout menjadi 5 detik
        super().__init__(timeout=5.0)
        self.cog = cog
        self.message = None # Tempat menyimpan object pesan untuk dihapus nanti

    # Fungsi yang akan otomatis berjalan jika 5 detik tidak ada interaksi
    async def on_timeout(self):
        if self.message:
            try:
                await self.message.delete()
            except:
                pass

    # Fungsi utama untuk memutar suara dan memotong koin
    async def play_sound(self, interaction: discord.Interaction, sound_name: str):
        # 1. Cek apakah user sedang berada di Voice Channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Masuk room voice dulu ya buat muter soundboard!", ephemeral=True)
            return

        # 2. Cek apakah file MP3-nya ada di folder 'sounds'
        file_path = f"sounds/{sound_name}.mp3"
        if not os.path.exists(file_path):
            await interaction.response.send_message(f"❌ File suara `{sound_name}.mp3` belum ditambahkan oleh Admin ke folder sounds.", ephemeral=True)
            return

        # 3. Eksekusi pemotongan 2 koin
        has_enough = await self.cog.deduct_coins(interaction.user.id, 2)
        if not has_enough:
            await interaction.response.send_message("🪙 Saldo koin kamu tidak cukup! Butuh **2 Koin** untuk fitur ini.", ephemeral=True)
            return

        # 4. Beri respon sukses secara private (ephemeral)
        await interaction.response.send_message(f"🔊 Memutar soundboard `{sound_name.upper()}` (-2 Koin)", ephemeral=True)

        # 5. Masukkan bot ke Voice Channel (DENGAN PENJEBAK ERROR)
        try:
            voice_channel = interaction.user.voice.channel
            vc = discord.utils.get(self.cog.bot.voice_clients, guild=interaction.guild)
            
            if not vc:
                # Tambahkan timeout agar bot tidak nge-hang jika jaringan Heroku lambat
                vc = await voice_channel.connect(timeout=10.0)
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)

            # 6. Hentikan suara sebelumnya (jika ada) lalu putar yang baru
            if vc.is_playing():
                vc.stop()

            source = discord.FFmpegPCMAudio(file_path)
            vc.play(source)

        except Exception as e:
            # JIKA GAGAL, ERRORNYA AKAN DIKIRIM LANGSUNG KE DISCORD!
            await interaction.followup.send(f"⚠️ **Sistem mendeteksi Error:**\n```py\n{e}\n```", ephemeral=True)
            print(f"ERROR SOUNDBOARD: {e}", flush=True)
    # --- DERETAN 8 TOMBOL SOUNDBOARD ---
    
    # Baris 1 (row=0)
    @discord.ui.button(label="Kaget", emoji="🤯", style=discord.ButtonStyle.secondary, row=0)
    async def btn_1(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "kaget")

    @discord.ui.button(label="Victory", emoji="🐦", style=discord.ButtonStyle.secondary, row=0)
    async def btn_2(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "victory")

    @discord.ui.button(label="Siren", emoji="🚨", style=discord.ButtonStyle.secondary, row=0)
    async def btn_3(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "siren")

    # Baris 2 (row=1)
    @discord.ui.button(label="FAHH", emoji="😆", style=discord.ButtonStyle.secondary, row=1)
    async def btn_4(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "fahh")

    @discord.ui.button(label="ketawa", emoji="🥰", style=discord.ButtonStyle.secondary, row=1)
    async def btn_5(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "ketawa")

    @discord.ui.button(label="WELKAM", emoji="👋", style=discord.ButtonStyle.secondary, row=1)
    async def btn_6(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "welkam")

    # Baris 3 (row=2)
    @discord.ui.button(label="Pou", emoji="😜", style=discord.ButtonStyle.secondary, row=2)
    async def btn_7(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "pou")

    @discord.ui.button(label="IWAK TEMPE", emoji="🐡", style=discord.ButtonStyle.secondary, row=2)
    async def btn_8(self, interaction: discord.Interaction, button: Button):
        await self.play_sound(interaction, "iwak_tempe")


# ==========================================
# COG SOUNDBOARD
# ==========================================
class Soundboard(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool

    async def deduct_coins(self, user_id, amount):
        data = await self.pool.fetchrow("SELECT coins FROM levels WHERE user_id = $1", user_id)
        if data and data['coins'] >= amount:
            await self.pool.execute("UPDATE levels SET coins = coins - $1 WHERE user_id = $2", amount, user_id)
            await self.pool.execute(
                "INSERT INTO coin_logs (user_id, amount, description) VALUES ($1, $2, $3)", 
                user_id, -amount, "Memutar Efek Soundboard"
            )
            return True
        return False

    @commands.command(name="panelsb")
    # HAS_PERMISSIONS DIHAPUS AGAR SEMUA MEMBER BISA MENGGUNAKANNYA
    async def spawn_sb_panel(self, ctx):
        """Memunculkan Panel UI Soundboard (Bisa untuk Semua Member)"""
        # Hapus chat command (!panelsb) dari member secara instan
        try: await ctx.message.delete()
        except: pass
        
        embed = discord.Embed(
            title="🎛️ DekVee Soundboard Panel",
            description=(
                "Klik tombol di bawah untuk memutar efek suara ke dalam Voice Channel!\n\n"
                "🪙 **Biaya:** `2 Koin` per klik\n"
                "⏳ *Panel ini otomatis hilang jika tidak digunakan selama 5 detik.*\n"
                "🗣️ **Syarat:** Kamu wajib berada di dalam Voice Channel terlebih dahulu."
            ),
            color=discord.Color.brand_green()
        )
        embed.set_footer(text="Panel interaktif oleh DekVee", icon_url=self.bot.user.display_avatar.url)
        
        # Kirim panel dan simpan objek pesannya ke dalam View untuk dihapus saat timeout
        view = SoundboardPanel(self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

async def setup(bot):
    await bot.add_cog(Soundboard(bot, bot.pool))