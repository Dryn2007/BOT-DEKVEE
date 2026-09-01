import time

import discord
from discord.ext import commands, tasks

from roomconfig import LOG_CHANNEL_ID, is_private_call

# ====================================================================
# 0. KONFIGURASI (gampang di-tuning saat testing)
# ====================================================================
VOICE_LIMIT_SECONDS = 3600      # Batas maksimal nongkrong di voice (1 jam)
WARN_BEFORE_SECONDS = 120       # Diingatkan 2 menit sebelum dikeluarkan
CHECK_INTERVAL_SECONDS = 15     # Presisi peringatan/kick jadi ±15 detik
SKIP_ROOM_PRIVAT = True         # True = room privat bebas, nggak ada batas waktu


def label_durasi(detik) -> str:
    """3600 -> '1 jam', 120 -> '2 menit', 5400 -> '1 jam 30 menit'."""
    jam, menit = divmod(int(detik) // 60, 60)
    if jam and menit:
        return f"{jam} jam {menit} menit"
    if jam:
        return f"{jam} jam"
    return f"{max(1, menit)} menit"


class VoiceCheck(commands.Cog):
    """Batas waktu nongkrong di voice: lewat 1 jam, otomatis dikeluarkan.

    Nggak ada soal/verifikasi apa pun — yang dihitung murni lama duduk di
    voice, nggak peduli mic/suaranya nyala atau nggak. 2 menit sebelum batas
    waktu ada peringatan di chat voice ("jangan terlalu lama di voice"), pas
    batasnya habis member di-disconnect (dapat DM + log admin) dan boleh join
    lagi kapan aja — begitu join lagi hitungannya mulai dari nol.

    Room privat & AFK channel server dikecualikan (lihat SKIP_ROOM_PRIVAT).
    """

    def __init__(self, bot):
        self.bot = bot
        # member_id -> {"channel_id": int, "since": float, "warned": bool}
        self.tracked = {}
        self.is_started = False

    def cog_unload(self):
        self.voice_check_task.cancel()

    # ----------------------------------------------------------------
    # HELPER
    # ----------------------------------------------------------------
    def is_watched_channel(self, channel) -> bool:
        """VC yang ikut dibatasi: semua kecuali room privat & AFK channel server."""
        if channel is None:
            return False
        if SKIP_ROOM_PRIVAT and is_private_call(channel):
            return False

        afk_channel = getattr(channel.guild, "afk_channel", None)
        if afk_channel is not None and channel.id == afk_channel.id:
            return False
        return True

    def is_tracked(self, member, voice_state) -> bool:
        if member is None or member.bot:
            return False
        return self.is_watched_channel(getattr(voice_state, "channel", None))

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
        """Mulai / lanjutkan / hapus hitungan durasi voice member."""
        if not self.is_tracked(member, voice_state):
            self.tracked.pop(member.id, None)
            return

        data = self.tracked.get(member.id)
        if data is None:
            self.tracked[member.id] = {
                "channel_id": voice_state.channel.id,
                "since": time.time(),
                "warned": False,
            }
            return

        # Pindah room dihitung sebagai lanjutan sesi yang sama, biar batas
        # 1 jam nggak bisa di-reset cuma dengan pindah-pindah channel.
        data["channel_id"] = voice_state.channel.id

    def reset_hitungan(self, member):
        """Dipakai kalau kick gagal: jangan nyoba lagi tiap 15 detik."""
        data = self.tracked.get(member.id)
        if data is None:
            return
        data["since"] = time.time()
        data["warned"] = False

    # ----------------------------------------------------------------
    # EVENT
    # ----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        # State nggak disimpan ke DB, jadi setelah restart hitungan dimulai
        # lagi dari bot ready.
        self.tracked.clear()
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                if not self.is_watched_channel(vc):
                    continue
                for member in vc.members:
                    self.sync_member(member, member.voice)

        if not self.is_started:
            self.is_started = True
            if not self.voice_check_task.is_running():
                self.voice_check_task.start()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        self.sync_member(member, after)

    # ----------------------------------------------------------------
    # LOOP PENGAWAS
    # ----------------------------------------------------------------
    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def voice_check_task(self):
        now = time.time()

        for member_id, data in list(self.tracked.items()):
            channel = self.bot.get_channel(data["channel_id"])
            if not isinstance(channel, discord.VoiceChannel):
                self.tracked.pop(member_id, None)
                continue

            member = channel.guild.get_member(member_id)
            # Baca state LIVE, jangan percaya cache internal cog
            if member is None or not self.is_tracked(member, member.voice):
                self.tracked.pop(member_id, None)
                continue
            if member.voice.channel.id != data["channel_id"]:
                self.sync_member(member, member.voice)
                channel = member.voice.channel   # peringatan/kick ke room terbaru

            elapsed = now - data["since"]

            if elapsed >= VOICE_LIMIT_SECONDS:
                await self.keluarkan(member, channel, elapsed)
            elif elapsed >= (VOICE_LIMIT_SECONDS - WARN_BEFORE_SECONDS) and not data["warned"]:
                data["warned"] = True
                await self.kirim_peringatan(member, channel)

    @voice_check_task.before_loop
    async def before_voice_check(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------------
    # PERINGATAN & KELUARKAN
    # ----------------------------------------------------------------
    async def kirim_peringatan(self, member, channel):
        try:
            await channel.send(
                f"⏳ {member.mention} kamu udah hampir **{label_durasi(VOICE_LIMIT_SECONDS)}** di voice. "
                f"**{label_durasi(WARN_BEFORE_SECONDS)} lagi** kamu otomatis dikeluarkan — "
                f"jangan terlalu lama di voice ya.\n"
                f"*Santai, habis keluar boleh langsung join lagi kok.*",
                delete_after=WARN_BEFORE_SECONDS + 60,
            )
            return
        except Exception:
            pass

        # Chat voice-nya nggak bisa dipakai (izin/dimatikan) -> coba lewat DM
        try:
            await member.send(
                f"⏳ **{label_durasi(WARN_BEFORE_SECONDS)} lagi** kamu otomatis dikeluarkan dari voice "
                f"**{channel.name}** (server **{channel.guild.name}**) karena udah hampir "
                f"**{label_durasi(VOICE_LIMIT_SECONDS)}** di voice. Jangan terlalu lama di voice ya!"
            )
        except Exception:
            pass

    async def keluarkan(self, member, channel, elapsed):
        menit = int(elapsed // 60)
        alasan = f"Batas waktu voice {label_durasi(VOICE_LIMIT_SECONDS)} habis ({menit} menit)"

        try:
            await member.move_to(None, reason=alasan)
        except discord.Forbidden:
            self.reset_hitungan(member)
            await self.gagal_keluarkan(member, channel, "Bot tidak punya izin **Move Members**.")
            return
        except Exception as e:
            self.reset_hitungan(member)
            await self.gagal_keluarkan(member, channel, f"Error: `{e!r}`")
            return

        self.tracked.pop(member.id, None)

        # Notifikasi publik di chat voice
        try:
            await channel.send(
                f"⌛ {member.mention} udah **{label_durasi(VOICE_LIMIT_SECONDS)}** di voice, "
                f"jadi otomatis dikeluarkan. Jangan terlalu lama di voice ya — "
                f"mau join lagi tinggal masuk aja. 👋",
                delete_after=300,
            )
        except Exception:
            pass

        try:
            await member.send(
                f"👋 Kamu dikeluarkan dari voice **{channel.name}** di server "
                f"**{channel.guild.name}** karena udah **{menit} menit** di voice "
                f"(batasnya {label_durasi(VOICE_LIMIT_SECONDS)}).\n"
                "Jangan terlalu lama di voice ya — kamu bisa masuk lagi kapan saja."
            )
        except Exception:
            pass

        await self.log_to_admin(
            "🕐 Member Dikeluarkan (Batas Waktu Voice)",
            f"**Member:** {member.mention} (`{member}`)\n"
            f"**Room:** {channel.mention} (`{channel.name}`)\n"
            f"**Durasi di voice:** {menit} menit "
            f"(batas {label_durasi(VOICE_LIMIT_SECONDS)})",
            discord.Color.orange(),
        )

    async def gagal_keluarkan(self, member, channel, sebab):
        await self.log_to_admin(
            "⚠️ Gagal Keluarkan dari Voice (Batas Waktu)",
            f"**Member:** {member.mention}\n**Room:** {channel.mention}\n"
            f"**Alasan gagal:** {sebab}",
            discord.Color.dark_red(),
        )

    # ----------------------------------------------------------------
    # COMMAND DIAGNOSA
    # ----------------------------------------------------------------
    @commands.command(name="voicecek")
    @commands.has_permissions(administrator=True)
    async def voicecek(self, ctx):
        """Lihat siapa saja yang sedang dihitung durasi voice-nya."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = discord.Embed(title="🕐 Batas Waktu Voice", color=discord.Color.blurple())
        embed.set_footer(
            text=f"Batas {label_durasi(VOICE_LIMIT_SECONDS)} "
                 f"• diingatkan {label_durasi(WARN_BEFORE_SECONDS)} sebelum out "
                 f"• cek tiap {CHECK_INTERVAL_SECONDS} detik"
        )

        if not self.tracked:
            embed.description = "Nggak ada member di voice channel yang dibatasi."
        else:
            now = time.time()
            baris = []
            for member_id, data in self.tracked.items():
                channel = self.bot.get_channel(data["channel_id"])
                member = (
                    channel.guild.get_member(member_id)
                    if isinstance(channel, discord.VoiceChannel) else None
                )
                nama = member.display_name if member else f"ID: {member_id}"
                nama_room = channel.name if channel else "Unknown"

                menit = int((now - data["since"]) // 60)
                sisa = max(0, int((VOICE_LIMIT_SECONDS - (now - data["since"])) // 60))
                tanda = "⚠️ udah diingatkan" if data["warned"] else f"sisa ±{sisa} menit"
                baris.append(f"👤 **{nama}** — `{nama_room}` — **{menit} menit** — {tanda}")

            if len(baris) > 25:
                baris = baris[:25] + [f"*...dan {len(baris) - 25} member lainnya*"]
            embed.description = "\n".join(baris)

        await ctx.send(embed=embed, delete_after=60)

    @voicecek.error
    async def voicecek_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Command ini hanya untuk Administrator.", delete_after=10)
        else:
            raise error


async def setup(bot):
    await bot.add_cog(VoiceCheck(bot))
