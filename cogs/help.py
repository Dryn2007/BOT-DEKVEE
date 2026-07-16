import discord
from discord.ext import commands
import asyncio

class HelpDashboardView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.locked_user = None
        self.timeout_task = None

    @discord.ui.select(
        placeholder="Pilih fitur yang ingin dilihat...",
        min_values=1, max_values=1,
        custom_id="help_dropdown_main",
        options=[
            discord.SelectOption(label="Leveling & Rank", description="Panduan sistem XP dan Role Hunter", emoji="🏆"),
            discord.SelectOption(label="Voice Log", description="Panduan statistik durasi Voice Channel", emoji="🔊"),
            discord.SelectOption(label="Admin Menu", description="Panduan command khusus Admin/Owner", emoji="👑")
        ]
    )
    async def select_menu(self, interaction: discord.Interaction, select: discord.ui.Select):
        # 1. CEK SISTEM ANTREAN / LOCKING
        if self.locked_user is not None and self.locked_user != interaction.user.id:
            await interaction.response.send_message(
                "⚠️ **Mohon tunggu!** Menu bantuan sedang digunakan oleh user lain. Tunggu gilirannya ya.", 
                ephemeral=True
            )
            return
            
        # 2. KUNCI DASHBOARD UNTUK USER INI & RESTART TIMER
        self.locked_user = interaction.user.id
        self.start_timer()
        
        # 3. TENTUKAN ISI PESAN
        val = select.values[0]
        embed = discord.Embed()
        
        if val == "Leveling & Rank":
            embed = discord.Embed(title="🏆 Panduan Leveling & Rank", description="Bot menggunakan sistem Hybrid! Kamu dapat XP dari Chat (2 XP) dan dari VC (1 XP / 2 Menit).", color=discord.Color.gold())
            embed.add_field(name="`!rank`", value="Melihat profil level, rank Hunter, dan progress bar XP kamu saat ini.", inline=False)
            embed.add_field(name="`!leaderboard`", value="Melihat 10 Hunter dengan level dan XP tertinggi di server.", inline=False)
            
        elif val == "Voice Log":
            embed = discord.Embed(title="🔊 Panduan Voice Log", description="Bot mencatat berapa lama kamu nongkrong di Voice Channel secara permanen.", color=discord.Color.blue())
            embed.add_field(name="`!vclog`", value="Menampilkan total statistik durasi seluruh member di VC untuk hari ini.", inline=False)
            embed.add_field(name="`!vclog history`", value="Menampilkan menu untuk melihat data durasi VC pada tanggal/hari sebelumnya.", inline=False)

        elif val == "Admin Menu":
            embed = discord.Embed(title="👑 Panduan Admin Menu", description="Command khusus yang hanya bisa diakses oleh petinggi server.", color=discord.Color.red())
            embed.add_field(name="`!clear`", value="**Akses:** Owner Bot\n**Fungsi:** Menghapus (purge) pesan di channel (default 5, max 100).", inline=False)
            embed.add_field(name="`!spawnstats`", value="**Akses:** Administrator\n**Fungsi:** Command rahasia untuk memaksa dashboard statistik muncul ulang.", inline=False)
            embed.add_field(name="`!testxp <jumlah>`", value="**Akses:** Administrator\n**Fungsi:** Mode testing untuk suntik XP ke akun sendiri secara instan.", inline=False)
            embed.add_field(name="`!spawnhelp`", value="**Akses:** Administrator\n**Fungsi:** Command rahasia untuk memunculkan ulang dashboard help secara paksa.", inline=False)

        # 4. UBAH STATUS UI (Tanpa menghapus item)
        select.placeholder = f"Sedang melihat: {val}"
        self.done_btn.disabled = False # Nyalakan tombol Selesai
            
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Selesai Membaca", style=discord.ButtonStyle.success, emoji="✅", 
        custom_id="help_done_btn", disabled=True # Awalnya dikunci / dimatikan
    )
    async def done_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.locked_user != interaction.user.id:
            await interaction.response.send_message("⚠️ Hanya user yang sedang membaca yang bisa ngeklik ini.", ephemeral=True)
            return
            
        await self.reset_dashboard(interaction)

    def start_timer(self):
        if self.timeout_task:
            self.timeout_task.cancel()
        self.timeout_task = self.cog.bot.loop.create_task(self.timer_logic())

    async def timer_logic(self):
        await asyncio.sleep(20.0)
        await self.reset_dashboard()

    async def reset_dashboard(self, interaction=None):
        self.locked_user = None
        if self.timeout_task:
            self.timeout_task.cancel()
            self.timeout_task = None
        
        # Kembalikan tampilan UI ke awal
        self.select_menu.placeholder = "Pilih fitur yang ingin dilihat..."
        self.done_btn.disabled = True # Matikan lagi tombolnya
        
        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )
        
        # Kirim pembaruan layar
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
        # Mendaftarkan custom_id ke memori bot agar selalu aktif
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
        if message.content.startswith("!spawnhelp"):
            return
            
        if message.channel.id == self.ROOM_HELP_ID:
            if message.author == self.bot.user:
                return
            try:
                await message.delete()
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(HelpMenu(bot))