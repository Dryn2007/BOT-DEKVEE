import asyncio
import time

import discord
from discord.ext import commands, tasks

from cogs.autogate import ROLE_IDS
from roomconfig import LOG_CHANNEL_ID, PRODI_CHAT_ROOMS, is_private_call

# ====================================================================
# 0. KONFIGURASI (gampang di-tuning saat testing)
# ====================================================================
VOICE_LIMIT_SECONDS = 3600      # Umur maksimal satu sesi room voice (1 jam)
WARN_BEFORE_SECONDS = 120       # Room diingatkan 2 menit sebelum dikosongkan
CHECK_INTERVAL_SECONDS = 15     # Presisi peringatan/kick jadi ±15 detik
SKIP_ROOM_PRIVAT = True         # True = room privat bebas, nggak ada batas waktu
MAX_TULIS_NAMA = 15             # Batas mention/nama yang ditulis dalam 1 pesan

# ID role prodi -> ID room chat prodi. ID role diambil dari ROLE_IDS (autogate)
# dan ID room chat dari roomconfig, jadi nggak ada ID yang disalin dua kali.
PRODI_ROLE_TO_CHAT = {
    ROLE_IDS[key]: chat_id
    for key, chat_id in PRODI_CHAT_ROOMS.items()
    if key in ROLE_IDS
}


def label_durasi(detik) -> str:
    """3600 -> '1 jam', 120 -> '2 menit', 5400 -> '1 jam 30 menit'."""
    jam, menit = divmod(int(detik) // 60, 60)
    if jam and menit:
        return f"{jam} jam {menit} menit"
    if jam:
        return f"{jam} jam"
    return f"{max(1, menit)} menit"


def ringkas_nama(items, formatter) -> str:
    """Gabung daftar member jadi satu baris, dipotong kalau kebanyakan."""
    if not items:
        return "—"
    teks = ", ".join(formatter(x) for x in items[:MAX_TULIS_NAMA])
    sisa = len(items) - MAX_TULIS_NAMA
    if sisa > 0:
        teks += f" (+{sisa} member lain)"
    return teks


class VoiceCheck(commands.Cog):
    """Batas umur room voice: lewat 1 jam, seisi room dikeluarkan bareng.

    Yang dihitung UMUR ROOM (sejak room kosong lalu mulai ada orang), bukan
    durasi tiap orang. Versi lama menghitung per member, jadi kick-nya nyicil
    satu-satu dan selalu ada yang masih nyangkut di room — call-nya nggak
    pernah selesai dan timer room di Discord nggak pernah balik ke 0. Sekarang
    pas batas waktunya habis SEMUA member di room itu di-disconnect bersamaan,
    room jadi benar-benar kosong dan timernya reset ke 0.

    2 menit sebelum batas ada peringatan di chat voice-nya. Setelah room
    dikosongkan, member dikabari di ROOM CHAT PRODI-nya masing-masing (plus DM
    + log admin) — biar mereka tetap baca walau udah nggak di voice. Boleh join
    lagi kapan aja: begitu ada yang masuk, hitungan room mulai dari nol.

    Room privat & AFK channel server dikecualikan (lihat SKIP_ROOM_PRIVAT).
    """

    def __init__(self, bot):
        self.bot = bot
        # channel_id -> {"since": float, "warned": bool}
        self.rooms = {}
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

    def anggota_room(self, channel):
        """Member manusia yang ada di room (bot nggak dihitung)."""
        if channel is None:
            return []
        return [m for m in channel.members if not m.bot]

    def sync_room(self, channel):
        """Mulai / hapus hitungan umur room sesuai isi room sekarang."""
        if channel is None:
            return

        # Room kosong (atau nggak dipantau) = sesi selesai, hitungan balik nol.
        if not self.is_watched_channel(channel) or not self.anggota_room(channel):
            self.rooms.pop(channel.id, None)
            return

        # Room yang udah jalan nggak di-reset cuma karena ada orang masuk/keluar
        # — yang dihitung umur sesi room-nya, bukan durasi per orang.
        if channel.id not in self.rooms:
            self.rooms[channel.id] = {"since": time.time(), "warned": False}

    def reset_hitungan(self, channel_id):
        """Dipakai kalau room gagal dikosongkan: jangan nyoba lagi tiap 15 detik."""
        data = self.rooms.get(channel_id)
        if data is None:
            return
        data["since"] = time.time()
        data["warned"] = False

    def chat_prodi(self, member):
        """Room chat prodi si member (None kalau prodinya belum punya room chat)."""
        for role in getattr(member, "roles", []):
            chat_id = PRODI_ROLE_TO_CHAT.get(role.id)
            if chat_id:
                channel = self.bot.get_channel(chat_id)
                if channel is not None:
                    return channel
        return None

    def kelompokkan_per_prodi(self, members):
        """-> ({room_chat_prodi: [member, ...]}, [member tanpa room prodi])."""
        per_prodi, tanpa_prodi = {}, []
        for member in members:
            chat = self.chat_prodi(member)
            if chat is None:
                tanpa_prodi.append(member)
            else:
                per_prodi.setdefault(chat, []).append(member)
        return per_prodi, tanpa_prodi

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

    # ----------------------------------------------------------------
    # EVENT
    # ----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        # State nggak disimpan ke DB, jadi setelah restart umur room dihitung
        # lagi dari bot ready.
        self.rooms.clear()
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                self.sync_room(vc)

        if not self.is_started:
            self.is_started = True
            if not self.voice_check_task.is_running():
                self.voice_check_task.start()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        # Dua-duanya di-sync: room yang ditinggalkan bisa jadi kosong (hitungan
        # dihapus), room yang dimasuki bisa jadi mulai sesi baru.
        for channel in (getattr(before, "channel", None), getattr(after, "channel", None)):
            self.sync_room(channel)

    # ----------------------------------------------------------------
    # LOOP PENGAWAS
    # ----------------------------------------------------------------
    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def voice_check_task(self):
        now = time.time()

        for channel_id, data in list(self.rooms.items()):
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel) or not self.is_watched_channel(channel):
                self.rooms.pop(channel_id, None)
                continue

            # Baca isi room LIVE, jangan percaya cache internal cog
            members = self.anggota_room(channel)
            if not members:
                self.rooms.pop(channel_id, None)
                continue

            elapsed = now - data["since"]

            if elapsed >= VOICE_LIMIT_SECONDS:
                await self.kosongkan_room(channel, members, elapsed)
            elif elapsed >= (VOICE_LIMIT_SECONDS - WARN_BEFORE_SECONDS) and not data["warned"]:
                data["warned"] = True
                await self.kirim_peringatan(channel, members)

    @voice_check_task.before_loop
    async def before_voice_check(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------------
    # PERINGATAN SEBELUM ROOM DIKOSONGKAN
    # ----------------------------------------------------------------
    async def kirim_peringatan(self, channel, members):
        try:
            await channel.send(
                f"⏳ {ringkas_nama(members, lambda m: m.mention)}\n"
                f"Room ini udah hampir **{label_durasi(VOICE_LIMIT_SECONDS)}** jalan. "
                f"**{label_durasi(WARN_BEFORE_SECONDS)} lagi** semua yang ada di sini "
                f"otomatis dikeluarkan bareng biar room-nya kosong lagi — jangan terlalu "
                f"lama di voice ya.\n"
                f"*Santai, habis keluar boleh langsung join lagi kok.*",
                delete_after=WARN_BEFORE_SECONDS + 60,
            )
            return
        except Exception:
            pass

        # Chat voice-nya nggak bisa dipakai (izin/dimatikan) -> coba lewat DM
        await asyncio.gather(*(self.dm_peringatan(m, channel) for m in members))

    async def dm_peringatan(self, member, channel):
        try:
            await member.send(
                f"⏳ **{label_durasi(WARN_BEFORE_SECONDS)} lagi** kamu otomatis dikeluarkan "
                f"dari voice **{channel.name}** (server **{channel.guild.name}**) karena "
                f"room itu udah hampir **{label_durasi(VOICE_LIMIT_SECONDS)}** jalan. "
                f"Jangan terlalu lama di voice ya!"
            )
        except Exception:
            pass

    # ----------------------------------------------------------------
    # KOSONGKAN ROOM (SEMUA MEMBER SEKALIGUS)
    # ----------------------------------------------------------------
    async def kosongkan_room(self, channel, members, elapsed):
        menit = int(elapsed // 60)
        alasan = (
            f"Room udah {label_durasi(VOICE_LIMIT_SECONDS)} jalan ({menit} menit) "
            f"— seisi room dikeluarkan biar timernya balik ke 0"
        )

        # Semua sekaligus, bukan satu-satu: kalau nyicil, selalu ada yang
        # tertinggal di room dan call-nya nggak pernah benar-benar selesai.
        hasil = await asyncio.gather(*(self.keluarkan_satu(m, alasan) for m in members))
        keluar = [m for m, ok, _ in hasil if ok]
        gagal = [(m, sebab) for m, ok, sebab in hasil if not ok]

        if gagal:
            # Masih ada yang nyangkut -> jangan spam percobaan tiap 15 detik
            self.reset_hitungan(channel.id)
        else:
            self.rooms.pop(channel.id, None)

        if keluar:
            await self.kabari_prodi(channel, keluar, menit)
            await asyncio.gather(*(self.dm_keluar(m, channel, menit) for m in keluar))

        await self.log_kosongkan(channel, menit, keluar, gagal)

    async def keluarkan_satu(self, member, alasan):
        """Disconnect satu member. -> (member, berhasil, sebab_gagal)."""
        try:
            await member.move_to(None, reason=alasan)
        except discord.Forbidden:
            return member, False, "bot nggak punya izin **Move Members** (atau role member lebih tinggi)"
        except Exception as e:
            return member, False, f"error `{e!r}`"
        return member, True, None

    async def kabari_prodi(self, voice_channel, members, menit):
        """Kabari member di room chat prodinya masing-masing."""
        per_prodi, tanpa_prodi = self.kelompokkan_per_prodi(members)

        for chat, anggota in per_prodi.items():
            await self.kirim_kabar(chat, voice_channel, anggota, menit)

        # Prodinya belum punya room chat -> kabari di chat voice-nya aja
        if tanpa_prodi:
            await self.kirim_kabar(voice_channel, voice_channel, tanpa_prodi, menit)

    async def kirim_kabar(self, tujuan, voice_channel, members, menit):
        """Pesan "room udah 1 jam" — sengaja nggak auto-delete biar kebaca."""
        try:
            await tujuan.send(
                f"⌛ {ringkas_nama(members, lambda m: m.mention)}\n"
                f"Voice **{voice_channel.name}** udah jalan **{menit} menit** "
                f"(batas **{label_durasi(VOICE_LIMIT_SECONDS)}**), jadi room-nya "
                f"dikosongkan biar timernya bener-bener balik ke 0 — kalian semua "
                f"dikeluarkan bareng. Jangan terlalu lama di voice ya.\n"
                f"*Mau lanjut ngobrol? Tinggal join ulang aja, hitungannya mulai "
                f"dari nol lagi.* 👋"
            )
        except Exception:
            pass

    async def dm_keluar(self, member, channel, menit):
        try:
            await member.send(
                f"👋 Voice **{channel.name}** di server **{channel.guild.name}** udah "
                f"jalan **{menit} menit** (batas {label_durasi(VOICE_LIMIT_SECONDS)}), "
                f"jadi room-nya dikosongkan dan kamu ikut dikeluarkan.\n"
                "Jangan terlalu lama di voice ya — kamu bisa masuk lagi kapan saja."
            )
        except Exception:
            pass

    async def log_kosongkan(self, channel, menit, keluar, gagal):
        deskripsi = (
            f"**Room:** {channel.mention} (`{channel.name}`)\n"
            f"**Umur room:** {menit} menit (batas {label_durasi(VOICE_LIMIT_SECONDS)})\n"
            f"**Dikeluarkan ({len(keluar)}):** {ringkas_nama(keluar, lambda m: m.mention)}"
        )

        if gagal:
            rincian = "\n".join(f"• {m.mention} — {sebab}" for m, sebab in gagal[:MAX_TULIS_NAMA])
            deskripsi += (
                f"\n**Gagal dikeluarkan ({len(gagal)}):**\n{rincian}\n"
                f"⚠️ Room belum benar-benar kosong — hitungan di-reset dari sekarang."
            )

        await self.log_to_admin(
            f"🕐 Room Dikosongkan (Batas {label_durasi(VOICE_LIMIT_SECONDS)} Voice)",
            deskripsi,
            discord.Color.dark_red() if gagal else discord.Color.orange(),
        )

    # ----------------------------------------------------------------
    # COMMAND DIAGNOSA
    # ----------------------------------------------------------------
    @commands.command(name="voicecek")
    @commands.has_permissions(administrator=True)
    async def voicecek(self, ctx):
        """Lihat room voice mana saja yang umurnya sedang dihitung."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = discord.Embed(title="🕐 Batas Waktu Room Voice", color=discord.Color.blurple())
        embed.set_footer(
            text=f"Batas {label_durasi(VOICE_LIMIT_SECONDS)} per room "
                 f"• diingatkan {label_durasi(WARN_BEFORE_SECONDS)} sebelum dikosongkan "
                 f"• cek tiap {CHECK_INTERVAL_SECONDS} detik"
        )

        if not self.rooms:
            embed.description = "Nggak ada room voice aktif yang dibatasi."
        else:
            now = time.time()
            baris = []
            for channel_id, data in self.rooms.items():
                channel = self.bot.get_channel(channel_id)
                nama_room = channel.name if channel else f"ID: {channel_id}"
                jumlah = len(self.anggota_room(channel))

                umur = now - data["since"]
                menit = int(umur // 60)
                sisa = max(0, int((VOICE_LIMIT_SECONDS - umur) // 60))
                tanda = "⚠️ udah diingatkan" if data["warned"] else f"sisa ±{sisa} menit"
                baris.append(
                    f"🔊 **{nama_room}** — {jumlah} member — **{menit} menit** — {tanda}"
                )

            if len(baris) > 25:
                baris = baris[:25] + [f"*...dan {len(baris) - 25} room lainnya*"]
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
