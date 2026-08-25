import discord
from discord.ext import commands, tasks
import time

from roomconfig import LOG_CHANNEL_ID, is_private_call

# ====================================================================
# 0. KONFIGURASI (gampang di-tuning saat testing)
# ====================================================================
AFK_LIMIT_SECONDS = 3600      # Batas AFK sebelum dikeluarkan (1 jam)
WARN_BEFORE_SECONDS = 300     # Peringatan dikirim 5 menit sebelum kick
CHECK_INTERVAL_SECONDS = 30   # Presisi kick jadi ±30 detik
COUNT_SERVER_MUTE = False     # True = mute/deafen dari moderator ikut dihitung AFK


class AfkKick(commands.Cog):
    """Mengeluarkan user dari voice channel kalau mute + deafen bersamaan > 1 jam.

    Room privat (kategori auto private call) DIKECUALIKAN — di sana orang bebas
    mau AFK selama apapun.
    """

    def __init__(self, bot):
        self.bot = bot
        # member_id -> {"since": float, "channel_id": int, "warned": bool}
        self.afk_state = {}
        self.is_started = False

    def cog_unload(self):
        self.afk_watch_task.cancel()

    # ----------------------------------------------------------------
    # HELPER
    # ----------------------------------------------------------------
    def is_afk_state(self, voice_state) -> bool:
        """Kondisi AFK: mic mati DAN suara mati bersamaan."""
        if voice_state is None or voice_state.channel is None:
            return False

        muted = voice_state.self_mute or (COUNT_SERVER_MUTE and voice_state.mute)
        deafened = voice_state.self_deaf or (COUNT_SERVER_MUTE and voice_state.deaf)
        return bool(muted and deafened)

    def is_watched_channel(self, channel) -> bool:
        """Channel yang ikut dipantau: semua VC kecuali room privat & AFK channel server."""
        if channel is None:
            return False
        if is_private_call(channel):
            return False

        afk_channel = getattr(channel.guild, "afk_channel", None)
        if afk_channel is not None and channel.id == afk_channel.id:
            return False
        return True

    def is_tracked(self, member, voice_state) -> bool:
        if member.bot:
            return False
        if not self.is_watched_channel(getattr(voice_state, "channel", None)):
            return False
        return self.is_afk_state(voice_state)

    async def log_to_admin(self, title, description, color):
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return
        embed = discord.Embed(title=title, description=description, color=color)
        embed.timestamp = discord.utils.utcnow()
        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

    def sync_member(self, member, voice_state):
        """Mulai / lanjutkan / hapus timer AFK sesuai kondisi terbaru member."""
        if not self.is_tracked(member, voice_state):
            self.afk_state.pop(member.id, None)
            return

        existing = self.afk_state.get(member.id)
        if existing and existing["channel_id"] == voice_state.channel.id:
            return  # timer lama tetap jalan

        # Timer baru: user baru mulai AFK, atau pindah channel (hitung dari awal lagi)
        self.afk_state[member.id] = {
            "since": time.time(),
            "channel_id": voice_state.channel.id,
            "warned": False,
        }

    # ----------------------------------------------------------------
    # EVENT
    # ----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        # Hitung ulang dari kondisi voice channel saat ini (state tidak disimpan ke DB,
        # jadi setelah restart timer mulai dari bot ready).
        self.afk_state.clear()
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                if not self.is_watched_channel(vc):
                    continue
                for member in vc.members:
                    self.sync_member(member, member.voice)

        if not self.is_started:
            self.is_started = True
            if not self.afk_watch_task.is_running():
                self.afk_watch_task.start()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        self.sync_member(member, after)

    # ----------------------------------------------------------------
    # LOOP PENGAWAS
    # ----------------------------------------------------------------
    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def afk_watch_task(self):
        now = time.time()

        for member_id, data in list(self.afk_state.items()):
            channel = self.bot.get_channel(data["channel_id"])
            if not isinstance(channel, discord.VoiceChannel):
                self.afk_state.pop(member_id, None)
                continue

            member = channel.guild.get_member(member_id)
            # Baca state LIVE, jangan percaya cache internal cog
            if member is None or not self.is_tracked(member, member.voice):
                self.afk_state.pop(member_id, None)
                continue
            if member.voice.channel.id != data["channel_id"]:
                self.sync_member(member, member.voice)
                continue

            elapsed = now - data["since"]

            if elapsed >= AFK_LIMIT_SECONDS:
                await self.kick_from_voice(member, channel, elapsed)
                self.afk_state.pop(member_id, None)
            elif elapsed >= (AFK_LIMIT_SECONDS - WARN_BEFORE_SECONDS) and not data["warned"]:
                data["warned"] = True
                await self.send_warning(member, channel)

    @afk_watch_task.before_loop
    async def before_afk_watch(self):
        await self.bot.wait_until_ready()

    async def send_warning(self, member, channel):
        menit = max(1, WARN_BEFORE_SECONDS // 60)
        try:
            await channel.send(
                f"⏳ {member.mention} kamu terdeteksi **AFK** (mic mati + suara mati). "
                f"Kalau dalam **{menit} menit** ke depan masih begini, kamu otomatis dikeluarkan dari voice ini.\n"
                f"*Cukup nyalakan mic atau suara supaya hitungannya di-reset.*",
                delete_after=WARN_BEFORE_SECONDS + 60,
            )
        except Exception:
            pass

    async def kick_from_voice(self, member, channel, elapsed):
        menit = int(elapsed // 60)
        reason = f"AFK (mute + deafen) selama {menit} menit"

        try:
            await member.move_to(None, reason=reason)
        except discord.Forbidden:
            await self.log_to_admin(
                "⚠️ Gagal Kick AFK",
                f"**Member:** {member.mention}\n**Room:** {channel.mention}\n"
                f"**Alasan gagal:** Bot tidak punya izin **Move Members**.",
                discord.Color.dark_red(),
            )
            return
        except Exception as e:
            await self.log_to_admin(
                "⚠️ Gagal Kick AFK",
                f"**Member:** {member.mention}\n**Room:** {channel.mention}\n**Error:** `{e!r}`",
                discord.Color.dark_red(),
            )
            return

        try:
            await member.send(
                f"👋 Kamu dikeluarkan dari voice **{channel.name}** di server **{channel.guild.name}** "
                f"karena AFK (mic dan suara mati) selama lebih dari {menit} menit.\n"
                "Kamu bisa masuk lagi kapan saja."
            )
        except Exception:
            pass

        await self.log_to_admin(
            "🔇 Member Dikick karena AFK",
            f"**Member:** {member.mention} (`{member}`)\n"
            f"**Room:** {channel.mention} (`{channel.name}`)\n"
            f"**Durasi AFK:** {menit} menit (mute + deafen)",
            discord.Color.orange(),
        )

    # ----------------------------------------------------------------
    # COMMAND DIAGNOSA
    # ----------------------------------------------------------------
    @commands.command(name="afkcek")
    @commands.has_permissions(administrator=True)
    async def afkcek(self, ctx):
        """Lihat siapa saja yang sedang dipantau sistem AFK."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = discord.Embed(
            title="🕵️ Pemantauan AFK (mute + deafen)",
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Limit {AFK_LIMIT_SECONDS // 60} menit • warning {WARN_BEFORE_SECONDS // 60} menit sebelum kick "
                 f"• cek tiap {CHECK_INTERVAL_SECONDS} detik"
        )

        if not self.afk_state:
            embed.description = "Tidak ada member yang sedang AFK di voice channel yang dipantau."
        else:
            now = time.time()
            baris = []
            for member_id, data in self.afk_state.items():
                channel = self.bot.get_channel(data["channel_id"])
                member = channel.guild.get_member(member_id) if isinstance(channel, discord.VoiceChannel) else None
                nama = member.display_name if member else f"ID: {member_id}"
                nama_room = channel.name if channel else "Unknown"
                menit = int((now - data["since"]) // 60)
                tanda = "⚠️ sudah diperingatkan" if data["warned"] else "⏳ dipantau"
                baris.append(f"👤 **{nama}** — `{nama_room}` — **{menit} menit** — {tanda}")
            embed.description = "\n".join(baris)

        await ctx.send(embed=embed, delete_after=60)

    @afkcek.error
    async def afkcek_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Command ini hanya untuk Administrator.", delete_after=10)
        else:
            raise error


async def setup(bot):
    await bot.add_cog(AfkKick(bot))
