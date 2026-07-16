import discord
from discord.ext import commands
import asyncio
import traceback

# ====================================================================
# 1. TAMPILAN MENU PRIVATE (HANYA DILIHAT OLEH USER YANG MASUK GRID)
# ====================================================================
class PrivateHelpDropdown(discord.ui.Select):
    def __init__(self):
        opsi = [
            discord.SelectOption(label="Leveling & Rank", description="Panduan sistem XP dan Role Hunter", emoji="🏆"),
            discord.SelectOption(label="Voice Log", description="Panduan statistik durasi Voice Channel", emoji="🔊"),
            discord.SelectOption(label="Admin Menu", description="Panduan command khusus Admin/Owner", emoji="👑")
        ]
        super().__init__(placeholder="Pilih fitur yang ingin dilihat...", min_values=1, max_values=1, options=opsi)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        embed = discord.Embed()

        if val == "Leveling & Rank":
            embed = discord.Embed(
                title="🏆 Panduan Leveling & Rank",
                description="Bot menggunakan sistem Hybrid! Kamu dapat XP dari Chat (2 XP) dan dari VC (1 XP / 2 Menit).",
                color=discord.Color.gold()
            )
            embed.add_field(name="`!rank`", value="Melihat profil level, rank Hunter, dan progress bar XP kamu saat ini.", inline=False)
            embed.add_field(name="`!leaderboard`", value="Melihat 10 Hunter dengan level dan XP tertinggi di server.", inline=False)

        elif val == "Voice Log":
            embed = discord.Embed(
                title="🔊 Panduan Voice Log",
                description="Bot mencatat berapa lama kamu nongkrong di Voice Channel secara permanen.",
                color=discord.Color.blue()
            )
            embed.add_field(name="`!vclog`", value="Menampilkan total statistik durasi seluruh member di VC untuk hari ini.", inline=False)
            embed.add_field(name="`!vclog history`", value="Menampilkan menu untuk melihat data durasi VC pada tanggal/hari sebelumnya.", inline=False)

        elif val == "Admin Menu":
            embed = discord.Embed(
                title="👑 Panduan Admin Menu",
                description="Command khusus yang hanya bisa diakses oleh petinggi server.",
                color=discord.Color.red()
            )
            embed.add_field(name="`!clear`", value="**Akses:** Owner Bot\n**Fungsi:** Menghapus (purge) pesan di channel (default 5, max 100).", inline=False)
            embed.add_field(name="`!spawnstats`", value="**Akses:** Administrator\n**Fungsi:** Command rahasia untuk memaksa dashboard statistik muncul ulang.", inline=False)
            embed.add_field(name="`!testxp <jumlah>`", value="**Akses:** Administrator\n**Fungsi:** Mode testing untuk suntik XP ke akun sendiri secara instan.", inline=False)
            embed.add_field(name="`!spawnhelp`", value="**Akses:** Administrator\n**Fungsi:** Command rahasia untuk memunculkan ulang dashboard help secara paksa.", inline=False)

        self.placeholder = f"Sedang melihat: {val}"
        await interaction.response.edit_message(embed=embed, view=self.view)


class PrivateDoneButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Tutup & Selesai", style=discord.ButtonStyle.danger, emoji="✖️")

    async def callback(self, interaction: discord.Interaction):
        # 1. Buka kembali gembok Grid di luar
        await self.view.main_view.unlock_grid(self.view.grid_index)
        
        # 2. Hapus menu private agar bersih
        try:
            await interaction.response.edit_message(content="✅ **Sesi selesai.** Grid telah dikembalikan dan siap digunakan orang lain.", embed=None, view=None)
        except Exception:
            pass


class PrivateHelpView(discord.ui.View):
    def __init__(self, main_view, grid_index):
        super().__init__(timeout=60.0) # Waktu baca 60 detik
        self.main_view = main_view
        self.grid_index = grid_index
        self.add_item(PrivateHelpDropdown())
        self.add_item(PrivateDoneButton())

    async def on_timeout(self):
        # Kalau user ketiduran / AFK 60 detik, paksa buka gembok Grid
        await self.main_view.unlock_grid(self.grid_index)


# ====================================================================
# 2. TAMPILAN UTAMA (DASHBOARD 4 GRID)
# ====================================================================
class MainDashboardView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        
        # Menyimpan data siapa yang menempati 4 slot grid
        self.grid_status = [None, None, None, None] 
        self.grid_tasks = [None, None, None, None]

        # Membuat 4 Tombol Grid (2 Baris)
        for i in range(4):
            btn = discord.ui.Button(
                label=f"Grid {i+1} (Tersedia)",
                style=discord.ButtonStyle.success,
                custom_id=f"help_grid_{i}",
                row=i // 2 # Ini trik bikin bentuk kotak 2x2
            )
            btn.callback = self.make_callback(i)
            self.add_item(btn)

    def make_callback(self, index):
        async def callback(interaction: discord.Interaction):
            # Cek apakah Grid ini sudah diisi orang
            if self.grid_status[index] is not None:
                await interaction.response.send_message(
                    "⛔ **Grid ini sedang dipakai!** Silakan pilih Grid lain yang warnanya hijau.", 
                    ephemeral=True
                )
                return

            # 1. KUNCI GRID-NYA
            self.grid_status[index] = interaction.user.id
            button = self.children[index]
            
            # Singkat nama agar tombol tidak kepanjangan (Maks 10 karakter)
            nama = interaction.user.display_name[:10]
            button.label = f"🔒 Dipakai {nama}"
            button.style = discord.ButtonStyle.secondary
            button.disabled = True

            # 2. UPDATE TAMPILAN TOMBOL DI LUAR
            await interaction.response.edit_message(view=self)

            # 3. KIRIM MENU PRIVATE KE USER YANG NGEKLIK
            private_view = PrivateHelpView(self, index)
            embed_intro = discord.Embed(
                title=f"🚪 Masuk Grid {index+1}",
                description="Silakan pilih panduan dari menu di bawah.\n\n*Catatan: Hanya kamu yang bisa melihat pesan ini. Waktu bacamu 60 detik. Klik **Tutup & Selesai** jika sudah beres agar grid ini bisa dipakai orang lain.*",
                color=discord.Color.brand_green()
            )
            await interaction.followup.send(embed=embed_intro, view=private_view, ephemeral=True)

            # 4. MULAI TIMER AUTO-RESET
            if self.grid_tasks[index]:
                self.grid_tasks[index].cancel()
            self.grid_tasks[index] = self.cog.bot.loop.create_task(self.timer_logic(index))

        return callback

    async def timer_logic(self, index):
        try:
            await asyncio.sleep(60.0)
            await self.unlock_grid(index)
        except asyncio.CancelledError:
            pass

    async def unlock_grid(self, index):
        # Jika Grid memang sudah kosong, hiraukan
        if self.grid_status[index] is None:
            return

        # Buka kembali Grid menjadi Hijau
        self.grid_status[index] = None
        button = self.children[index]
        button.label = f"Grid {index+1} (Tersedia)"
        button.style = discord.ButtonStyle.success
        button.disabled = False

        if self.grid_tasks[index]:
            self.grid_tasks[index].cancel()
            self.grid_tasks[index] = None

        # Perbarui tampilan dashboard luar
        if self.cog.dashboard_message:
            try:
                await self.cog.dashboard_message.edit(view=self)
            except Exception as e:
                print(f"[unlock_grid] Gagal update dashboard: {e!r}")


# ====================================================================
# 3. COG UTAMA
# ====================================================================
class HelpMenu(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.remove_command('help')
        self.ROOM_HELP_ID = 1526498364932227092
        self.dashboard_message = None
        self.is_spawned = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_spawned:
            self.is_spawned = True
            await asyncio.sleep(3)
            await self.spawn_dashboard()

    async def spawn_dashboard(self):
        await self.bot.wait_until_ready()

        channel = self.bot.get_channel(self.ROOM_HELP_ID)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(self.ROOM_HELP_ID)
            except Exception:
                return

        try:
            await channel.purge(limit=100)
        except Exception as e:
            traceback.print_exc()

        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee (Sistem 4 Grid)",
            description="Selamat datang di Pusat Bantuan!\n\nUntuk menghindari berebut menu, silakan klik salah satu **Grid Hijau** di bawah ini untuk membuka menu panduan khusus untukmu.\n\n*(Jika Grid abu-abu dan tergembok, artinya sedang dibaca user lain)*",
            color=discord.Color.dark_theme()
        )

        view = MainDashboardView(self)
        self.dashboard_message = await channel.send(embed=embed, view=view)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def spawnhelp(self, ctx):
        """Memunculkan dashboard help secara paksa"""
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await self.spawn_dashboard()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if message.content.startswith("!spawnhelp"):
            return

        if message.channel.id == self.ROOM_HELP_ID:
            try:
                await message.delete()
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(HelpMenu(bot))