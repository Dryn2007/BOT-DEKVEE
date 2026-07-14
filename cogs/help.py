import discord
from discord.ext import commands
import asyncio

class HelpDropdown(discord.ui.Select):
    def __init__(self):
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
            )
        ]
        super().__init__(placeholder="Pilih fitur yang ingin dilihat...", min_values=1, max_values=1, options=opsi)

    async def callback(self, interaction: discord.Interaction):
        # Tentukan isi pesan berdasarkan pilihan user
        if self.values[0] == "Leveling & Rank":
            embed = discord.Embed(
                title="🏆 Panduan Leveling & Rank",
                description="Bot menggunakan sistem Hybrid! Kamu dapat XP dari Chat (2 XP) dan dari VC (1 XP / 2 Menit).",
                color=discord.Color.gold()
            )
            embed.add_field(name="`!rank`", value="Melihat profil level, rank Hunter, dan progress bar XP kamu saat ini.", inline=False)
            embed.add_field(name="`!leaderboard`", value="Melihat 10 Hunter dengan level dan XP tertinggi di server.", inline=False)
            
        elif self.values[0] == "Voice Log":
            embed = discord.Embed(
                title="🔊 Panduan Voice Log",
                description="Bot mencatat berapa lama kamu nongkrong di Voice Channel secara permanen.",
                color=discord.Color.blue()
            )
            embed.add_field(name="`!vclog`", value="Menampilkan total statistik durasi seluruh member di VC untuk hari ini.", inline=False)
            embed.add_field(name="`!vclog history`", value="Menampilkan menu untuk melihat data durasi VC pada tanggal/hari sebelumnya.", inline=False)

        # Update pesan dengan embed yang baru
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self):
        # View akan timeout (hangus) setelah 10 detik
        super().__init__(timeout=10.0)
        self.message = None
        self.add_item(HelpDropdown())

    async def on_timeout(self):
        # Jika tidak ada aktivitas selama 10 detik, pesan help akan dihapus
        if self.message:
            try:
                await self.message.delete()
            except discord.NotFound:
                pass
            except Exception:
                pass


class HelpMenu(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Nonaktifkan command help bawaan Discord secara otomatis
        self.bot.remove_command('help')

    # --- FITUR BARU: AUTO-DELETE PESAN SELAIN !HELP ---
    @commands.Cog.listener()
    async def on_message(self, message):
        # Abaikan pesan dari bot itu sendiri
        if message.author.bot:
            return

        ROOM_HELP_ID = 1526498364932227092

        # Cek apakah pesan dikirim di room khusus command-center
        if message.channel.id == ROOM_HELP_ID:
            # Jika isi pesannya BUKAN "!help" (mengabaikan huruf besar/kecil dan spasi tambahan)
            if message.content.strip().lower() != "!help":
                try:
                    # Langsung hapus tanpa peringatan
                    await message.delete()
                except discord.Forbidden:
                    pass # Abaikan jika bot tidak punya izin hapus pesan
                except Exception:
                    pass

    @commands.command()
    async def help(self, ctx):
        # Pastikan command ini HANYA bisa dipakai di room khusus
        ROOM_HELP_ID = 1526498364932227092
        
        # Hapus chat "!help" dari user agar room selalu bersih
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        # Jika user mengetik di room yang salah, bot tidak merespon
        if ctx.channel.id != ROOM_HELP_ID:
            return

        # Buat dan kirim embed awal beserta Dropdown Menu
        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Pilih menu di bawah ini untuk melihat detail fitur dan cara menggunakannya!\n\n*(Pesan ini akan menghilang otomatis dalam 10 detik jika tidak digunakan)*",
            color=discord.Color.dark_theme()
        )
        
        view = HelpView()
        msg = await ctx.send(embed=embed, view=view)
        
        # Simpan referensi pesan ke dalam view agar bisa dihapus saat timeout
        view.message = msg

# FUNGSI SETUP
async def setup(bot):
    await bot.add_cog(HelpMenu(bot))