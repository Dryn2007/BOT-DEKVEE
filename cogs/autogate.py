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
# 2. SISTEM AUTO-GATE (FULL OTOMATIS & ANTI MALING)
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

        # Batasi ke 12 RPM (bukan 15) sebagai safety margin,
        # supaya nggak mepet banget ke limit resmi Google (Free Tier = 15 RPM)
        self.gemini_limiter = RateLimiter(max_calls=12, period=60.0)

        # Mulai background task untuk sinkronisasi Web -> Discord
        self.sync_web_verification.start()

    def cog_unload(self):
        self.sync_web_verification.cancel()

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
                    username TEXT
                )
            ''')

            # Tambahkan kolom sync_discord ke tabel users jika belum ada
            try:
                await self.bot.pool.execute('''
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS sync_discord BOOLEAN DEFAULT false
                ''')
            except Exception as e:
                print(f"[DB WARN] Gagal alter table users (mungkin sudah ada atau bukan admin DB): {e}")

            self.is_ready = True
            print("✅ Tabel Keamanan SKL & Sync Discord siap!")

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

    # ==============================================================
    # FUNGSI SINKRONISASI VERIFIKASI DARI WEB KE DISCORD
    # ==============================================================
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

                # --- (Opsional) Sinkronisasi Nama Lengkap ke Nickname Discord ---
                if full_name:
                    clean_name = full_name.title()[:32]  # max nickname Discord = 32 char
                    if member.display_name != clean_name and member.id != guild.owner_id:
                        try:
                            await member.edit(nick=clean_name)
                            print(f"🔄 [WEB-SYNC] Mengubah nama {member.name} menjadi {clean_name}")
                        except discord.Forbidden:
                            pass  # Abaikan jika bot tidak punya izin ganti nama

        except Exception as e:
            print(f"❌ Error in sync_web_verification loop: {e}")

    @sync_web_verification.before_loop
    async def before_sync(self):
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
    async def send_halt_message(self, channel, member, is_retry=False):
        sapaan = "Ayo coba lagi" if is_retry else "Berhenti di situ"

        konten = (
            f"🚨 **HALT!** {sapaan}, {member.mention}!\n\n"
            f"Untuk masuk, **upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
            f"Atau verifikasi lebih cepat melalui **Website Resmi Telyu Jekardah!**\n"
            f"⚠️ **PENTING:** Pastikan **Nama Lengkap, Nomor Registrasi (11 Angka), "
            f"Prodi, Kampus Jakarta**, dan tahun **2026/2027** terlihat dengan jelas ya!\n\n"
            f"📄 **Link Drive di bawah ini CUMA buat LIHAT CONTOH format SKL yang valid, "
            f"BUKAN tempat upload ya:**\n"
            f"https://drive.google.com/drive/folders/157xVAUCZHl7PSMP-Zj4brYPwXDY9baXd?usp=sharing\n\n"
            f"Ssst... ruangan ini cuma buat upload gambar, jadi dilarang chat. "
            f"Langsung drop fotonya aja!"
        )

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

        asset_path = "assets/tutorial_upload.png"
        if not os.path.isfile(asset_path):
            print(f"❌ Asset '{asset_path}' tidak ditemukan. Halt message dikirim tanpa gambar tutorial.")
            await channel.send(content=konten)
            return

        file = discord.File(asset_path, filename="tutorial_upload.png")
        await channel.send(content=konten, embed=embed, file=file)

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
        if member.bot:
            return

        pos_satpam = self.bot.get_channel(self.pos_satpam_id)
        if pos_satpam is None:
            try:
                pos_satpam = await self.bot.fetch_channel(self.pos_satpam_id)
            except Exception as e:
                print(f"❌ Gagal fetch channel pos_satpam untuk {member}: {e}")
                return

        try:
            await self.send_halt_message(pos_satpam, member, is_retry=False)
        except Exception as e:
            print(f"❌ Gagal kirim halt message ke {member} saat join: {e}")

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
            record = await self.bot.pool.fetchrow(
                "SELECT username FROM skl_registry WHERE no_reg = $1", no_reg
            )
            if record and record['username'] != discord_username:
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

            # ==========================================================
            # DOKUMEN VALID — mulai proses pemberian role & simpan DB
            # ==========================================================
            nama_lengkap_skl = await self.ekstrak_nama_lengkap(hasil_mentah)

            # --- Simpan ke DB SELALU, tidak bergantung pada berhasil/tidaknya role ---
            # (Di versi lama semua ini ada di dalam `if role_prodi:` sehingga
            #  kalau role nggak ketemu, no_reg nggak pernah ke-lock dan
            #  SKL yang sama bisa dipakai berulang oleh akun lain.)
            try:
                await self.bot.pool.execute(
                    "INSERT INTO skl_registry (no_reg, username) VALUES ($1, $2) "
                    "ON CONFLICT (no_reg) DO NOTHING",
                    no_reg, discord_username
                )
                await self.bot.pool.execute(
                    "INSERT INTO maba_roles (username, role_name, full_name) "
                    "VALUES ($1, $2, $3) ON CONFLICT (username) DO UPDATE SET "
                    "role_name = EXCLUDED.role_name, full_name = EXCLUDED.full_name",
                    discord_username, role_key, nama_lengkap_skl
                )

                # Sinkronkan balik ke tabel users milik website
                discord_id_str = str(message.author.id)
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
                f"dokumen SKL lu lolos untuk prodi **{role_key}**. Cuss cek room welcome-center!"
            )
            await asyncio.sleep(5)
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
