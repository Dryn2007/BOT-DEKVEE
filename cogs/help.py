import discord
from discord.ext import commands
import asyncio

class HelpDashboardView(discord.ui.View):
    def __init__(self, cog):
        # timeout=None agar dashboard menetap selamanya
        super().__init__(timeout=None)
        self.cog = cog
        self.locked_user = None
        self.timeout_task = None
        
        # Bangun UI untuk pertama kali
        self.setup_ui(is_main_menu=True)

    def setup_ui(self, is_main_menu=True, selected_val=None):
        """Fungsi pembangun UI yang aman dari bug 'Interaction Failed'"""
        self.clear_items()
        
        opsi = [
            discord.SelectOption(label="Leveling & Rank", description="Panduan sistem XP dan Role Hunter", emoji="🏆"),
            discord.SelectOption(label="Voice Log", description="Panduan statistik durasi Voice Channel", emoji="🔊"),
            discord.SelectOption(label="Admin Menu", description="Panduan command khusus Admin/Owner", emoji="👑")
        ]
        
        placeholder = "Pilih fitur yang ingin dilihat..."
        if not is_main_menu and selected_val:
            placeholder = f"Sedang melihat: {selected_val}"
            
        # 1. Buat Dropdown Utama
        self.dropdown = discord.ui.Select(
            placeholder=placeholder,
            min_values=1, max_values=1, options=opsi,
            custom_id="help_dropdown_main" # Wajib pakai custom_id agar permanen
        )
        self.dropdown.callback = self.dropdown_callback
        self.add_item(self.dropdown)
        
        # 2. Buat Tombol Selesai (Hanya muncul jika sedang membuka menu)
        if not is_main_menu:
            self.done_button = discord.ui.Button(
                label="Selesai Membaca", style=discord.ButtonStyle.success, emoji="✅",
                custom_id="help_done_btn" # Wajib pakai custom_id agar permanen
            )
            self.done_button.callback = self.done_callback
            self.add_item(self.done_button)

    async def dropdown_callback(self, interaction: discord.Interaction):
        # CEK SISTEM ANTREAN / LOCKING
        if self.locked_user is not None and self.locked_user != interaction.user.id:
            await interaction.response.send_message(
                "⚠️ **Mohon tunggu!** Menu bantuan sedang digunakan oleh user lain. Sistem akan reset otomatis dalam 20 detik jika tidak ada aktivitas.", 
                ephemeral=True
            )
            return
            
        # KUNCI DASHBOARD UNTUK USER INI & RESTART TIMER
        self.locked_user = interaction.user.id
        self.start_timer()
        
        # TENTUKAN ISI PESAN
        val = self.dropdown.values[0]
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

        # Bangun ulang UI untuk memunculkan tombol Selesai
        self.setup_ui(is_main_menu=False, selected_val=val)
        await interaction.response.edit_message(embed=embed, view=self)

    async def done_callback(self, interaction: discord.Interaction):
        if self.locked_user != interaction.user.id:
            await interaction.response.send_message("⚠️ Hanya user yang sedang membaca yang bisa menyelesaikan sesi ini.", ephemeral=True)
            return
            
        # Panggil fungsi reset dengan menyertakan interaction
        await self.reset_dashboard(interaction)

    def start_timer(self):
        if self.timeout_task:
            self.timeout_task.cancel()
        # Buat task baru yang berjalan di background
        self.timeout_task = self.cog.bot.loop.create_task(self.timer_logic())

    async def timer_logic(self):
        await asyncio.sleep(20.0)
        await self.reset_dashboard()

    async def reset_dashboard(self, interaction=None):
        self.locked_user = None
        if self.timeout_task:
            self.timeout_task.cancel()
            self.timeout_task = None
        
        # Bangun ulang UI kembali ke Main Menu (tanpa tombol Selesai)
        self.setup_ui(is_main_menu=True)
        
        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )
        
        # Update pesan melalui interaction (jika diklik user) atau edit manual (jika kena timer)
        if interaction:
            try:
                await interaction.response.edit_message(embed=embed, view=self)
            except Exception:
                pass
        elif self.cog.dashboard_message:
            try:
                await self.cog.dashboard_message.edit(embed=embed, view=self)
            except Exception:
                pass


class HelpMenu(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.remove_command('help')
        self.ROOM_HELP_ID = 1526498364932227092
        self.dashboard_message = None 
        self.is_spawned = False 

    @commands.Cog.listener()
    async def on_ready(self):
        # Daftarkan View ini ke memori internal agar anti-gagal meskipun bot ter-restart
        self.bot.add_view(HelpDashboardView(self))
        
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

        await channel.purge(limit=100)

        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )
        
        view = HelpDashboardView(self)
        self.dashboard_message = await channel.send(embed=embed, view=view)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def spawnhelp(self, ctx):
        """Memunculkan dashboard help secara paksa"""
        try:
            await ctx.message.delete()
        except:
            pass
        await self.spawn_dashboard()

    @commands.Cog.listener()
    async def on_message(self, message):
        # Abaikan command !spawnhelp
        if message.content.startswith("!spawnhelp"):
            return
            
        if message.channel.id == self.ROOM_HELP_ID:
            # Pastikan bot tidak menghapus dashboard-nya sendiri
            if message.author == self.bot.user:
                return
                
            try:
                await message.delete()
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(HelpMenu(bot))