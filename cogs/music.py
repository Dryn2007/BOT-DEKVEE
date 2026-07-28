import discord
from discord.ext import commands
import asyncio

class MusicUI(discord.ui.View):
    def __init__(self, bot, ctx):
        super().__init__(timeout=None)
        self.bot = bot
        self.ctx = ctx

    @discord.ui.button(label="⏮️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Logika previous song
        await interaction.response.send_message("Memutar lagu sebelumnya...", ephemeral=True)

    @discord.ui.button(label="⏸️ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Logika pause/resume
        await interaction.response.send_message("Musik dijeda/dilanjutkan.", ephemeral=True)

    @discord.ui.button(label="⏭️ Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Logika skip song
        await interaction.response.send_message("Menge-skip lagu...", ephemeral=True)

    @discord.ui.button(label="📋 Antrean", style=discord.ButtonStyle.success)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Logika melihat daftar antrean
        await interaction.response.send_message("Fitur drag & drop tidak didukung API, berikut list antrean: \n1. Lagu A\n2. Lagu B", ephemeral=True)

class Music(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.music_sessions = {} # Menyimpan data siapa yang mutar lagu di mana

    async def deduct_coins(self, user_id, amount):
        # Mengecek dan memotong koin
        data = await self.pool.fetchrow("SELECT coins FROM levels WHERE user_id = $1", user_id)
        if data and data['coins'] >= amount:
            await self.pool.execute("UPDATE levels SET coins = coins - $1 WHERE user_id = $2", amount, user_id)
            return True
        return False

    @commands.command(name="music")
    async def play_music(self, ctx, url: str):
        # Hapus pesan command dari user jika ada
        try: await ctx.message.delete()
        except: pass

        # Cek apakah user ada di voice channel
        if not ctx.author.voice or not ctx.author.voice.channel:
            msg = await ctx.send(f"{ctx.author.mention}, kamu harus masuk room voice dulu ya!", delete_after=5.0)
            return

        # Cek tipe link (Lagu atau Playlist) dan potong koin
        is_playlist = "playlist" in url.lower() or "list=" in url.lower()
        cost = 10 if is_playlist else 3
        
        has_enough_coins = await self.deduct_coins(ctx.author.id, cost)
        if not has_enough_coins:
            await ctx.send(f"Koin kamu tidak cukup! Butuh {cost} koin.", delete_after=5.0)
            return

        # Daftarkan session siapa yang memutar di VC mana
        self.music_sessions[ctx.guild.id] = {
            'requester_id': ctx.author.id,
            'voice_channel': ctx.author.voice.channel.id
        }

        # Embed UI Player
        embed = discord.Embed(
            title="🎵 Now Playing (DekVee Music)",
            description=f"Memutar musik dari link: {url}",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url="https://link-ke-foto-koin-atau-cover-musik.png") # Bisa diganti dengan cover album dari yt-dlp
        embed.set_footer(text=f"Requested by {ctx.author.name} | Sisa Koin terpotong {cost}", icon_url=ctx.author.display_avatar.url)

        # Muncul di channel tempat command diketik
        view = MusicUI(self.bot, ctx)
        await ctx.send(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        
        # Aturan: Jika orang yang memutar musik keluar dari Voice, musik berhenti
        guild_session = self.music_sessions.get(member.guild.id)
        if guild_session:
            # Cek jika member yang keluar adalah requester dan channel yang ditinggalkan sama
            if member.id == guild_session['requester_id'] and before.channel is not None and after.channel is None:
                # Logika memberhentikan lagu bot di sini
                voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
                if voice_client and voice_client.is_connected():
                    await voice_client.disconnect()
                self.music_sessions.pop(member.guild.id, None)

async def setup(bot):
    await bot.add_cog(Music(bot, bot.pool))