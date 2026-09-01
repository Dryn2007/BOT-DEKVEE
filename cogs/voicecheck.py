import random
import re
import time

import discord
from discord.ext import commands, tasks

from roomconfig import LOG_CHANNEL_ID, is_private_call

# ====================================================================
# 0. KONFIGURASI (gampang di-tuning saat testing)
# ====================================================================
VOICE_LIMIT_SECONDS = 3600      # Lama nongkrong di voice sebelum ditanyai (1 jam)
ANSWER_WINDOW_SECONDS = 300     # Waktu buat jawab soalnya (5 menit)
REMIND_BEFORE_SECONDS = 120     # Diingatkan 2 menit sebelum dikeluarkan
CHECK_INTERVAL_SECONDS = 15     # Presisi peringatan/kick jadi ±15 detik
SKIP_ROOM_PRIVAT = True         # True = room privat nggak pernah ditanyai

# Angka pengecoh dibikin nempel sama jawaban benar biar nggak gampang ditebak
# dari bentuknya (bukan cuma "yang paling besar/paling kecil").
OFFSET_PENGECOH = [-12, -9, -7, -5, -3, -2, -1, 1, 2, 3, 5, 7, 9, 12]


def label_durasi(detik) -> str:
    """3600 -> '1 jam', 300 -> '5 menit', 5400 -> '1 jam 30 menit'."""
    jam, menit = divmod(int(detik) // 60, 60)
    if jam and menit:
        return f"{jam} jam {menit} menit"
    if jam:
        return f"{jam} jam"
    return f"{max(1, menit)} menit"


def buat_soal() -> dict:
    """Soal penjumlahan gampang + 3 pilihan pengecoh, urutannya diacak."""
    a = random.randint(3, 39)
    b = random.randint(3, 39)
    jawaban = a + b

    pilihan = {jawaban}
    while len(pilihan) < 4:
        kandidat = jawaban + random.choice(OFFSET_PENGECOH)
        if kandidat > 0:
            pilihan.add(kandidat)

    pilihan = list(pilihan)
    random.shuffle(pilihan)
    return {"a": a, "b": b, "jawaban": jawaban, "pilihan": pilihan}


# ====================================================================
# 1. VIEW TOMBOL PILIHAN JAWABAN
# ====================================================================
class SoalView(discord.ui.View):
    """Tombol pilihan jawaban, cuma bisa dipakai member yang ditanya.

    Keputusan kick TIDAK ada di sini — semua diputuskan loop pengawas di cog
    biar cuma ada satu sumber keputusan (timeout view di discord.py ikut
    ke-reset setiap kali tombolnya diklik).
    """

    def __init__(self, cog, member_id):
        super().__init__(timeout=ANSWER_WINDOW_SECONDS)
        self.cog = cog
        self.member_id = member_id
        self.pasang_tombol()

    def pasang_tombol(self):
        """Bikin ulang tombolnya dari soal yang sedang aktif di state cog."""
        self.clear_items()
        data = self.cog.tracked.get(self.member_id) or {}
        soal = data.get("soal")
        if not soal:
            return

        for nilai in soal["pilihan"]:
            tombol = discord.ui.Button(label=str(nilai), style=discord.ButtonStyle.secondary)
            tombol.callback = self._buat_callback(nilai)
            self.add_item(tombol)

    def _buat_callback(self, nilai):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.member_id:
                await interaction.response.send_message(
                    "❌ Soal ini bukan buat kamu.", ephemeral=True
                )
                return
            await self.cog.proses_jawaban_tombol(interaction, nilai)

        return callback

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        data = self.cog.tracked.get(self.member_id) or {}
        pesan = (data.get("soal") or {}).get("message")
        if pesan is None:
            return
        try:
            await pesan.edit(view=self)
        except Exception:
            pass


# ====================================================================
# 2. COG
# ====================================================================
class VoiceCheck(commands.Cog):
    """Cek "masih ada orangnya nggak?" buat yang nongkrong di voice > 1 jam.

    Alurnya: 1 jam di voice -> bot kirim soal penjumlahan (klik tombol atau
    ketik angkanya di chat) -> 2 menit sebelum batas waktu diingatkan ->
    kalau sampai batas waktu tetap nggak dijawab, langsung dikeluarkan dari
    voice (boleh join lagi kapan aja). Lolos soal = hitungan mulai dari nol,
    jadi ditanya lagi 1 jam berikutnya.

    Beda sama cog AfkKick: di sini nggak peduli mic/suara mati atau nggak,
    yang dihitung murni lama duduk di voice. Room privat dikecualikan
    (lihat SKIP_ROOM_PRIVAT).
    """

    def __init__(self, bot):
        self.bot = bot
        # member_id -> {"channel_id": int, "since": float, "soal": dict | None}
        self.tracked = {}
        self.is_started = False

    def cog_unload(self):
        self.voice_check_task.cancel()

    # ----------------------------------------------------------------
    # HELPER
    # ----------------------------------------------------------------
    def is_watched_channel(self, channel) -> bool:
        """VC yang ikut dipantau: semua kecuali room privat & AFK channel server."""
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

    def sedang_diurus_afkkick(self, member) -> bool:
        """True kalau cog AfkKick sudah memantau member ini.

        Biar dia nggak dapat peringatan AFK + soal verifikasi di menit yang
        sama; begitu mic/suaranya nyala lagi (keluar dari pantauan AFK),
        soalnya baru dikirim.
        """
        afk_cog = self.bot.get_cog("AfkKick")
        return bool(afk_cog and member.id in getattr(afk_cog, "afk_state", {}))

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
    # STATE
    # ----------------------------------------------------------------
    async def bersihkan_soal(self, data, teks_akhir=None):
        """Matikan soal yang sedang aktif (tombolnya + pesannya)."""
        soal = (data or {}).get("soal")
        if not soal:
            return

        data["soal"] = None
        view = soal.get("view")
        if view is not None:
            view.stop()

        pesan = soal.get("message")
        if pesan is None:
            return
        try:
            if teks_akhir:
                await pesan.edit(content=teks_akhir, view=None)
            else:
                await pesan.delete()
        except Exception:
            pass

    async def lupakan(self, member_id, teks_akhir=None):
        data = self.tracked.pop(member_id, None)
        await self.bersihkan_soal(data, teks_akhir)

    async def sync_member(self, member, voice_state):
        """Mulai / lanjutkan / hapus hitungan durasi voice member."""
        if not self.is_tracked(member, voice_state):
            await self.lupakan(member.id)
            return

        data = self.tracked.get(member.id)
        if data is None:
            self.tracked[member.id] = {
                "channel_id": voice_state.channel.id,
                "since": time.time(),
                "soal": None,
            }
            return

        if data["channel_id"] != voice_state.channel.id:
            # Pindah room: total durasi voice tetap dihitung (nggak di-reset),
            # tapi soal yang nyangkut di chat room lama dibatalkan — loop
            # pengawas bakal nanya ulang di room yang baru.
            data["channel_id"] = voice_state.channel.id
            await self.bersihkan_soal(data)
    # ----------------------------------------------------------------
    # EVENT
    # ----------------------------------------------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        # State nggak disimpan ke DB, jadi setelah restart hitungan dimulai
        # lagi dari bot ready.
        for member_id in list(self.tracked):
            await self.lupakan(member_id)

        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                if not self.is_watched_channel(vc):
                    continue
                for member in vc.members:
                    await self.sync_member(member, member.voice)

        if not self.is_started:
            self.is_started = True
            if not self.voice_check_task.is_running():
                self.voice_check_task.start()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        await self.sync_member(member, after)

    # ----------------------------------------------------------------
    # LOOP PENGAWAS
    # ----------------------------------------------------------------
    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def voice_check_task(self):
        now = time.time()

        for member_id, data in list(self.tracked.items()):
            channel = self.bot.get_channel(data["channel_id"])
            if not isinstance(channel, discord.VoiceChannel):
                await self.lupakan(member_id)
                continue

            member = channel.guild.get_member(member_id)
            # Baca state LIVE, jangan percaya cache internal cog
            if member is None or not self.is_tracked(member, member.voice):
                await self.lupakan(member_id)
                continue
            if member.voice.channel.id != data["channel_id"]:
                await self.sync_member(member, member.voice)
                continue

            soal = data.get("soal")
            if soal:
                if now >= soal["deadline"]:
                    await self.keluarkan(member, channel, data)
                elif not soal["diingatkan"] and now >= soal["deadline"] - REMIND_BEFORE_SECONDS:
                    soal["diingatkan"] = True
                    await self.kirim_ingatan(member, data)
            elif (now - data["since"]) >= VOICE_LIMIT_SECONDS:
                if self.sedang_diurus_afkkick(member):
                    continue
                await self.kirim_soal(member, channel, data)

    @voice_check_task.before_loop
    async def before_voice_check(self):
        await self.bot.wait_until_ready()
    # ----------------------------------------------------------------
    # KIRIM SOAL & PERINGATAN
    # ----------------------------------------------------------------
    def teks_soal(self, member, soal, channel, di_dm=False) -> str:
        lokasi = (
            f"voice **{channel.name}** (server **{channel.guild.name}**)"
            if di_dm else "voice ini"
        )
        return (
            f"🕐 {member.mention} kamu udah nongkrong di {lokasi} lebih dari "
            f"**{label_durasi(VOICE_LIMIT_SECONDS)}**. Masih di depan layar?\n\n"
            f"**{soal['a']} + {soal['b']} = ?**\n\n"
            f"Klik jawabannya di bawah, atau ketik angkanya di chat ini, dalam "
            f"**{label_durasi(ANSWER_WINDOW_SECONDS)}**. Kalau nggak dijawab kamu "
            f"otomatis dikeluarkan dari voice — santai, bisa join lagi kapan aja."
        )

    async def kirim_soal(self, member, channel, data):
        data["soal"] = {
            **buat_soal(),
            "deadline": time.time() + ANSWER_WINDOW_SECONDS,
            "diingatkan": False,
            "message": None,
            "channel": None,
            "channel_id": None,
            "view": None,
        }
        view = SoalView(self, member.id)      # tombolnya dibaca dari state di atas
        data["soal"]["view"] = view

        pesan = None
        try:
            pesan = await channel.send(
                self.teks_soal(member, data["soal"], channel),
                view=view,
                delete_after=ANSWER_WINDOW_SECONDS + 120,
            )
        except Exception:
            # Chat voice-nya nggak bisa dipakai (izin/dimatikan) -> coba lewat DM
            try:
                pesan = await member.send(
                    self.teks_soal(member, data["soal"], channel, di_dm=True), view=view
                )
            except Exception:
                pesan = None

        if pesan is None:
            # Nggak ada tempat buat nanya -> JANGAN dikeluarkan. Hitungan
            # dimulai ulang, admin dikabari biar izinnya dibenerin.
            await self.bersihkan_soal(data)
            data["since"] = time.time()
            await self.log_to_admin(
                "⚠️ Gagal Kirim Soal Verifikasi Voice",
                f"**Member:** {member.mention}\n**Room:** {channel.mention}\n"
                f"**Sebab:** chat voice nggak bisa dikirimi pesan dan DM-nya tertutup.\n"
                f"Member ini dilewati (nggak dikeluarkan), hitungan dimulai ulang.",
                discord.Color.dark_red(),
            )
            return

        data["soal"]["message"] = pesan
        data["soal"]["channel"] = pesan.channel
        data["soal"]["channel_id"] = pesan.channel.id

    async def kirim_ingatan(self, member, data):
        soal = data.get("soal")
        target = (soal or {}).get("channel")
        if not soal or target is None:
            return

        sisa = max(1, int(round((soal["deadline"] - time.time()) / 60)))
        try:
            await target.send(
                f"⏰ {member.mention} **{sisa} menit lagi** kamu dikeluarkan dari voice "
                f"kalau soalnya masih belum dijawab: **{soal['a']} + {soal['b']} = ?**\n"
                f"*Tombol jawabannya ada di pesan sebelumnya, atau ketik angkanya di sini.*",
                delete_after=REMIND_BEFORE_SECONDS + 60,
            )
        except Exception:
            pass
    # ----------------------------------------------------------------
    # KELUARKAN DARI VOICE
    # ----------------------------------------------------------------
    async def keluarkan(self, member, channel, data):
        soal = data.get("soal") or {}
        alasan = (
            f"Nggak jawab verifikasi voice (di voice > {label_durasi(VOICE_LIMIT_SECONDS)})"
        )

        try:
            await member.move_to(None, reason=alasan)
        except discord.Forbidden:
            await self.gagal_keluarkan(
                member, channel, data, "Bot tidak punya izin **Move Members**."
            )
            return
        except Exception as e:
            await self.gagal_keluarkan(member, channel, data, f"Error: `{e!r}`")
            return

        await self.bersihkan_soal(
            data,
            teks_akhir=(
                f"⌛ Waktu habis — {member.mention} dikeluarkan dari voice karena "
                f"soalnya nggak dijawab. Boleh join lagi kapan aja."
            ),
        )
        self.tracked.pop(member.id, None)

        try:
            await member.send(
                f"👋 Kamu dikeluarkan dari voice **{channel.name}** di server "
                f"**{channel.guild.name}** karena udah lebih dari "
                f"**{label_durasi(VOICE_LIMIT_SECONDS)}** di voice dan soal verifikasinya "
                f"nggak dijawab dalam {label_durasi(ANSWER_WINDOW_SECONDS)}.\n"
                "Kamu bisa masuk lagi kapan saja."
            )
        except Exception:
            pass

        await self.log_to_admin(
            "🕐 Member Dikeluarkan (Verifikasi Voice)",
            f"**Member:** {member.mention} (`{member}`)\n"
            f"**Room:** {channel.mention} (`{channel.name}`)\n"
            f"**Soal:** {soal.get('a')} + {soal.get('b')} = {soal.get('jawaban')}\n"
            f"**Sebab:** nggak dijawab dalam {label_durasi(ANSWER_WINDOW_SECONDS)}",
            discord.Color.orange(),
        )

    async def gagal_keluarkan(self, member, channel, data, sebab):
        """Kick gagal -> jangan spam tiap 15 detik: hitungan dimulai ulang."""
        await self.bersihkan_soal(data)
        data["since"] = time.time()
        await self.log_to_admin(
            "⚠️ Gagal Keluarkan dari Voice (Verifikasi)",
            f"**Member:** {member.mention}\n**Room:** {channel.mention}\n"
            f"**Alasan gagal:** {sebab}",
            discord.Color.dark_red(),
        )
    # ----------------------------------------------------------------
    # PROSES JAWABAN
    # ----------------------------------------------------------------
    def teks_lolos(self, member) -> str:
        return (
            f"✅ Mantap {member.mention}, jawabannya benar. Aman — nanti ditanya lagi "
            f"kalau kamu masih di voice {label_durasi(VOICE_LIMIT_SECONDS)} lagi."
        )

    def teks_soal_sekarang(self, member, data) -> str:
        soal = data["soal"]
        channel = self.bot.get_channel(data["channel_id"])
        if channel is None:
            return (
                f"**{soal['a']} + {soal['b']} = ?**\n"
                "Klik jawabannya di bawah atau ketik angkanya di chat ini."
            )
        return self.teks_soal(
            member, soal, channel, di_dm=soal.get("channel_id") != channel.id
        )

    def reset_setelah_lolos(self, data):
        """Lolos verifikasi: hitungan 1 jam mulai dari nol lagi."""
        soal = data.get("soal")
        data["soal"] = None
        data["since"] = time.time()
        view = (soal or {}).get("view")
        if view is not None:
            view.stop()
        return soal

    def acak_ulang_soal(self, data):
        """Angkanya diganti tiap kali salah, biar main tebak tombol nggak jalan."""
        soal = data["soal"]
        soal.update(buat_soal())
        view = soal.get("view")
        if view is not None:
            view.pasang_tombol()
        return soal

    async def proses_jawaban_tombol(self, interaction: discord.Interaction, nilai):
        data = self.tracked.get(interaction.user.id)
        soal = (data or {}).get("soal")

        if not soal:
            try:
                await interaction.response.edit_message(
                    content="⌛ Soal ini udah nggak aktif (mungkin bot habis restart). Kamu aman.",
                    view=None,
                )
            except Exception:
                pass
            return

        if nilai == soal["jawaban"]:
            self.reset_setelah_lolos(data)
            try:
                await interaction.response.edit_message(
                    content=self.teks_lolos(interaction.user), view=None
                )
            except Exception:
                pass
            return

        soal = self.acak_ulang_soal(data)
        try:
            await interaction.response.edit_message(
                content=self.teks_soal_sekarang(interaction.user, data), view=soal["view"]
            )
        except Exception:
            pass
        try:
            await interaction.followup.send(
                "❌ Salah. Soalnya diganti, coba lagi ya.", ephemeral=True
            )
        except Exception:
            pass
    @commands.Cog.listener()
    async def on_message(self, message):
        """Jawaban yang diketik langsung di chat (nggak semua orang mau klik tombol)."""
        if message.author.bot:
            return

        data = self.tracked.get(message.author.id)
        soal = (data or {}).get("soal")
        if not soal or message.channel.id != soal.get("channel_id"):
            return

        # Biarin command (!xxx 5) diproses cog lain, jangan dianggap jawaban
        prefix = getattr(self.bot, "command_prefix", "!")
        if isinstance(prefix, str) and message.content.startswith(prefix):
            return

        angka = re.search(r"-?\d+", message.content)
        if angka is None:
            return

        pesan_soal = soal.get("message")

        if int(angka.group()) == soal["jawaban"]:
            self.reset_setelah_lolos(data)
            if pesan_soal is not None:
                try:
                    await pesan_soal.edit(content=self.teks_lolos(message.author), view=None)
                except Exception:
                    pass
            try:
                await message.add_reaction("✅")
            except Exception:
                pass
            return

        self.acak_ulang_soal(data)
        if pesan_soal is not None:
            try:
                await pesan_soal.edit(
                    content=self.teks_soal_sekarang(message.author, data), view=soal["view"]
                )
            except Exception:
                pass
        try:
            await message.reply(
                "❌ Salah. Soalnya diganti, coba lagi ya.",
                delete_after=20,
                mention_author=False,
            )
        except Exception:
            pass
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

        embed = discord.Embed(title="🕐 Pemantauan Durasi Voice", color=discord.Color.blurple())
        embed.set_footer(
            text=f"Soal muncul setelah {label_durasi(VOICE_LIMIT_SECONDS)} "
                 f"• waktu jawab {label_durasi(ANSWER_WINDOW_SECONDS)} "
                 f"• diingatkan {label_durasi(REMIND_BEFORE_SECONDS)} sebelum out "
                 f"• cek tiap {CHECK_INTERVAL_SECONDS} detik"
        )

        if not self.tracked:
            embed.description = "Nggak ada member di voice channel yang dipantau."
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

                soal = data.get("soal")
                if soal:
                    sisa = max(0, int(soal["deadline"] - now))
                    tanda = f"❓ ditanya, sisa **{sisa} detik**"
                    if soal["diingatkan"]:
                        tanda += " (udah diingatkan)"
                else:
                    tanda = f"⏳ **{int((now - data['since']) // 60)} menit** di voice"

                baris.append(f"👤 **{nama}** — `{nama_room}` — {tanda}")

            if len(baris) > 25:
                baris = baris[:25] + [f"*...dan {len(baris) - 25} member lainnya*"]
            embed.description = "\n".join(baris)

        await ctx.send(embed=embed, delete_after=60)
    @commands.command(name="voicesoal")
    @commands.has_permissions(administrator=True)
    async def voicesoal(self, ctx, member: discord.Member = None):
        """Paksa munculkan soal verifikasi sekarang (buat ngetes tanpa nunggu 1 jam)."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        target = member or ctx.author
        voice_state = target.voice

        if not self.is_tracked(target, voice_state):
            await ctx.send(
                f"❌ **{target.display_name}** nggak sedang di voice channel yang dipantau.",
                delete_after=15,
            )
            return

        data = self.tracked.get(target.id)
        if data is None:
            data = {"channel_id": voice_state.channel.id, "since": time.time(), "soal": None}
            self.tracked[target.id] = data

        if data.get("soal"):
            await ctx.send("⚠️ Soalnya udah muncul, tunggu dijawab dulu.", delete_after=15)
            return

        await self.kirim_soal(target, voice_state.channel, data)
        await ctx.send(
            f"✅ Soal dikirim buat {target.mention} di {voice_state.channel.mention}.",
            delete_after=15,
        )

    @voicecek.error
    @voicesoal.error
    async def voicecheck_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Command ini hanya untuk Administrator.", delete_after=10)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ Member-nya nggak ketemu.", delete_after=10)
        else:
            raise error


async def setup(bot):
    await bot.add_cog(VoiceCheck(bot))
