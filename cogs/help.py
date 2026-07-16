import discord
from discord.ext import commands
import asyncio

class HelpDropdown(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        opsi = [
            discord.SelectOption(
                label="Leveling & Rank", 
                description="Panduan sistem XP dan Role Hunter", 
                emoji="🏆"
            ),
            discord.SelectOption(
                label="Voice Log", 
                description="Panduan statistik durasi Voice Channel", 
                emoji="🔊"
            ),
            discord.SelectOption(
                label="Admin Menu", 
                description="Panduan command khusus Admin/Owner", 
                emoji="👑"
            )
        ]
        super().__init__(placeholder="Pilih fitur yang ingin dilihat...", min_values=1, max_values=1, options=opsi)

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        
        # 1. CEK SISTEM ANTREAN / LOCKING
        if view.locked_user is not None and view.locked_user != interaction.user.id:
            await interaction.response.send_message(
                "⚠️ **Mohon tunggu!** Menu bantuan sedang digunakan oleh user lain. Sistem akan reset otomatis dalam 20 detik jika tidak ada aktivitas.", 
                ephemeral=True
            )
            return
            
        # 2. KUNCI DASHBOARD UNTUK USER INI & RESTART TIMER
        view.locked_user = interaction.user.id
        await view.start_timer()
        
        # 3. TENTUKAN ISI PESAN
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

        self.placeholder = f"Sedang melihat: {val}"

        # 4. TAMBAHKAN TOMBOL "SELESAI" JIKA BELUM ADA
        if view.done_button not in view.children:
            view.add_item(view.done_button)
            
        await interaction.response.edit_message(embed=embed, view=view)


class DoneButton(discord.ui.Button):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(label="Selesai Membaca", style=discord.ButtonStyle.success, emoji="✅")
        
    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        if view.locked_user != interaction.user.id:
            await interaction.response.send_message("⚠️ Hanya user yang sedang membaca yang bisa menyelesaikan sesi ini.", ephemeral=True)
            return
            
        await interaction.response.defer()
        await view.reset_dashboard()


class HelpDashboardView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.locked_user = None
        self.timeout_task = None
        self.dropdown = HelpDropdown(self)
        self.done_button = DoneButton(self)
        self.add_item(self.dropdown)

    async def start_timer(self):
        if self.timeout_task:
            self.timeout_task.cancel()
        self.timeout_task = asyncio.create_task(self.timer_logic())

    async def timer_logic(self):
        await asyncio.sleep(20.0)
        await self.reset_dashboard()

    async def reset_dashboard(self):
        self.locked_user = None
        if self.timeout_task:
            self.timeout_task.cancel()
            self.timeout_task = None
        
        self.dropdown.placeholder = "Pilih fitur yang ingin dilihat..."
        if self.done_button in self.children:
            self.remove_item(self.done_button)
        
        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )
        
        if self.cog.dashboard_message:
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
        self.is_spawned = False # Penanda agar tidak dobel

    # MENGGUNAKAN ON_READY SEBAGAI GANTI COG_LOAD
    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_spawned:
            self.is_spawned = True
            await self.spawn_dashboard()

    async def spawn_dashboard(self):
        channel = self.bot.get_channel(self.ROOM_HELP_ID)
        if not channel:
            return

        await channel.purge(limit=100)

        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )
        
        view = HelpDashboardView(self)
        self.dashboard_message = await channel.send(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id == self.ROOM_HELP_ID:
            if self.dashboard_message and message.id == self.dashboard_message.id:
                return
            try:
                await message.delete()
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(HelpMenu(bot))