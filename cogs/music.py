import discord
from discord.ext import commands
import asyncio
import yt_dlp
import aiohttp
import re

class MusicUI(discord.ui.View):
    def __init__(self, bot, ctx):
        super().__init__(timeout=None)
        self.bot = bot
        self.ctx = ctx

    @discord.ui.button(label="⏮️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Fitur antrean (Prev) belum tersedia untuk saat ini.", ephemeral=True)

    @discord.ui.button(label="⏸️ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_paused():
                vc.resume()
                await interaction.response.send_message("▶️ Musik dilanjutkan.", ephemeral=True)
            elif vc.is_playing():
                vc.pause()
                await interaction.response.send_message("⏸️ Musik dijeda.", ephemeral=True)
            else:
                await interaction.response.send_message("Tidak ada musik yang sedang diputar.", ephemeral=True)
        else:
            await interaction.response.send_message("Bot tidak berada di Voice Channel.", ephemeral=True)

    @discord.ui.button(label="⏭️ Stop/Skip", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏹️ Musik dihentikan.", ephemeral=True)
        else:
            await interaction.response.send_message("Tidak ada musik yang sedang diputar.", ephemeral=True)

    @discord.ui.button(label="📋 Antrean", style=discord.ButtonStyle.success)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Fitur antrean penuh sedang dalam pengembangan.", ephemeral=True)


class Music(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.music_sessions = {} 

        self.YDL_OPTIONS = {
            'format': 'ba/bestaudio/b/best', 
            'noplaylist': True, 
            'quiet': True,
            'default_search': 'auto',
            'cookiefile': 'cookies.txt'
        }
        
        self.FFMPEG_OPTIONS = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 
            'options': '-vn'
        }

    async def deduct_coins(self, user_id, amount):
        data = await self.pool.fetchrow("SELECT coins FROM levels WHERE user_id = $1", user_id)
        if data and data['coins'] >= amount:
            await self.pool.execute("UPDATE levels SET coins = coins - $1 WHERE user_id = $2", amount, user_id)
            return True
        return False

    # === FUNGSI RAHASIA: PENERJEMAH LINK SPOTIFY / APPLE MUSIC ===
    async def convert_link(self, url):
        # 1. Jika URL dari Spotify (Menggunakan OEmbed API Gratis)
        if "spotify.com" in url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://open.spotify.com/oembed?url={url}") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            title = data.get('title', '')
                            author = data.get('author_name', '')
                            # Ubah menjadi keyword pencarian YouTube
                            return f"{title} {author} audio"
            except Exception as e:
                print(f"Gagal translate Spotify: {e}")

        # 2. Jika URL dari Apple Music (Membaca tag <title> web)
        elif "apple.com" in url:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                            if match:
                                title = match.group(1).replace(" on Apple Music", "").replace(" - Apple Music", "")
                                return f"{title} audio"
            except Exception as e:
                print(f"Gagal translate Apple Music: {e}")
        
        # Jika bukan link Spotify/Apple, kembalikan teks aslinya (Bisa link YT atau sekadar judul ketikan)
        return url

    @commands.command(name="music")
    async def play_music(self, ctx, *, query: str):
        try: await ctx.message.delete()
        except: pass

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(f"❌ {ctx.author.mention}, kamu harus masuk room voice dulu ya!", delete_after=5.0)
            return

        is_playlist = "playlist" in query.lower() or "list=" in query.lower()
        cost = 10 if is_playlist else 3
        
        has_enough_coins = await self.deduct_coins(ctx.author.id, cost)
        if not has_enough_coins:
            await ctx.send(f"🪙 Koin kamu tidak cukup! Butuh **{cost} koin**.", delete_after=5.0)
            return

        # 1. Masukkan Bot ke Voice Channel
        voice_channel = ctx.author.voice.channel
        vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)

        if not vc:
            vc = await voice_channel.connect(timeout=10.0)
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)

        self.music_sessions[ctx.guild.id] = {
            'requester_id': ctx.author.id,
            'voice_channel': voice_channel.id
        }

        # Pesan Loading sementara
        loading_msg = await ctx.send("⏳ Sedang memproses audio, mohon tunggu sebentar...")

        # --- EKSEKUSI PENERJEMAH LINK ---
        if "spotify.com" in query or "apple.com" in query:
            await loading_msg.edit(content="🔍 Membaca link musik Spotify/Apple...")
            query = await self.convert_link(query)

        # [BARU] Paksa yt-dlp melakukan pencarian jika teks bukan berupa link murni
        if not query.startswith("http"):
            query = f"ytsearch:{query}"

        # 2. Proses Pencarian dengan yt-dlp
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(self.YDL_OPTIONS) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                
                if 'entries' in info:
                    info = info['entries'][0]
                
                audio_url = info['url']
                title = info.get('title', 'Unknown Title')
                
        except Exception as e:
            print(f"Error yt-dlp: {e}")
            # Tampilkan pesan error ke Discord agar mudah dicek
            await loading_msg.edit(content=f"❌ **Gagal memproses lagu.**\n```py\n{e}\n```")
            await asyncio.sleep(10)
            await loading_msg.delete()
            return

        # 3. Putar Musik
        if vc.is_playing():
            vc.stop()

        try:
            source = await discord.FFmpegOpusAudio.from_probe(audio_url, **self.FFMPEG_OPTIONS)
            vc.play(source)
        except Exception as e:
            print(f"Error FFmpeg: {e}")
            await loading_msg.edit(content=f"❌ **Terjadi kesalahan saat mencoba memutar audio:**\n```py\n{e}\n```")
            return

        # 4. Kirim UI Player
        embed = discord.Embed(
            title="🎵 Now Playing (DekVee Music)",
            description=f"**{title}**",
            color=discord.Color.blurple()
        )
        
        file = discord.File("assets/coin.png", filename="coin.png")
        embed.set_thumbnail(url="attachment://coin.png") 
        
        embed.set_footer(text=f"Requested by {ctx.author.name} | Sisa Koin terpotong {cost}", icon_url=ctx.author.display_avatar.url)

        view = MusicUI(self.bot, ctx)
        await loading_msg.delete() 
        await ctx.send(file=file, embed=embed, view=view)

        # 5. [BARU] Sistem Otomatis Keluar (Auto-Disconnect)
        # Menunggu sampai lagu benar-benar berhenti (habis, atau distop manual via tombol)
        while vc.is_playing() or vc.is_paused():
            await asyncio.sleep(1.0)

        # Cek apakah bot masih terhubung dan sudah tidak ada lagu yang diputar
        if vc and vc.is_connected() and not vc.is_playing() and not vc.is_paused():
            await vc.disconnect()
            self.music_sessions.pop(ctx.guild.id, None)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        
        guild_session = self.music_sessions.get(member.guild.id)
        if guild_session:
            if member.id == guild_session['requester_id'] and before.channel is not None and after.channel is None:
                voice_client = discord.utils.get(self.bot.voice_clients, guild=member.guild)
                if voice_client and voice_client.is_connected():
                    await voice_client.disconnect()
                self.music_sessions.pop(member.guild.id, None)

async def setup(bot):
    await bot.add_cog(Music(bot, bot.pool))