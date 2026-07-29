import discord
from discord.ext import commands
import asyncio
import yt_dlp
import aiohttp
import re

class MusicUI(discord.ui.View):
    def __init__(self, bot, ctx, cog):
        super().__init__(timeout=None)
        self.bot = bot
        self.ctx = ctx
        self.cog = cog

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Fitur mundur (Prev) belum tersedia untuk sistem Antrean baru.", ephemeral=True)

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_session = self.cog.music_sessions.get(interaction.guild.id)
        if guild_session and interaction.user.id != guild_session.get('requester_id'):
            await interaction.response.send_message("❌ Hanya orang yang menyetel lagu ini yang bisa menjeda atau melanjutkannya!", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        guild_id = interaction.guild.id
        
        if vc:
            if vc.is_paused():
                vc.resume()
                # Batalkan timer 1 menit karena lagu sudah dilanjutkan
                pause_task = self.cog.pause_tasks.pop(guild_id, None)
                if pause_task:
                    pause_task.cancel()
                    
            elif vc.is_playing():
                vc.pause()
                
                # Fungsi timer otomatis 60 detik
                async def auto_skip():
                    try:
                        await asyncio.sleep(60) # Menunggu 1 menit
                        if vc and vc.is_paused():
                            vc.stop() # Skip otomatis ke lagu selanjutnya
                    except asyncio.CancelledError:
                        pass # Timer dibatalkan karena tombol Resume ditekan
                        
                # Simpan dan jalankan timer di memori server
                old_task = self.cog.pause_tasks.get(guild_id)
                if old_task:
                    old_task.cancel()
                self.cog.pause_tasks[guild_id] = asyncio.create_task(auto_skip())
        
        try: await interaction.response.defer()
        except: pass

    @discord.ui.button(label="Stop/Skip", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_session = self.cog.music_sessions.get(interaction.guild.id)
        if guild_session and interaction.user.id != guild_session.get('requester_id'):
            await interaction.response.send_message("❌ Hanya orang yang menyetel lagu ini yang bisa melewatinya (Skip)!", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            # Batalkan timer jika lagu di-skip manual saat sedang dijeda
            pause_task = self.cog.pause_tasks.pop(interaction.guild.id, None)
            if pause_task:
                pause_task.cancel()
            vc.stop() 
        
        try: await interaction.response.defer()
        except: pass

    @discord.ui.button(label="Antrean", style=discord.ButtonStyle.success)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = self.cog.queues.get(interaction.guild.id, [])
        if not queue:
            await interaction.response.send_message("Antrean saat ini kosong.", ephemeral=True)
        else:
            q_list = "\n".join([f"{i+1}. {q['query'].replace('ytsearch:', '')}" for i, q in enumerate(queue[:10])])
            if len(queue) > 10:
                q_list += f"\n*...dan {len(queue) - 10} lagu lainnya.*"
            await interaction.response.send_message(f"**Daftar Antrean:**\n{q_list}", ephemeral=True)

class Music(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.music_sessions = {}
        self.queues = {} 
        self.pause_tasks = {} # Memori khusus untuk menyimpan timer jeda per server

        self.YDL_OPTIONS = {
            'format': 'bestaudio/best',
            'restrictfilenames': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'logtostderr': False,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0',
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

    async def convert_link(self, url):
        if "spotify.com" in url:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                            if match:
                                title = match.group(1).split('|')[0].replace("- Spotify", "").strip()
                                return f"{title} audio"
            except Exception as e: print(f"Gagal translate Spotify: {e}")
        elif "apple.com" in url:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                            if match:
                                title = match.group(1).replace(" on Apple Music", "").replace("- Apple Music", "")
                                return f"{title} audio"
            except Exception as e: print(f"Gagal translate Apple Music: {e}")
        elif "youtube.com" in url or "youtu.be" in url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://www.youtube.com/oembed?url={url}&format=json") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            title = data.get('title', '')
                            return f"{title} audio"
            except Exception as e: print(f"Gagal translate YouTube: {e}")
        return url

    async def play_next(self, ctx):
        vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        if not vc or not vc.is_connected():
            return

        guild_id = ctx.guild.id
        
        # Bersihkan sisa timer jika ada agar tidak bentrok dengan lagu baru
        pause_task = self.pause_tasks.pop(guild_id, None)
        if pause_task:
            pause_task.cancel()

        queue = self.queues.get(guild_id, [])

        guild_session = self.music_sessions.get(guild_id, {})
        old_msg = guild_session.get('player_msg')
        if old_msg:
            try: await old_msg.delete()
            except: pass

        if not queue:
            try: await ctx.send("✅ Antrean habis. DekVee keluar dari Voice Channel ya!", delete_after=10.0)
            except: pass
            
            if vc.is_connected():
                await vc.disconnect()
            self.music_sessions.pop(guild_id, None)
            return

        next_track = queue.pop(0)
        query = next_track['query']
        requester = next_track['requester']

        loading_msg = await ctx.send(f"⏳ Memproses lagu: **{query.replace('ytsearch:', '')}**...")

        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(self.YDL_OPTIONS) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                if 'entries' in info:
                    info = info['entries'][0]
                audio_url = info['url']
                title = info.get('title', 'Unknown Title')
        except Exception as e:
            await loading_msg.edit(content=f"❌ Gagal memutar lagu. Lanjut ke lagu berikutnya...")
            await asyncio.sleep(3)
            await loading_msg.delete()
            return await self.play_next(ctx) 

        try:
            source = discord.FFmpegPCMAudio(audio_url, **self.FFMPEG_OPTIONS)
            
            def after_playing(err):
                if err: print(f"Error FFmpeg: {err}")
                asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop)

            vc.play(source, after=after_playing)
        except Exception as e:
            await loading_msg.edit(content="❌ Terjadi kesalahan sistem. Lanjut ke lagu berikutnya...")
            await asyncio.sleep(3)
            await loading_msg.delete()
            return await self.play_next(ctx)

        embed = discord.Embed(
            title="🎵 Now Playing (DekVee Music)",
            description=f"**{title}**",
            color=discord.Color.blurple()
        )
        file = discord.File("assets/coin.png", filename="coin.png")
        embed.set_thumbnail(url="attachment://coin.png")
        embed.set_footer(text=f"Requested by {requester.name} | Sisa Antrean: {len(queue)}", icon_url=requester.display_avatar.url)

        view = MusicUI(self.bot, ctx, self)
        await loading_msg.delete()
        player_msg = await ctx.send(file=file, embed=embed, view=view)

        self.music_sessions[guild_id] = {
            'requester_id': requester.id,
            'player_msg': player_msg
        }

    @commands.command(name="music")
    async def play_music(self, ctx, *, query: str):
        try: await ctx.message.delete()
        except: pass

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(f"❌ {ctx.author.mention}, kamu harus masuk room voice dulu ya!", delete_after=5.0)
            return

        if ("spotify.com" in query or "apple.com" in query) and ("playlist" in query or "album" in query):
            await ctx.send("❌ **Peringatan! Fitur Playlist saat ini HANYA mendukung link dari YouTube**. Silakan gunakan link Playlist YouTube ya!", delete_after=10.0)
            return

        cost = 10 if ("playlist" in query.lower() or "&list=" in query.lower()) else 3
        has_enough_coins = await self.deduct_coins(ctx.author.id, cost)
        if not has_enough_coins:
            await ctx.send(f"🪙 Koin kamu tidak cukup! Butuh **{cost} koin**.", delete_after=5.0)
            return

        if ctx.guild.id not in self.queues:
            self.queues[ctx.guild.id] = []

        voice_channel = ctx.author.voice.channel
        vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        
        if vc and not vc.is_connected():
            await vc.disconnect(force=True)
            vc = None

        if not vc:
            try:
                vc = await voice_channel.connect(timeout=10.0)
            except Exception as e:
                await ctx.send("❌ Bot gagal terhubung ke Voice Channel. (Jaringan terblokir, hubungi Kamatera)", delete_after=10.0)
                return
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)

        if "youtube.com/playlist" in query or "&list=" in query:
            msg = await ctx.send("⏳ Membongkar daftar lagu dari Playlist YouTube...")
            ydl_opts = {'extract_flat': True, 'quiet': True}
            loop = asyncio.get_event_loop()
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                    if 'entries' in info:
                        for entry in info['entries']:
                            title = entry.get('title')
                            if title:
                                self.queues[ctx.guild.id].append({
                                    'query': f"ytsearch:{title}",
                                    'requester': ctx.author
                                })
                
                await msg.edit(content=f"✅ Berhasil memasukkan **{len(info['entries'])} lagu** ke dalam antrean!")
                
                if not vc.is_playing() and not vc.is_paused():
                    await self.play_next(ctx)
                
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass
                return 

            except Exception as e:
                await msg.edit(content="❌ Gagal membaca playlist YouTube.")
                return 

        else:
            if "spotify.com" in query or "apple.com" in query or "youtube.com" in query or "youtu.be" in query:
                query = await self.convert_link(query)

            if not query.startswith("http"):
                query = f"ytsearch:{query}"

            self.queues[ctx.guild.id].append({
                'query': query,
                'requester': ctx.author
            })

            if vc.is_playing() or vc.is_paused():
                await ctx.send(f"✅ **Ditambahkan ke antrean:** {query.replace('ytsearch:', '')}", delete_after=5.0)

        if not vc.is_playing() and not vc.is_paused():
            await self.play_next(ctx)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: 
            return

        vc = discord.utils.get(self.bot.voice_clients, guild=member.guild)
        if not vc or not vc.is_connected():
            return

        if len(vc.channel.members) == 1:
            await asyncio.sleep(3) 
            
            if len(vc.channel.members) == 1:
                await vc.disconnect()
                
                guild_id = member.guild.id
                guild_session = self.music_sessions.get(guild_id)
                
                if guild_session:
                    player_msg = guild_session.get('player_msg')
                    if player_msg:
                        try: await player_msg.delete()
                        except: pass
                        
                # Hapus jejak timer dan antrean saat bot keluar
                self.music_sessions.pop(guild_id, None)
                self.queues.pop(guild_id, None)
                pause_task = self.pause_tasks.pop(guild_id, None)
                if pause_task:
                    pause_task.cancel()

async def setup(bot):
    await bot.add_cog(Music(bot, bot.pool))