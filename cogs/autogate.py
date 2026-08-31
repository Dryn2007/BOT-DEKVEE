import discord
from discord.ext import commands, tasks
import aiohttp
import os
import base64
import asyncio
import re
import time
from collections import deque

# Ambil API key dari .env
gemini_key = os.getenv("GEMINI_API_KEY")


# ==========================================
# 0. KONFIGURASI ROLE  <<< SATU-SATUNYA TEMPAT EDIT ID ROLE >>>
# ==========================================
# PENTING: role dicari pakai ID, BUKAN nama.
# Nama role gampang keubah/typo di Discord dan bikin verifikasi silent-fail.
ROLE_IDS = {
    "MEMBER":  1526575163166949447,                 # TODO: isi ID role MEMBER kamu
    "TEKINFO": 1526566212077879438,  # S1 Teknologi Informasi
    "DKV":     1526565350731284532,  # S1 Desain Komunikasi Visual
    "TEKTEL":  1526566818024783872,  # S1 Teknik Telekomunikasi
    "INFOR":   1538489249895292951,  # S1 Informatika
    "SISFOR":  1526566441040478352,                 # TODO: isi ID role Sistem Informasi
}

# Keyword prodi -> key di ROLE_IDS.
# Dicocokkan dengan word-boundary dan diurutkan dari keyword TERPANJANG,
# jadi singkatan 2 huruf hanya dipakai kalau tidak ada frasa panjang yang cocok.
PRODI_KEYWORDS = {
    "desain komunikasi visual": "DKV",
    "dkv": "DKV",

    "teknologi informasi": "TEKINFO",
    "tekinfo": "TEKINFO",

    "teknik informatika": "INFOR",
    "informatika": "INFOR",

    "sistem informasi": "SISFOR",
    "sisfor": "SISFOR",

    "teknik telekomunikasi": "TEKTEL",
    "telekomunikasi": "TEKTEL",
    "tektel": "TEKTEL",

    # Singkatan pendek — hanya jadi fallback terakhir karena diurutkan by length.
    "ti": "TEKINFO",
    "if": "INFOR",
    "si": "SISFOR",
    "tt": "TEKTEL",
}

# Diurutkan sekali saat import: keyword terpanjang dievaluasi lebih dulu.
_PRODI_SORTED = sorted(PRODI_KEYWORDS.items(), key=lambda kv: len(kv[0]), reverse=True)

# Nama prodi versi website (kolom users.prodi) -> key di ROLE_IDS
WEB_PRODI_TO_ROLE = {
    "Desain Komunikasi Visual": "DKV",
    "Teknologi Informasi": "TEKINFO",
    "Informatika": "INFOR",
    "Sistem Informasi": "SISFOR",
    "Teknik Telekomunikasi": "TEKTEL",
    "Umum": None,
}

# Kebalikannya, untuk nulis balik ke kolom users.prodi
ROLE_TO_WEB_PRODI = {v: k for k, v in WEB_PRODI_TO_ROLE.items() if v}


# ==========================================
# 0b. KONFIGURASI ROLE KELAS (dari website)
# ==========================================
# Format kelas (JS1DKV-26-REG-01) HANYA didefinisikan di web: lib/kelas.ts.
# Bot sengaja tidak menyalin daftar prefix per prodi — dia baca kolom `prodi`
# dari tabel `users` dan cuma butuh regex longgar untuk mengenali "ini role
# kelas". Jadi kalau ada tahun/nomor kelas baru, bot tidak perlu diedit.
#
# Yang perlu ditambah di sini HANYA kalau ada PRODI BARU: satu baris warna.
# Kalau lupa, role kelasnya tetap dibuat, cuma warnanya default (abu).
CLASS_ROLE_COLORS = {
    "Desain Komunikasi Visual": 0xD52AA2,
    "Sistem Informasi":         0x2AD55D,
    "Teknologi Informasi":      0xD5B22A,
    "Teknik Telekomunikasi":    0x2A4DD5,
    "Informatika":              0x992D22,
}
CLASS_ROLE_DEFAULT_COLOR = 0x99AAB5  # abu Discord — penanda prodi belum dipetakan

# Longgar dengan sengaja: prefix apa pun (JS1DKV/JS1SI/...) dan ekor apa pun
# (01, GAB01) diterima, karena validasi ketatnya sudah dilakukan web sebelum
# menulis ke DB. Regex ini dipakai untuk "role ini role kelas atau bukan?".
KELAS_RE = re.compile(r'^JS1[A-Z]{2,5}-\d{2}-REG-[A-Z0-9]{1,10}$')


# ==========================================
# 0c. JARING PENGAMAN PESAN HALT
# ==========================================
# Kenapa ini ada: `on_member_join` cuma sampai kalau bot SEDANG online. Worker
# di-restart tiap deploy dan gateway Discord kadang drop event saat resume, jadi
# member yang join tepat di celah itu nggak pernah dapat instruksi upload SKL
# sama sekali — room regis kelihatan kosong buat dia.
#
# Sweep di bawah ini nyusul member yang kelewat: cek siapa yang belum punya role
# MEMBER, join-nya masih baru, dan belum pernah disebut bot di room regis.
HALT_SWEEP_MIN_AGE = 120         # detik — kasih waktu on_member_join kerja dulu
HALT_SWEEP_MAX_AGE = 24 * 3600   # detik — jangan ungkit member lama
HALT_SWEEP_MAX_PER_RUN = 5       # batas kirim per putaran, biar room nggak kebanjiran
HALT_HISTORY_SCAN = 100          # jumlah pesan terakhir yang dicek buat anti-duplikat

# Dipakai untuk membaca ulang mention di pesan bot sendiri (<@123> / <@!123>)
MENTION_RE = re.compile(r'<@!?(\d+)>')


def resolve_prodi(teks: str):
    """
    Cari prodi dari teks OCR pakai word-boundary matching.

    Kenapa word-boundary: `"ti" in teks` itu True untuk "informaTIka",
    "serTIfikat", "idenTItas" — jadi anak Informatika bisa kebagian role
    Teknologi Informasi. Sama juga `"si"` yang cocok ke "regiStraSI",
    "viSIual", "univerSItas", dan `"if"` ke "veriFIkasi"/"sertIFikat".

    Return: (role_key, keyword_yang_cocok) atau (None, None)
    """
    for keyword, role_key in _PRODI_SORTED:
        # (?<!\w) / (?!\w) = word boundary yang aman untuk keyword multi-kata
        if re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', teks):
            return role_key, keyword
    return None, None


# ==========================================
# 1. RATE LIMITER (SLIDING WINDOW)
# ==========================================
class RateLimiter:
    """
    Sliding-window rate limiter.
    max_calls per period (detik). Kalau limit tercapai, request akan menunggu
    (await) sampai ada slot kosong, bukan langsung error.
    """

    def __init__(self, max_calls: int, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            # buang catatan panggilan yang sudah lewat dari periode (60 detik)
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()

            if len(self.calls) >= self.max_calls:
                # hitung berapa lama harus nunggu sampai slot pertama expired
                wait_time = self.period - (now - self.calls[0])
                if wait_time > 0:
                    print(f"⏳ Rate limit tercapai, menunggu {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                # bersihkan lagi setelah nunggu
                now = time.monotonic()
                while self.calls and now - self.calls[0] > self.period:
                    self.calls.popleft()

            self.calls.append(time.monotonic())


# ==========================================
# 2. KONFIRMASI RESET KELAS (khusus admin)
# ==========================================
class ConfirmResetKelasView(discord.ui.View):
    """
    Konfirmasi dua langkah untuk `!resetkelas`.

    Kelas itu permanen buat user, jadi jangan sampai satu typo di chat langsung
    menghapus data. Role kelas TIDAK PERNAH dihapus dari server di sini — cuma
    dilepas dari member yang bersangkutan.
    """

    def __init__(self, cog, author: discord.User, discord_id: str,
                 kelas_lama: str, member: discord.Member | None):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.author = author
        self.discord_id = discord_id
        self.kelas_lama = kelas_lama
        self.member = member
        self.message = None  # diisi setelah pesan konfirmasi terkirim

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ Cuma yang menjalankan `!resetkelas` yang bisa konfirmasi.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content="⌛ Konfirmasi kedaluwarsa (60 detik). Tidak ada yang diubah.",
                    embed=None, view=self
                )
            except Exception:
                pass

    @discord.ui.button(label="Ya, reset kelasnya", style=discord.ButtonStyle.danger, emoji="🔓")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        for item in self.children:
            item.disabled = True

        # 1. Kosongkan kolom kelas. sync_kelas = false supaya kalau user memilih
        #    ulang, loop sync langsung memprosesnya lagi.
        try:
            status = await self.cog.bot.pool.execute(
                "UPDATE users SET kelas = NULL, sync_kelas = false WHERE discord_id = $1",
                self.discord_id
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal update database: {e}")
            self.stop()
            return

        if isinstance(status, str) and status.strip().endswith(" 0"):
            await interaction.followup.send(
                f"⚠️ Tidak ada baris `users` dengan discord_id `{self.discord_id}` — "
                f"user ini belum ada di database website."
            )
            self.stop()
            return

        # 2. Lepas role kelas dari member (role-nya sendiri dibiarkan hidup)
        dicabut = []
        catatan = []
        if self.member:
            roles_kelas = self.cog.class_roles_of(self.member)
            if roles_kelas:
                try:
                    await self.member.remove_roles(
                        *roles_kelas, reason=f"!resetkelas oleh {self.author}"
                    )
                    dicabut = [r.name for r in roles_kelas]
                except discord.Forbidden:
                    catatan.append("Bot ditolak Discord saat mencabut role kelas (cek hierarki).")
                except discord.HTTPException as e:
                    catatan.append(f"Discord API error saat mencabut role: {e}")
        else:
            catatan.append("Member tidak ada di server, jadi role-nya nggak bisa dicabut sekarang.")

        embed = discord.Embed(
            title="🔓 Kelas Berhasil Direset",
            color=discord.Color.green()
        )
        embed.add_field(name="Discord ID", value=f"`{self.discord_id}`", inline=False)
        embed.add_field(name="Kelas lama", value=f"`{self.kelas_lama}`", inline=False)
        embed.add_field(
            name="Role dicabut dari member",
            value=", ".join(f"`{n}`" for n in dicabut) if dicabut else "— (tidak ada)",
            inline=False
        )
        embed.add_field(
            name="Langkah selanjutnya",
            value=("User bisa buka **/profile** di website dan memilih kelas lagi. "
                   "Setelah disimpan, kelasnya terkunci lagi dan role barunya nempel "
                   "otomatis dalam ≤1 menit."),
            inline=False
        )
        if catatan:
            embed.add_field(name="⚠️ Catatan", value="\n".join(catatan), inline=False)
        embed.set_footer(text="Role kelas TIDAK dihapus dari server — hapus manual kalau memang kosong.")

        await interaction.followup.send(embed=embed)
        try:
            await interaction.message.edit(view=self)  # matikan tombolnya
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="✖️ Reset kelas dibatalkan. Tidak ada yang diubah.",
            embed=None, view=self
        )
        self.stop()

# ==========================================
# 3. SISTEM AUTO-GATE (FULL OTOMATIS & ANTI MALING)
# ==========================================
class AutoGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # MASUKKAN ID ROOM MASING-MASING DI SINI
        self.pos_satpam_id = 1526900951678587013
        self.pengumuman_id = 1526219303714820186

        # ID Server (Guild ID)
        self.guild_id = 1522059025485664326

        self.warned_users = set()
        self.is_ready = False

        # Member yang sudah pernah di-DM instruksi gerbang (per sesi bot).
        # Tanpa ini, member yang lewat Rules Screening bisa dapat DM dobel:
        # sekali saat join (masih pending), sekali lagi saat setuju rules.
        self._dm_gate_terkirim = set()

        # Batasi ke 12 RPM (bukan 15) sebagai safety margin,
        # supaya nggak mepet banget ke limit resmi Google (Free Tier = 15 RPM)
        self.gemini_limiter = RateLimiter(max_calls=12, period=60.0)


        # Role kelas: cache {nama_kelas: role_id} + lock supaya dua baris DB
        # dengan kelas yang sama tidak pernah bikin dua role kembar.
        self._class_role_cache = {}
        self._class_role_lock = asyncio.Lock()

    def cog_unload(self):
        self.sync_web_verification.cancel()
        self.sync_web_kelas.cancel()
        self.sweep_halt_terlewat.cancel()

    # ==============================================================
    # HELPER ROLE — semua pencarian role lewat sini
    # ==============================================================
    def get_role(self, guild: discord.Guild, role_key: str):
        """
        Ambil role dari ROLE_IDS by ID. Return None + log jelas kalau gagal,
        supaya penyebabnya kelihatan di console dan bukan silent-fail.
        """
        role_id = ROLE_IDS.get(role_key)
        if role_id is None:
            print(f"⚠️ [ROLE] '{role_key}' belum diisi ID-nya di ROLE_IDS. Dilewati.")
            return None

        role = guild.get_role(role_id)
        if role is None:
            print(f"❌ [ROLE] '{role_key}' (ID {role_id}) tidak ada di server "
                  f"'{guild.name}'. Role terhapus atau ID salah?")
            return None
        return role

    async def assign_roles(self, member: discord.Member, role_keys):
        """
        Tambahkan role ke member dengan penanganan error eksplisit.

        Return: (list_role_berhasil, pesan_error_atau_None)

        Kenapa ini penting: `await member.add_roles(x)` yang gagal karena
        hierarki akan melempar discord.Forbidden. Di kode lama exception itu
        kabur ke `except Exception` paling luar dan cuma muncul sebagai
        "sistem pusing", jadi penyebab aslinya nggak pernah kelihatan.
        """
        guild = member.guild
        to_add = []
        missing = []

        for key in role_keys:
            role = self.get_role(guild, key)
            if role is None:
                missing.append(key)
                continue
            if role in member.roles:
                continue  # sudah punya, skip
            to_add.append(role)

        if not to_add:
            if missing:
                return [], f"Role belum terkonfigurasi: {', '.join(missing)}"
            return [], None

        # Cek hierarki dulu supaya errornya informatif, bukan Forbidden mentah
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return [], "Bot tidak punya permission **Manage Roles**."

        too_high = [r.name for r in to_add if r >= me.top_role]
        if too_high:
            return [], (f"Role {', '.join(too_high)} posisinya **di atas** role bot "
                        f"('{me.top_role.name}') di Server Settings → Roles. "
                        f"Geser role bot ke paling atas.")

        try:
            await member.add_roles(*to_add, reason="AutoGate: verifikasi SKL")
        except discord.Forbidden:
            return [], "Bot ditolak Discord (Forbidden) saat menambah role."
        except discord.HTTPException as e:
            return [], f"Discord API error saat menambah role: {e}"

        granted = [r.name for r in to_add]
        print(f"✅ [ROLE] Diberikan [{', '.join(granted)}] ke {member.name}")

        if missing:
            return to_add, f"Sebagian role belum terkonfigurasi: {', '.join(missing)}"
        return to_add, None

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_ready:
            await self.bot.pool.execute('''
                CREATE TABLE IF NOT EXISTS skl_registry (
                    no_reg TEXT PRIMARY KEY,
                    username TEXT,
                    discord_id TEXT
                )
            ''')

            # Tabel yang sudah ada duluan belum punya kolom discord_id.
            # no_reg diikat ke Discord ID, bukan username: username Discord
            # bisa diganti kapan saja dan bikin pengecekan pemilik meleset.
            try:
                await self.bot.pool.execute('''
                    ALTER TABLE skl_registry ADD COLUMN IF NOT EXISTS discord_id TEXT
                ''')
            except Exception as e:
                print(f"[DB WARN] Gagal alter table skl_registry: {e}")

            # Tambahkan kolom sync_discord ke tabel users jika belum ada
            try:
                await self.bot.pool.execute('''
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS sync_discord BOOLEAN DEFAULT false
                ''')
            except Exception as e:
                print(f"[DB WARN] Gagal alter table users (mungkin sudah ada atau bukan admin DB): {e}")

            # Dipakai web (lockUserClass) sebagai pemicu pembuatan role kelas
            try:
                await self.bot.pool.execute('''
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS sync_kelas BOOLEAN DEFAULT false
                ''')
            except Exception as e:
                print(f"[DB WARN] Gagal alter table users untuk sync_kelas: {e}")

            self.is_ready = True
            print("✅ Tabel Keamanan SKL & Sync Discord siap!")

            # Mulai background task setelah bot siap
            if not self.sync_web_verification.is_running():
                self.sync_web_verification.start()
            if not self.sync_web_kelas.is_running():
                self.sync_web_kelas.start()
            if not self.sweep_halt_terlewat.is_running():
                self.sweep_halt_terlewat.start()

            # Audit konfigurasi role saat startup — ketahuan langsung kalau ada yang salah
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                for key in ROLE_IDS:
                    self.get_role(guild, key)

    # ==============================================================
    # COMMAND DIAGNOSA — jalankan ini kalau role masih nggak masuk
    # ==============================================================
    @commands.command(name="cekrole")
    @commands.has_permissions(administrator=True)
    async def cekrole(self, ctx):
        """Laporan kesehatan konfigurasi role + hierarki bot."""
        guild = ctx.guild
        me = guild.me

        lines = []
        can_manage = me.guild_permissions.manage_roles
        lines.append(f"{'✅' if can_manage else '❌'} Permission **Manage Roles**: {can_manage}")
        lines.append(f"📍 Role tertinggi bot: **{me.top_role.name}** (posisi {me.top_role.position})")
        lines.append("")

        for key, rid in ROLE_IDS.items():
            if rid is None:
                lines.append(f"⚠️ `{key}` — ID belum diisi di ROLE_IDS")
                continue
            role = guild.get_role(rid)
            if role is None:
                lines.append(f"❌ `{key}` — ID `{rid}` tidak ditemukan di server")
            elif role >= me.top_role:
                lines.append(f"🔒 `{key}` — **{role.name}** ada, tapi posisinya "
                             f"({role.position}) di atas/sama dengan role bot → bot nggak bisa kasih")
            else:
                lines.append(f"✅ `{key}` — **{role.name}** (posisi {role.position}) siap dipakai")

        embed = discord.Embed(
            title="🔧 Diagnosa Konfigurasi Role AutoGate",
            description="\n".join(lines),
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Kalau ada 🔒 atau ❌, itu penyebab role prodi nggak masuk.")
        await ctx.send(embed=embed)

    @commands.command(name="cekkelas")
    @commands.has_permissions(administrator=True)
    async def cekkelas(self, ctx):
        """Diagnosa role kelas + antrean sinkronisasi. Read-only, tidak mengubah apa pun."""
        guild = ctx.guild
        me = guild.me

        roles_kelas = sorted(
            (r for r in guild.roles if KELAS_RE.match(r.name)),
            key=lambda r: r.name
        )

        lines = [
            f"{'✅' if me.guild_permissions.manage_roles else '❌'} Permission **Manage Roles**: "
            f"{me.guild_permissions.manage_roles}",
            f"📊 Total role di server: **{len(guild.roles)}**/250 "
            f"(role kelas: **{len(roles_kelas)}**)",
            "",
        ]

        if not roles_kelas:
            lines.append("_Belum ada role kelas sama sekali._")
        else:
            kosong = 0
            for role in roles_kelas:
                jumlah = len(role.members)
                if jumlah == 0:
                    kosong += 1
                    lines.append(f"🕳️ `{role.name}` — **kosong**, aman dihapus manual")
                elif role >= me.top_role:
                    lines.append(f"🔒 `{role.name}` — {jumlah} anggota, tapi posisinya "
                                 f"di atas role bot → bot nggak bisa kelola")
                else:
                    lines.append(f"✅ `{role.name}` — {jumlah} anggota")
            if kosong:
                lines.append(f"\n🕳️ **{kosong}** role kelas kosong. "
                             f"Bot sengaja tidak pernah menghapus role — hapus sendiri kalau perlu.")

        # --- Sisi database ---
        db_lines = []
        try:
            pending = await self.bot.pool.fetch("""
                SELECT discord_id, kelas FROM users
                WHERE kelas IS NOT NULL AND sync_kelas = false AND discord_id IS NOT NULL
            """)
            if pending:
                db_lines.append(f"⏳ **{len(pending)}** baris masih menunggu sinkronisasi "
                                f"(`sync_kelas = false`):")
                for row in pending[:10]:
                    db_lines.append(f"• <@{row['discord_id']}> → `{row['kelas']}`")
                if len(pending) > 10:
                    db_lines.append(f"• _…dan {len(pending) - 10} lagi_")
                db_lines.append("_Normal kalau baru dipilih (<1 menit) atau member belum join server._")
            else:
                db_lines.append("✅ Tidak ada baris yang nyangkut — semua kelas sudah tersinkron.")

            semua = await self.bot.pool.fetch(
                "SELECT discord_id, kelas FROM users WHERE kelas IS NOT NULL"
            )
            aneh = [r for r in semua if not KELAS_RE.match((r['kelas'] or "").strip().upper())]
            db_lines.append(f"\n🗂️ Total user berkelas: **{len(semua)}**")
            if aneh:
                db_lines.append(f"⚠️ **{len(aneh)}** kelas formatnya tidak dikenali "
                                f"(role-nya tidak akan dibuat):")
                for row in aneh[:10]:
                    db_lines.append(f"• <@{row['discord_id']}> → `{row['kelas']}`")
                if len(aneh) > 10:
                    db_lines.append(f"• _…dan {len(aneh) - 10} lagi_")
                db_lines.append("_Betulkan dengan `!resetkelas @user`, lalu user pilih ulang di web._")
        except Exception as e:
            db_lines.append(f"❌ Gagal baca database: {e}")

        embed = discord.Embed(
            title="🎓 Diagnosa Role Kelas",
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple()
        )
        embed.add_field(name="Database", value="\n".join(db_lines)[:1024], inline=False)
        embed.set_footer(text="Salah kelas? !resetkelas @user")
        await ctx.send(embed=embed)

    @commands.command(name="resetkelas")
    @commands.has_permissions(administrator=True)
    async def resetkelas(self, ctx, target: str = None):
        """
        Kosongkan kelas user supaya dia bisa memilih ulang di website.

        Menerima mention (`!resetkelas @user`) maupun Discord ID mentah
        (`!resetkelas 123456789012345678`) supaya user yang sudah keluar server
        tetap bisa dibetulkan.
        """
        if not target:
            await ctx.send("❌ Pakai: `!resetkelas @user` atau `!resetkelas <discord_id>`")
            return

        # Mention di Discord bentuknya <@123> atau <@!123>
        digits = re.sub(r'\D', '', target)
        if not digits:
            await ctx.send(f"❌ `{target}` bukan mention atau Discord ID yang valid.")
            return
        discord_id = digits

        try:
            row = await self.bot.pool.fetchrow(
                "SELECT kelas, prodi, username FROM users WHERE discord_id = $1", discord_id
            )
        except Exception as e:
            await ctx.send(f"❌ Gagal baca database: {e}")
            return

        if row is None:
            await ctx.send(f"⚠️ Discord ID `{discord_id}` belum ada di tabel `users` "
                           f"(user ini belum pernah login di website).")
            return

        if not row['kelas']:
            await ctx.send(f"ℹ️ <@{discord_id}> memang belum punya kelas — "
                           f"kartu pilih kelas di website sudah terbuka untuk dia.")
            return

        member = ctx.guild.get_member(int(discord_id))
        roles_kelas = self.class_roles_of(member) if member else []

        embed = discord.Embed(
            title="⚠️ Konfirmasi Reset Kelas",
            description=(
                f"Kelas <@{discord_id}> akan **dikosongkan** supaya dia bisa memilih ulang "
                f"di website. Aksi ini cuma bisa dijalankan admin."
            ),
            color=discord.Color.orange()
        )
        embed.add_field(name="User", value=f"<@{discord_id}> (`{row['username'] or discord_id}`)", inline=False)
        embed.add_field(name="Kelas sekarang", value=f"`{row['kelas']}`", inline=True)
        embed.add_field(name="Prodi", value=f"{row['prodi'] or '—'} _(tetap, tidak direset)_", inline=True)
        embed.add_field(
            name="Role yang akan dicabut dari member",
            value=", ".join(f"`{r.name}`" for r in roles_kelas) if roles_kelas
                  else ("— (tidak ada)" if member else "— (member tidak ada di server)"),
            inline=False
        )
        embed.set_footer(text="Role kelas TIDAK dihapus dari server, cuma dilepas dari member. Timeout 60 detik.")

        view = ConfirmResetKelasView(self, ctx.author, discord_id, row['kelas'], member)
        view.message = await ctx.send(embed=embed, view=view)

    @tasks.loop(minutes=1.0)
    async def sync_web_verification(self):
        """
        Background task yang berjalan setiap 1 menit.
        Mengecek tabel `users` dari website, jika is_verified = true,
        maka otomatis memberikan role di Discord (jika belum punya).
        """
        if not self.is_ready or not hasattr(self.bot, 'pool'):
            return

        try:
            records = await self.bot.pool.fetch("""
                SELECT discord_id, prodi, full_name
                FROM users
                WHERE is_verified = true AND sync_discord = false AND discord_id IS NOT NULL
            """)

            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            for record in records:
                discord_id_str = record['discord_id']
                try:
                    member_id = int(discord_id_str)
                except (ValueError, TypeError):
                    continue  # discord_id bukan angka

                member = guild.get_member(member_id)
                if not member:
                    continue  # Member sedang tidak ada di server / cache

                web_prodi = record['prodi']
                target_key = WEB_PRODI_TO_ROLE.get(web_prodi)
                full_name = record['full_name']

                # --- Berikan role (pakai ID, bukan nama) ---
                role_keys = ["MEMBER"]
                if target_key:
                    role_keys.append(target_key)

                granted, err = await self.assign_roles(member, role_keys)
                if err:
                    print(f"⚠️ [WEB-SYNC] {member.name}: {err}")

                # --- Simpan ke maba_roles ---
                if target_key:
                    try:
                        await self.bot.pool.execute(
                            "INSERT INTO maba_roles (username, role_name, full_name) "
                            "VALUES ($1, $2, $3) ON CONFLICT (username) DO UPDATE SET "
                            "role_name = EXCLUDED.role_name, full_name = EXCLUDED.full_name",
                            member.name, target_key, full_name
                        )
                        print(f"🔄 [WEB-SYNC] Disimpan ke maba_roles untuk {member.name}")
                    except Exception as e:
                        print(f"[DB ERROR] Gagal input ke maba_roles dari auto-sync: {e}")

                # --- Hapus notifikasi 'HALT' di pos_satpam jika ada ---
                pos_satpam = self.bot.get_channel(self.pos_satpam_id)
                if pos_satpam:
                    async for msg in pos_satpam.history(limit=50):
                        if msg.author == self.bot.user and member.mention in msg.content:
                            try:
                                await msg.delete()
                            except Exception:
                                pass

                # --- Kirim notif ke pengumuman ---
                pengumuman_channel = self.bot.get_channel(self.pengumuman_id)
                if pengumuman_channel is None:
                    try:
                        pengumuman_channel = await self.bot.fetch_channel(self.pengumuman_id)
                    except Exception:
                        pass

                if pengumuman_channel and target_key:
                    nama_tampil = full_name.split()[0] if full_name else member.display_name
                    embed_pengumuman = discord.Embed(
                        title="🎉 MAHASISWA BARU TELAH TIBA!",
                        description=(
                            f"Mari sambut **{nama_tampil}** ({member.mention}) dari prodi "
                            f"**{target_key}** yang baru aja lolos verifikasi gerbang utama via Web!\n"
                            f"Selamat bergabung di kampus, jangan lupa mampir ke kantin virtual!"
                        ),
                        color=discord.Color.gold()
                    )
                    embed_pengumuman.set_thumbnail(url=member.display_avatar.url)
                    try:
                        await pengumuman_channel.send(embed=embed_pengumuman)
                        print(f"✅ [WEB-SYNC] Berhasil mengirim pengumuman untuk {member.name}")
                    except Exception as e:
                        print(f"❌ [WEB-SYNC] Gagal kirim pengumuman: {e}")
                elif not pengumuman_channel:
                    print("⚠️ [WEB-SYNC] Pengumuman channel tidak ditemukan!")

                # --- TANDAI SUDAH DISINKRONISASI agar tidak diulang menit depan ---
                try:
                    await self.bot.pool.execute(
                        "UPDATE users SET sync_discord = true WHERE discord_id = $1",
                        discord_id_str
                    )
                except Exception as e:
                    print(f"[DB ERROR] Gagal update sync_discord flag: {e}")

                # =========================================================================
                # SINKRONISASI NAMA DI-DISABLED BERDASARKAN PERMINTAAN!
                # =========================================================================
                # if full_name:
                #     clean_name = full_name.title()[:32]  # max nickname Discord = 32 char
                #     if member.display_name != clean_name and member.id != guild.owner_id:
                #         try:
                #             await member.edit(nick=clean_name)
                #             print(f"🔄 [WEB-SYNC] Mengubah nama {member.name} menjadi {clean_name}")
                #         except discord.Forbidden:
                #             pass  # Abaikan jika bot tidak punya izin ganti nama

        except Exception as e:
            print(f"❌ Error in sync_web_verification loop: {e}")

    @sync_web_verification.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()

    # ==============================================================
    # ROLE KELAS — dibuat otomatis dari pilihan user di website
    # ==============================================================
    def class_roles_of(self, member: discord.Member):
        """Semua role kelas yang sedang dipakai member (dicocokkan pakai KELAS_RE)."""
        return [r for r in member.roles if KELAS_RE.match(r.name)]

    async def ensure_class_role(self, guild: discord.Guild, kelas: str, prodi: str):
        """
        Cari role kelas, buat kalau belum ada.

        Return: (role, pesan_error_atau_None)

        Lock + cache dipakai supaya dua user yang memilih kelas sama dalam satu
        putaran loop tidak menghasilkan dua role dengan nama identik (Discord
        mengizinkan nama role duplikat, jadi ini harus dijaga di sisi bot).
        """
        if not kelas or not KELAS_RE.match(kelas):
            return None, f"Format kelas `{kelas}` tidak dikenali, role tidak dibuat."

        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return None, "Bot tidak punya permission **Manage Roles**."

        async with self._class_role_lock:
            # 1. Cache dari pembuatan sebelumnya
            cached_id = self._class_role_cache.get(kelas)
            if cached_id:
                role = guild.get_role(cached_id)
                if role:
                    return role, None
                self._class_role_cache.pop(kelas, None)  # role sudah dihapus manual

            # 2. Role-nya mungkin sudah ada di server
            role = discord.utils.get(guild.roles, name=kelas)
            if role:
                self._class_role_cache[kelas] = role.id
                return role, None

            # 3. Belum ada → buat baru
            colour = CLASS_ROLE_COLORS.get(prodi)
            if colour is None:
                print(f"⚠️ [KELAS] Prodi '{prodi}' belum ada di CLASS_ROLE_COLORS, "
                      f"role '{kelas}' dibuat dengan warna default.")
                colour = CLASS_ROLE_DEFAULT_COLOR

            try:
                role = await guild.create_role(
                    name=kelas,
                    colour=discord.Colour(colour),
                    hoist=True,
                    mentionable=True,
                    reason="AutoGate: kelas dipilih user di website"
                )
            except discord.Forbidden:
                return None, "Bot ditolak Discord (Forbidden) saat membuat role kelas."
            except discord.HTTPException as e:
                # 30005 = Maximum number of guild roles reached (250)
                return None, f"Discord API error saat membuat role kelas: {e}"

            self._class_role_cache[kelas] = role.id
            print(f"🆕 [KELAS] Role '{kelas}' dibuat (warna #{colour:06X}, prodi {prodi})")

            # Taruh tepat di bawah role bot supaya bot tetap bisa mengelolanya.
            # Gagal reposisi bukan masalah fatal — role-nya sudah jadi.
            try:
                await role.edit(position=max(1, me.top_role.position - 1),
                                reason="AutoGate: rapikan posisi role kelas")
            except Exception as e:
                print(f"⚠️ [KELAS] Role '{kelas}' dibuat tapi gagal diposisikan: {e}")

            return role, None

    async def apply_class_role(self, member: discord.Member, kelas: str, prodi: str):
        """
        Pasang role kelas ke member, dan lepas role kelas lain yang masih nempel
        (satu member = satu kelas).

        Role yang dilepas TIDAK pernah dihapus dari server — anggota kelas lain
        masih memakainya, dan penghapusan role selalu keputusan admin.

        Return: (role, pesan_error_atau_None)
        """
        role, err = await self.ensure_class_role(member.guild, kelas, prodi)
        if role is None:
            return None, err

        me = member.guild.me
        if role >= me.top_role:
            return None, (f"Role '{role.name}' posisinya di atas/sejajar role bot "
                          f"('{me.top_role.name}'), bot nggak bisa memasangnya.")

        lama = [r for r in self.class_roles_of(member) if r.id != role.id]
        try:
            if lama:
                await member.remove_roles(*lama, reason="AutoGate: pindah kelas")
                print(f"➖ [KELAS] {member.name} dilepas dari "
                      f"[{', '.join(r.name for r in lama)}] (role tidak dihapus dari server)")
            if role not in member.roles:
                await member.add_roles(role, reason="AutoGate: kelas dari website")
                print(f"✅ [KELAS] Role '{role.name}' diberikan ke {member.name}")
        except discord.Forbidden:
            return None, "Bot ditolak Discord (Forbidden) saat memasang role kelas."
        except discord.HTTPException as e:
            return None, f"Discord API error saat memasang role kelas: {e}"

        return role, None

    @tasks.loop(minutes=1.0)
    async def sync_web_kelas(self):
        """
        Kembaran sync_web_verification untuk kelas.

        Web menandai `sync_kelas = false` begitu user mengunci kelasnya, lalu
        loop ini yang membuat/menemukan role kelas dan memasangnya. Flag hanya
        di-set true kalau role BENAR-BENAR terpasang, jadi member yang sedang
        tidak ada di server otomatis dicoba lagi menit depan.
        """
        if not self.is_ready or not hasattr(self.bot, 'pool'):
            return

        try:
            records = await self.bot.pool.fetch("""
                SELECT discord_id, kelas, prodi
                FROM users
                WHERE kelas IS NOT NULL AND sync_kelas = false AND discord_id IS NOT NULL
            """)

            if not records:
                return

            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            for record in records:
                discord_id_str = record['discord_id']
                kelas = (record['kelas'] or "").strip().upper()
                prodi = record['prodi']

                if not KELAS_RE.match(kelas):
                    print(f"⚠️ [KELAS-SYNC] '{kelas}' (discord_id={discord_id_str}) "
                          f"formatnya tidak dikenali, dilewati. Cek pakai !cekkelas.")
                    continue

                try:
                    member_id = int(discord_id_str)
                except (ValueError, TypeError):
                    continue

                member = guild.get_member(member_id)
                if not member:
                    continue  # belum join / tidak ada di cache → coba lagi menit depan

                role, err = await self.apply_class_role(member, kelas, prodi)
                if err:
                    print(f"⚠️ [KELAS-SYNC] {member.name}: {err}")
                    continue  # flag dibiarkan false supaya dicoba ulang

                try:
                    await self.bot.pool.execute(
                        "UPDATE users SET sync_kelas = true WHERE discord_id = $1",
                        discord_id_str
                    )
                except Exception as e:
                    print(f"[DB ERROR] Gagal update sync_kelas flag: {e}")

        except Exception as e:
            print(f"❌ Error in sync_web_kelas loop: {e}")

    @sync_web_kelas.before_loop
    async def before_sync_kelas(self):
        await self.bot.wait_until_ready()

    # ==============================================================
    # GEMINI API
    # ==============================================================
    async def panggil_gemini_api(self, prompt, image_data, mime_type):
        if not gemini_key:
            raise Exception("API Key Gemini belum terbaca!")

        await self.gemini_limiter.acquire()

        clean_key = gemini_key.strip()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-flash-lite-latest:generateContent?key={clean_key}")
        base64_image = base64.b64encode(image_data).decode('utf-8')

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": base64_image}}
                ]
            }],
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 429:
                    print("⚠️ Kena 429 dari Gemini, retry dalam 10 detik...")
                    await asyncio.sleep(10)
                    async with session.post(url, json=payload) as resp2:
                        if resp2.status != 200:
                            raise Exception(f"API Error {resp2.status} (setelah retry)")
                        data = await resp2.json()
                elif resp.status != 200:
                    raise Exception(f"API Error {resp.status}")
                else:
                    data = await resp.json()

                kandidat = data.get('candidates', [{}])[0]
                if kandidat.get('finishReason') in ['PROHIBITED_CONTENT', 'SAFETY']:
                    return "KODE_BLOKIR_SENSOR"
                try:
                    return kandidat['content']['parts'][0]['text']
                except KeyError:
                    raise Exception("Format JSON tidak terbaca.")

    async def ekstrak_nama_lengkap(self, hasil_mentah):
        """Panggil Gemini text-only untuk ambil nama lengkap. Return None kalau gagal."""
        try:
            prompt_nama = (
                "Dari teks berikut, ekstrak HANYA nama lengkap siswa/mahasiswa. "
                "Kembalikan HANYA nama lengkapnya saja, tanpa penjelasan lain.\n\n"
                f"Teks dokumen:\n{hasil_mentah}"
            )
            await self.gemini_limiter.acquire()
            clean_key = gemini_key.strip()
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-flash-lite-latest:generateContent?key={clean_key}")
            payload_nama = {"contents": [{"parts": [{"text": prompt_nama}]}]}

            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, json=payload_nama) as resp_nama:
                    if resp_nama.status != 200:
                        return None
                    data_nama = await resp_nama.json()

            raw_nama = (data_nama.get('candidates', [{}])[0]
                        .get('content', {})
                        .get('parts', [{}])[0]
                        .get('text', '')
                        .strip().strip('"\'').strip())

            if 3 <= len(raw_nama) <= 100:
                nama = raw_nama.title()
                print(f"📝 Nama lengkap SKL terdeteksi: {nama}")
                return nama
        except Exception as e:
            print(f"[WARN] Gagal extract nama lengkap: {e}")
        return None

    # ==============================================================
    # FUNGSI UNTUK MEMUNCULKAN ULANG PESAN SURUH UPLOAD
    # ==============================================================
    async def resolve_channel(self, channel_id: int):
        """Ambil channel dari cache, fallback ke REST. None kalau tetap gagal."""
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception as e:
            print(f"❌ [GATE] Channel {channel_id} tidak bisa diambil: {e}")
            return None

    def halt_text(self, member, is_retry=False):
        sapaan = "Ayo coba lagi" if is_retry else "Berhenti di situ"
        return (
            f"🚨 **HALT!** {sapaan}, {member.mention}!\n\n"
            f"Untuk masuk, **upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
            f"Atau verifikasi lebih cepat melalui **Website Resmi Telyu Jekardah!**\n"
            f"⚠️ **PENTING:** Pastikan **Nama Lengkap, Nomor Registrasi (11 Angka), "
            f"Prodi, Kampus Jakarta**, dan tahun **2026/2027** terlihat dengan jelas ya!\n\n"
            f"🎓 **Role kelas** (mis. `JS1DKV-26-REG-01`) didapat setelah kamu **memilih kelas "
            f"di website** — bot yang bikin & masangin role-nya otomatis. "
            f"Pilihannya **permanen**, jadi teliti dulu sebelum simpan.\n\n"
            f"📄 **Link Drive di bawah ini CUMA buat LIHAT CONTOH format SKL yang valid, "
            f"BUKAN tempat upload ya:**\n"
            f"https://drive.google.com/drive/folders/157xVAUCZHl7PSMP-Zj4brYPwXDY9baXd?usp=sharing\n\n"
            f"Ssst... ruangan ini cuma buat upload gambar, jadi dilarang chat. "
            f"Langsung drop fotonya aja!"
        )

    async def send_halt_message(self, channel, member, is_retry=False):
        """
        Kirim instruksi upload SKL ke room regis.

        Return True kalau pesannya benar-benar terkirim. Dulu fungsi ini tidak
        pernah melapor apa pun, jadi kegagalan permission (Attach Files / Embed
        Links / Send Messages dicabut di room regis) cuma jadi keheningan dan
        kelihatannya seperti "on_member_join nggak jalan".
        """
        konten = self.halt_text(member, is_retry)

        embed = discord.Embed(
            title="📲 Cara Upload SKL",
            description=(
                "1️⃣ Klik ikon **(+)** di pojok kiri bawah kolom chat (lihat gambar)\n"
                "2️⃣ Pilih **Upload a File**\n"
                "3️⃣ Cari & pilih foto SKL kamu dari galeri/file manager, lalu kirim"
            ),
            color=discord.Color.blurple()
        )
        embed.set_image(url="attachment://tutorial_upload.png")

        # Path asset dibikin absolut relatif ke file ini: kalau bot dijalankan
        # dari direktori lain (systemd/worker), path relatif "assets/..." meleset
        # dan gambar tutorial hilang tanpa sebab yang jelas.
        asset_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "tutorial_upload.png"
        )

        try:
            if os.path.isfile(asset_path):
                file = discord.File(asset_path, filename="tutorial_upload.png")
                await channel.send(content=konten, embed=embed, file=file)
            else:
                print(f"❌ [GATE] Asset '{asset_path}' tidak ada. "
                      f"Halt message dikirim tanpa gambar tutorial.")
                await channel.send(content=konten)
            print(f"📨 [GATE] Halt message terkirim ke #{getattr(channel, 'name', channel.id)} "
                  f"untuk {member} (retry={is_retry})")
            return True
        except discord.Forbidden:
            perms = channel.permissions_for(channel.guild.me) if getattr(channel, "guild", None) else None
            detail = ""
            if perms:
                kurang = [nama for nama, ada in (
                    ("View Channel", perms.view_channel),
                    ("Send Messages", perms.send_messages),
                    ("Embed Links", perms.embed_links),
                    ("Attach Files", perms.attach_files),
                ) if not ada]
                detail = f" Permission bot yang kurang di room itu: {', '.join(kurang) or '—'}."
            print(f"❌ [GATE] Ditolak Discord saat kirim halt message untuk {member}.{detail}")
        except discord.HTTPException as e:
            print(f"❌ [GATE] Discord API error saat kirim halt message untuk {member}: {e}")
        return False

    async def dm_halt_message(self, member, sekali=True):
        """
        Cadangan lewat DM.

        Room regis biasanya menutup **Read Message History** untuk member yang
        belum terverifikasi. Efeknya: pesan yang dikirim SEBELUM dia membuka room
        tidak pernah kelihatan olehnya (walau admin melihatnya dengan normal).
        DM memotong masalah itu. Gagal kirim DM bukan error fatal — banyak orang
        menutup DM dari server.

        `sekali=True` menjaga satu member cuma di-DM sekali per sesi bot, supaya
        join + Rules Screening + sweep tidak menumpuk jadi tiga DM identik.
        """
        if sekali and member.id in self._dm_gate_terkirim:
            return False
        self._dm_gate_terkirim.add(member.id)

        link = f"https://discord.com/channels/{self.guild_id}/{self.pos_satpam_id}"
        try:
            await member.send(
                f"👋 Halo **{member.display_name}**, selamat datang di server "
                f"**Telyu Jekardah**!\n\n"
                f"Sebelum bisa jalan-jalan di dalam server, kamu wajib verifikasi dulu: "
                f"**upload foto SKL** kamu di room regis 👉 {link}\n"
                f"⚠️ Pastikan **Nama Lengkap, Nomor Registrasi (11 angka), Prodi, "
                f"Kampus Jakarta**, dan tahun **2026/2027** kelihatan jelas di fotonya.\n\n"
                f"Kalau room-nya kelihatan kosong, tinggal langsung kirim fotonya aja ya — "
                f"instruksi lengkapnya nunggu di sana."
            )
            print(f"✉️ [GATE] DM instruksi terkirim ke {member}")
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"ℹ️ [GATE] DM ke {member} gagal (DM tertutup?): {e}")
            return False

    async def kirim_gate_lengkap(self, member, is_retry=False, paksa_dm=False):
        """Room regis + DM sekaligus. Dipakai oleh join, screening, dan sweep."""
        channel = await self.resolve_channel(self.pos_satpam_id)
        terkirim = False
        if channel is None:
            print(f"❌ [GATE] Room regis (ID {self.pos_satpam_id}) tidak ditemukan — "
                  f"cek ulang pos_satpam_id, room-nya kemungkinan sudah dihapus/diganti.")
        else:
            terkirim = await self.send_halt_message(channel, member, is_retry=is_retry)

        await self.dm_halt_message(member, sekali=not paksa_dm)
        return terkirim

    async def gagal(self, channel, member, pesan):
        """Kirim pesan gagal sementara, lalu tampilkan ulang instruksi upload."""
        err_msg = await channel.send(pesan)
        await asyncio.sleep(5)
        try:
            await err_msg.delete()
        except Exception:
            pass
        await self.send_halt_message(channel, member, is_retry=True)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot or member.guild.id != self.guild_id:
            return

        # Kalau server pakai Rules Screening ("Membership Screening"), member
        # masih `pending`: dia belum bisa lihat/kirim apa pun sampai centang
        # persetujuan rules. Kirim sekarang = pesannya hilang di riwayat sebelum
        # dia bisa membacanya, jadi ditunda ke on_member_update.
        if getattr(member, "pending", False):
            print(f"⏸️ [GATE] {member} masih pending Rules Screening. "
                  f"Instruksi upload ditunda sampai dia setuju rules.")
            await self.dm_halt_message(member)
            return

        await self.kirim_gate_lengkap(member, is_retry=False)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """
        Susulan untuk server yang mengaktifkan Rules Screening.

        Transisi pending True -> False artinya member baru saja menyetujui rules
        dan detik ini juga baru bisa melihat isi room. Itulah saat yang tepat
        untuk instruksi upload SKL muncul.
        """
        if after.bot or after.guild.id != self.guild_id:
            return
        if not (getattr(before, "pending", False) and not getattr(after, "pending", False)):
            return

        role_member = self.get_role(after.guild, "MEMBER")
        if role_member and role_member in after.roles:
            return  # sudah lolos verifikasi, nggak perlu digangguin

        print(f"▶️ [GATE] {after} selesai Rules Screening, kirim instruksi upload sekarang.")
        await self.kirim_gate_lengkap(after, is_retry=False)

    # ==============================================================
    # JARING PENGAMAN — nyusul member yang event join-nya kelewat
    # ==============================================================
    async def sudah_disebut_bot(self, channel, limit=HALT_HISTORY_SCAN):
        """
        Set berisi ID user yang sudah pernah disebut bot di riwayat room regis.
        Dipakai supaya sweep tidak mengirim instruksi dobel.
        """
        disebut = set()
        try:
            async for msg in channel.history(limit=limit):
                if msg.author.id != self.bot.user.id:
                    continue
                for uid in MENTION_RE.findall(msg.content or ""):
                    disebut.add(int(uid))
        except discord.Forbidden:
            print("⚠️ [SWEEP] Bot tidak punya **Read Message History** di room regis, "
                  "anti-duplikat tidak bisa dicek. Sweep dilewati.")
            return None
        except discord.HTTPException as e:
            print(f"⚠️ [SWEEP] Gagal baca riwayat room regis: {e}")
            return None
        return disebut

    def kandidat_belum_verif(self, guild):
        """Member baru (2 menit–24 jam) yang belum punya role MEMBER."""
        role_member = self.get_role(guild, "MEMBER")
        sekarang = discord.utils.utcnow()
        hasil = []
        for m in guild.members:
            if m.bot or getattr(m, "pending", False) or m.joined_at is None:
                continue
            if role_member and role_member in m.roles:
                continue
            umur = (sekarang - m.joined_at).total_seconds()
            if HALT_SWEEP_MIN_AGE <= umur <= HALT_SWEEP_MAX_AGE:
                hasil.append(m)
        return hasil

    @tasks.loop(minutes=10.0)
    async def sweep_halt_terlewat(self):
        """
        Setiap 10 menit: kirim instruksi upload ke member yang join-nya kelewat.

        Ini yang menutup lubang paling sering bikin "pesan peringatan nggak ada":
        bot lagi restart/deploy saat orangnya join, jadi event on_member_join-nya
        hilang dan nggak ada apa pun yang mengulangnya.
        """
        if not self.is_ready:
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        kandidat = self.kandidat_belum_verif(guild)
        if not kandidat:
            return

        channel = await self.resolve_channel(self.pos_satpam_id)
        if channel is None:
            return

        disebut = await self.sudah_disebut_bot(channel)
        if disebut is None:
            return

        terkirim = 0
        for member in sorted(kandidat, key=lambda m: m.joined_at):
            if member.id in disebut:
                continue
            if terkirim >= HALT_SWEEP_MAX_PER_RUN:
                print(f"⏭️ [SWEEP] Batas {HALT_SWEEP_MAX_PER_RUN} pesan/putaran tercapai, "
                      f"sisanya lanjut 10 menit lagi.")
                break
            print(f"🔁 [SWEEP] {member} join {member.joined_at:%d/%m %H:%M} UTC tapi belum "
                  f"pernah dapat instruksi upload — dikirim sekarang.")
            if await self.kirim_gate_lengkap(member, is_retry=False):
                terkirim += 1
            await asyncio.sleep(2)  # santai, jangan kena rate limit Discord

        if terkirim:
            print(f"✅ [SWEEP] {terkirim} member disusulkan instruksi upload SKL.")

    @sweep_halt_terlewat.before_loop
    async def before_sweep_halt(self):
        await self.bot.wait_until_ready()
        # Beri jeda supaya cache member sempat penuh (chunking) sebelum sweep pertama.
        await asyncio.sleep(30)

    # ==============================================================
    # DIAGNOSA GERBANG — jalankan kalau pesan HALT nggak muncul
    # ==============================================================
    @commands.command(name="cekgate")
    @commands.has_permissions(administrator=True)
    async def cekgate(self, ctx):
        """Laporan kesehatan pesan HALT di room regis (read-only)."""
        guild = ctx.guild
        me = guild.me
        lines = []

        intents_members = self.bot.intents.members
        lines.append(f"{'✅' if intents_members else '❌'} Intent **Server Members**: "
                     f"{intents_members}"
                     + ("" if intents_members else " → `on_member_join` NGGAK AKAN pernah jalan"))

        channel = await self.resolve_channel(self.pos_satpam_id)
        if channel is None:
            lines.append(f"❌ Room regis ID `{self.pos_satpam_id}` **tidak ditemukan** — "
                         f"room-nya terhapus/diganti? Betulkan `pos_satpam_id` di autogate.py")
        else:
            lines.append(f"✅ Room regis: {channel.mention} (`{channel.name}`)")

            p = channel.permissions_for(me)
            for nama, ada, catatan in (
                ("View Channel", p.view_channel, "bot nggak bisa lihat room"),
                ("Send Messages", p.send_messages, "pesan HALT gagal terkirim"),
                ("Embed Links", p.embed_links, "embed tutorial hilang"),
                ("Attach Files", p.attach_files, "gambar tutorial gagal, kirim jadi Forbidden"),
                ("Read Message History", p.read_message_history, "anti-duplikat & sweep mati"),
                ("Manage Messages", p.manage_messages, "chat nyasar nggak bisa dihapus"),
            ):
                lines.append(f"{'✅' if ada else '❌'} Bot — **{nama}**"
                             + ("" if ada else f" → {catatan}"))

            pe = channel.permissions_for(guild.default_role)
            lines.append("")
            lines.append(f"👥 @everyone — View Channel: {'✅' if pe.view_channel else '❌'}, "
                         f"Attach Files: {'✅' if pe.attach_files else '❌'}, "
                         f"Read Message History: {'✅' if pe.read_message_history else '❌'}")
            if not pe.read_message_history:
                lines.append("⚠️ **Read Message History ditutup untuk @everyone.** Pesan HALT "
                             "tetap terkirim (admin lihat), tapi user baru membuka room dalam "
                             "keadaan KOSONG. Nyalakan permission itu, atau andalkan DM cadangan.")
            if not pe.attach_files:
                lines.append("⚠️ **Attach Files ditutup untuk @everyone** — user nggak bisa "
                             "upload SKL sama sekali.")

        asset_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "tutorial_upload.png"
        )
        lines.append("")
        lines.append(f"{'✅' if os.path.isfile(asset_path) else '❌'} Asset tutorial: "
                     f"`{asset_path}`")

        screening = "MEMBER_VERIFICATION_GATE_ENABLED" in guild.features
        lines.append(f"{'⚠️' if screening else '✅'} Rules Screening: "
                     f"{'AKTIF — instruksi dikirim setelah member setuju rules' if screening else 'nonaktif'}")

        kandidat = self.kandidat_belum_verif(guild)
        belum = []
        if channel is not None and kandidat:
            disebut = await self.sudah_disebut_bot(channel)
            if disebut is not None:
                belum = [m for m in kandidat if m.id not in disebut]

        lines.append("")
        lines.append(f"🕵️ Member 24 jam terakhir yang belum punya role MEMBER: "
                     f"**{len(kandidat)}**")
        if belum:
            lines.append(f"⏳ Belum pernah dapat instruksi (bakal disusul sweep ≤10 menit): "
                         f"**{len(belum)}**")
            for m in belum[:10]:
                lines.append(f"• {m.mention} (join {m.joined_at:%d/%m %H:%M} UTC)")
        lines.append(f"🔄 Sweep berjalan: {'✅' if self.sweep_halt_terlewat.is_running() else '❌'}")

        embed = discord.Embed(
            title="🚧 Diagnosa Gerbang Verifikasi",
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Paksa kirim ulang ke satu orang: !kirimhalt @user")
        await ctx.send(embed=embed)

    @commands.command(name="kirimhalt")
    @commands.has_permissions(administrator=True)
    async def kirimhalt(self, ctx, member: discord.Member = None):
        """Paksa kirim ulang instruksi upload SKL ke satu member."""
        if member is None:
            await ctx.send("❌ Pakai: `!kirimhalt @user`")
            return
        ok = await self.kirim_gate_lengkap(member, is_retry=False, paksa_dm=True)
        await ctx.send(
            f"{'✅' if ok else '⚠️'} Instruksi upload untuk {member.mention} "
            f"{'terkirim ke room regis' if ok else 'GAGAL masuk room regis — cek `!cekgate`'} "
            f"(DM juga dicoba)."
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.channel.id != self.pos_satpam_id:
            return

        # Sudah punya role MEMBER? Tendang chat-nya.
        role_member = self.get_role(message.guild, "MEMBER")
        if role_member and role_member in message.author.roles:
            try:
                await message.delete()
            except Exception:
                pass
            peringatan_lolos = await message.channel.send(
                f"⚠️ **Eits {message.author.mention}, kamu kan udah lolos verifikasi!** "
                f"Nggak perlu upload SKL atau chat di sini lagi ya. "
                f"Cuss langsung beraktivitas di dalam server!"
            )
            await peringatan_lolos.delete(delay=5)
            return

        # Hanya terima gambar
        is_valid_image = False
        attachment = None
        if message.attachments:
            attachment = message.attachments[0]
            if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg']):
                is_valid_image = True

        if not is_valid_image:
            try:
                await message.delete()
            except Exception:
                pass
            if message.author.id not in self.warned_users:
                self.warned_users.add(message.author.id)
                peringatan = await message.channel.send(
                    f"⚠️ **Tahan {message.author.mention}!** Ruangan ini khusus buat "
                    f"**upload foto SKL** (jpg/png). Tolong jangan ngirim chat di mari ya.\n"
                    f"📲 Klik ikon **(+)** di pojok kiri bawah kolom chat buat upload fotonya."
                )
                await peringatan.delete(delay=5)
            return

        # Sapu bersih pesan bot sebelumnya
        async for msg in message.channel.history(limit=50):
            if msg.author == self.bot.user and message.author.mention in msg.content:
                try:
                    await msg.delete()
                except Exception:
                    pass

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200:
                        return
                    image_data = await resp.read()

            try:
                await message.delete()
            except Exception:
                pass

            nama_depan = message.author.display_name.split()[0]
            discord_username = message.author.name

            prompt = ("Salin seluruh teks yang ada di gambar ini dengan teliti. "
                      "Pastikan kamu membaca baris Nomor Registrasi (11 angka), "
                      "Program Studi, Tahun, dan Nama Kampus. Jangan berikan penjelasan.")

            hasil_mentah = await self.panggil_gemini_api(prompt, image_data, attachment.content_type)

            if "KODE_BLOKIR_SENSOR" in hasil_mentah:
                await self.gagal(
                    message.channel, message.author,
                    f"❌ **Waduh {nama_depan}, sistem Google pusing baca dokumenmu!** "
                    f"{message.author.mention}\n"
                    f"**SOLUSI:** Pastikan foto nggak blur dan teks kelihatan jelas. "
                    f"Coba upload ulang gambarnya!"
                )
                return

            teks = " ".join(hasil_mentah.lower().split())

            # --- Cek 11 Angka Nomor Registrasi ---
            match_noreg = re.search(r'\b\d{11}\b', teks)
            if not match_noreg:
                await self.gagal(
                    message.channel, message.author,
                    f"❌ **Verifikasi Gagal, {nama_depan}** {message.author.mention}.\n"
                    f"Sistem tidak bisa menemukan **11 Angka Nomor Registrasi** di fotomu! "
                    f"Pastikan bagian tersebut tidak terpotong atau blur."
                )
                return

            no_reg = match_noreg.group(0)

            # --- Cek Database (anti maling SKL) ---
            # Pemilik diikat ke Discord ID; username cuma dipakai untuk baris
            # warisan yang discord_id-nya masih NULL (belum ke-backfill).
            discord_id_str = str(message.author.id)
            record = await self.bot.pool.fetchrow(
                "SELECT username, discord_id FROM skl_registry WHERE no_reg = $1", no_reg
            )
            if record:
                if record['discord_id']:
                    is_owner = record['discord_id'] == discord_id_str
                else:
                    is_owner = (record['username'] or "").lower() == discord_username.lower()

                if not is_owner:
                    await self.gagal(
                        message.channel, message.author,
                        f"🚨 **PELANGGARAN TERDETEKSI!** {message.author.mention}\n"
                        f"Nomor registrasi **{no_reg}** sudah tertaut dengan akun Discord lain "
                        f"(`{record['username']}`). Kamu tidak bisa menggunakan Dokumen SKL "
                        f"milik orang lain!"
                    )
                    return

            # --- Cek Kampus, Tahun, Prodi ---
            syarat_kampus = "jakarta" in teks or "telkom university" in teks
            syarat_tahun = "2026" in teks
            role_key, keyword_cocok = resolve_prodi(teks)

            if not (syarat_kampus and syarat_tahun and role_key):
                kurang = []
                if not syarat_kampus:
                    kurang.append("Kampus Jakarta")
                if not syarat_tahun:
                    kurang.append("Tahun 2026")
                if not role_key:
                    kurang.append("Program Studi")
                await self.gagal(
                    message.channel, message.author,
                    f"❌ **Verifikasi Gagal, {nama_depan}** {message.author.mention}.\n"
                    f"Dokumen lu kurang lengkap nih! Yang nggak kebaca: "
                    f"**{', '.join(kurang)}**.\n"
                    f"Pastikan **Nama, Prodi, Kampus Jakarta, dan Tahun 2026/2027** "
                    f"benar-benar kelihatan di fotonya. Silakan upload ulang atau panggil Admin."
                )
                return

            print(f"🔎 Prodi terdeteksi: '{keyword_cocok}' → role key '{role_key}'")

            # --- Kelas sudah paten? Prodi ikut paten. ---
            # Prefix kelas (JS1DKV, JS1SI, ...) terikat ke prodi, jadi user yang
            # kelasnya sudah terkunci tidak boleh verifikasi ulang pakai SKL prodi
            # lain — aturannya sama persis dengan /api/verify di web.
            try:
                row_kelas = await self.bot.pool.fetchrow(
                    "SELECT kelas, prodi FROM users WHERE discord_id = $1", discord_id_str
                )
            except Exception as e:
                print(f"[DB ERROR] Gagal cek kelas terkunci: {e}")
                row_kelas = None

            if row_kelas and row_kelas['kelas']:
                kelas_terkunci = row_kelas['kelas']
                role_terkunci = WEB_PRODI_TO_ROLE.get(row_kelas['prodi'] or "")

                if role_terkunci is None:
                    # Kelas terkunci tapi kolom prodi kosong/asing — bot tidak bisa
                    # menyimpulkan prodinya sendiri (format kelas cuma ada di web).
                    # Web yang memulihkan kolom prodi dari prefix kelas saat verifikasi.
                    await self.gagal(
                        message.channel, message.author,
                        f"🔒 **Kelas kamu sudah terkunci di `{kelas_terkunci}`**, "
                        f"{message.author.mention}, tapi data prodi di database belum lengkap.\n"
                        f"Silakan verifikasi lewat **Website Resmi Telyu Jekardah** ya "
                        f"(di sana prodinya otomatis dipulihkan dari kelas kamu), "
                        f"atau minta **Admin** menjalankan `!resetkelas` kalau kelasnya keliru."
                    )
                    return

                if role_terkunci != role_key:
                    await self.gagal(
                        message.channel, message.author,
                        f"🔒 **Verifikasi ditolak, {nama_depan}** {message.author.mention}.\n"
                        f"Kelas kamu sudah terkunci di **{kelas_terkunci}** "
                        f"(**{row_kelas['prodi']}**), sedangkan SKL yang kamu upload "
                        f"prodinya **{ROLE_TO_WEB_PRODI.get(role_key, role_key)}**.\n"
                        f"Kelas itu permanen dan prodi ikut terkunci. Kalau ini keliru, "
                        f"minta **Admin** menjalankan `!resetkelas` dulu."
                    )
                    return

            # ==========================================================
            # DOKUMEN VALID — mulai proses pemberian role & simpan DB
            # ==========================================================
            nama_lengkap_skl = await self.ekstrak_nama_lengkap(hasil_mentah)

            # --- Simpan ke DB SELALU, tidak bergantung pada berhasil/tidaknya role ---
            # (Di versi lama semua ini ada di dalam `if role_prodi:` sehingga
            #  kalau role nggak ketemu, no_reg nggak pernah ke-lock dan
            #  SKL yang sama bisa dipakai berulang oleh akun lain.)
            try:
                # discord_id ikut disimpan supaya no_reg terikat ke akun, bukan
                # ke username yang bisa diganti. Untuk baris warisan (discord_id
                # NULL), backfill hanya boleh dilakukan oleh pemilik sahnya —
                # akun lain tidak bisa merebut no_reg orang.
                await self.bot.pool.execute(
                    """
                    INSERT INTO skl_registry (no_reg, username, discord_id)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (no_reg) DO UPDATE
                       SET discord_id = EXCLUDED.discord_id,
                           username   = EXCLUDED.username
                     WHERE skl_registry.discord_id IS NULL
                       AND lower(skl_registry.username) = lower(EXCLUDED.username)
                    """,
                    no_reg, discord_username, discord_id_str
                )
                await self.bot.pool.execute(
                    "INSERT INTO maba_roles (username, role_name, full_name) "
                    "VALUES ($1, $2, $3) ON CONFLICT (username) DO UPDATE SET "
                    "role_name = EXCLUDED.role_name, full_name = EXCLUDED.full_name",
                    discord_username, role_key, nama_lengkap_skl
                )

                # Sinkronkan balik ke tabel users milik website
                web_prodi = ROLE_TO_WEB_PRODI.get(role_key, "Umum")
                status = await self.bot.pool.execute(
                    """
                    UPDATE users
                    SET is_verified = true, prodi = $1, full_name = $2, sync_discord = true
                    WHERE discord_id = $3
                    """,
                    web_prodi, nama_lengkap_skl, discord_id_str
                )
                # asyncpg mengembalikan string seperti 'UPDATE 0' kalau tidak ada baris kena
                if isinstance(status, str) and status.strip().endswith(" 0"):
                    print(f"ℹ️ [DB] Tidak ada baris `users` dengan discord_id={discord_id_str} "
                          f"(user ini belum pernah daftar di website). Ini normal.")
            except Exception as e:
                print(f"[DB ERROR] Gagal input ke database: {e}")

            # --- Berikan role (pakai ID, dengan error yang informatif) ---
            granted, err = await self.assign_roles(message.author, ["MEMBER", role_key])

            if err:
                print(f"❌ [VERIFIKASI] {discord_username}: {err}")
                await message.channel.send(
                    f"⚠️ **{nama_depan}, dokumenmu VALID tapi bot gagal kasih role.** "
                    f"{message.author.mention}\n"
                    f"Alasan: {err}\n"
                    f"Tolong panggil **Admin** ya, ini masalah setelan server bukan salahmu."
                )
                return

            acc_msg = await message.channel.send(
                f"✅ **Verifikasi Berhasil!** Halo **{nama_depan}** {message.author.mention}, "
                f"dokumen SKL lu lolos untuk prodi **{role_key}**. Cuss cek room welcome-center!\n"
                f"🎓 Mau dapet **role kelas** (mis. `JS1DKV-26-REG-01`)? Pilih kelasmu di "
                f"**Website Resmi Telyu Jekardah** — role-nya nempel otomatis dalam 1 menit. "
                f"Pilih dengan teliti ya, **kelas itu permanen** dan cuma bisa dibetulkan Admin."
            )
            await asyncio.sleep(15)
            try:
                await acc_msg.delete()
            except Exception:
                pass

            # --- Kirim pengumuman ---
            pengumuman_channel = self.bot.get_channel(self.pengumuman_id)
            if pengumuman_channel is None:
                try:
                    pengumuman_channel = await self.bot.fetch_channel(self.pengumuman_id)
                except Exception:
                    pass

            if pengumuman_channel:
                embed_pengumuman = discord.Embed(
                    title="🎉 MAHASISWA BARU TELAH TIBA!",
                    description=(
                        f"Mari sambut **{nama_depan}** ({message.author.mention}) dari prodi "
                        f"**{role_key}** yang baru aja lolos verifikasi gerbang utama!\n"
                        f"Selamat bergabung di kampus, jangan lupa mampir ke kantin virtual!"
                    ),
                    color=discord.Color.gold()
                )
                embed_pengumuman.set_thumbnail(url=message.author.display_avatar.url)
                try:
                    await pengumuman_channel.send(embed=embed_pengumuman)
                    print(f"✅ [DISCORD-UPLOAD] Berhasil mengirim pengumuman untuk {discord_username}")
                except Exception as e:
                    print(f"❌ [DISCORD-UPLOAD] Gagal kirim pengumuman: {e}")
            else:
                print("⚠️ [DISCORD-UPLOAD] Pengumuman channel tidak ditemukan!")

        except Exception as e:
            print(f"❌ [VERIFIKASI] Exception tak terduga: {type(e).__name__}: {e}")
            await self.gagal(
                message.channel, message.author,
                f"⚠️ Waduh, sistem pusing: {e}"
            )
        finally:
            try:
                await message.delete()
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        pengumuman_channel = self.bot.get_channel(self.pengumuman_id)
        if pengumuman_channel:
            embed_leave = discord.Embed(
                title="👋 Seseorang Telah Pergi...",
                description=(
                    f"Sayonara **{member.display_name}** ({member.name}) "
                    f"telah keluar dari server Telyu Jekardah."
                ),
                color=discord.Color.red()
            )
            embed_leave.set_thumbnail(url=member.display_avatar.url)
            await pengumuman_channel.send(embed=embed_leave)
            print(f"Member keluar: {member.name}")


async def setup(bot):
    await bot.add_cog(AutoGate(bot))
