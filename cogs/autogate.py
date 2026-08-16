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

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_ready:
            await self.bot.pool.execute('''
                CREATE TABLE IF NOT EXISTS skl_registry (
                    no_reg TEXT PRIMARY KEY,
                    username TEXT
                )
            ''')
            # (BARU) Tambahkan kolom sync_discord ke tabel users jika belum ada
            try:
                await self.bot.pool.execute('''
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS sync_discord BOOLEAN DEFAULT false
                ''')
            except Exception as e:
                print(f"[DB WARN] Gagal alter table users (mungkin sudah ada atau bukan admin DB): {e}")

            self.is_ready = True
            print("✅ Tabel Keamanan SKL & Sync Discord siap!")

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
        # Tunggu sampai bot benar-benar siap dan pool DB tersedia
        if not self.is_ready or not hasattr(self.bot, 'pool'):
            return

        try:
            # Cari user yang sudah verifikasi di web DAN belum pernah disinkronkan ke discord (sync_discord = false)
            records = await self.bot.pool.fetch("""
                SELECT discord_id, prodi, full_name 
                FROM users 
                WHERE is_verified = true AND sync_discord = false AND discord_id IS NOT NULL
            """)

            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return

            role_member = discord.utils.get(guild.roles, name="MEMBER")

            # Mapping nama prodi di Web -> nama role di Discord
            role_mapping = {
                "Desain Komunikasi Visual": "DKV",
                "Teknologi Informasi": "TI",
                "Informatika": "INFOR",
                "Sistem Informasi": "SISFOR",
                "Teknik Telekomunikasi": "TEKTEL",
                "Umum": None # Kalau belum pilih kelas/prodi, minimal dapat MEMBER
            }

            for record in records:
                discord_id_str = record['discord_id']
                try:
                    member_id = int(discord_id_str)
                    member = guild.get_member(member_id)
                    
                    if not member:
                        continue # Member sedang tidak ada di server / cache

                    # 1. Cek apakah sudah punya role MEMBER
                    has_member_role = role_member in member.roles if role_member else False
                    
                    # 2. Tentukan target role prodi berdasarkan data Web
                    web_prodi = record['prodi']
                    target_role_name = role_mapping.get(web_prodi)
                    
                    target_role = discord.utils.get(guild.roles, name=target_role_name) if target_role_name else None
                    has_prodi_role = target_role in member.roles if target_role else False

                    # 3. Kumpulkan role yang harus ditambahkan
                    roles_to_add = []
                    if role_member and not has_member_role:
                        roles_to_add.append(role_member)
                    if target_role and not has_prodi_role:
                        roles_to_add.append(target_role)

                    full_name = record['full_name']

                    # 4. Berikan role (jika ada yang kurang)
                    if roles_to_add:
                        try:
                            await member.add_roles(*roles_to_add)
                            role_names = ", ".join([r.name for r in roles_to_add])
                            print(f"🔄 Auto-Sync (Web->DC): Memberikan role [{role_names}] ke {member.name}")
                        except discord.Forbidden:
                            print(f"⚠️ Missing permission to add roles to {member.name}")

                    # 4.5. ALWAYS Send notification and save to maba_roles for newly verified web users
                    if target_role_name:
                        try:
                            await self.bot.pool.execute(
                                "INSERT INTO maba_roles (username, role_name, full_name) VALUES ($1, $2, $3) ON CONFLICT (username) DO UPDATE SET role_name = EXCLUDED.role_name, full_name = EXCLUDED.full_name",
                                member.name, target_role_name, full_name
                            )
                            print(f"🔄 Auto-Sync (Web->DC): Disimpan ke maba_roles untuk {member.name}")
                        except Exception as e:
                            print(f"[DB ERROR] Gagal input ke maba_roles dari auto-sync: {e}")

                    # Hapus Notifikasi 'HALT' di pos_satpam jika ada
                    pos_satpam = self.bot.get_channel(self.pos_satpam_id)
                    if pos_satpam:
                        async for msg in pos_satpam.history(limit=50):
                            if msg.author == self.bot.user and member.mention in msg.content:
                                try:
                                    await msg.delete()
                                except:
                                    pass
                        
                    # Kirim notif ke pengumuman
                    pengumuman_channel = self.bot.get_channel(self.pengumuman_id)
                    if pengumuman_channel is None:
                        try: pengumuman_channel = await self.bot.fetch_channel(self.pengumuman_id)
                        except: pass
                    
                    if pengumuman_channel and target_role_name:
                        embed_pengumuman = discord.Embed(
                            title="🎉 MAHASISWA BARU TELAH TIBA!",
                            description=f"Mari sambut **{full_name.split()[0] if full_name else member.display_name}** ({member.mention}) dari prodi **{target_role_name}** yang baru aja lolos verifikasi gerbang utama via Web!\nSelamat bergabung di kampus, jangan lupa mampir ke kantin virtual!",
                            color=discord.Color.gold()
                        )
                        embed_pengumuman.set_thumbnail(url=member.display_avatar.url)
                        try:
                            await pengumuman_channel.send(embed=embed_pengumuman)
                            print(f"✅ [WEB-SYNC] Berhasil mengirim pengumuman untuk {member.name}")
                        except Exception as e:
                            print(f"❌ [WEB-SYNC] Gagal kirim pengumuman: {e}")
                    else:
                        print(f"⚠️ [WEB-SYNC] Pengumuman channel tidak ditemukan atau role kosong!")

                    # TANDAI SEBAGAI SUDAH DISINKRONISASI agar tidak diulang menit depan!
                    try:
                        await self.bot.pool.execute(
                            "UPDATE users SET sync_discord = true WHERE discord_id = $1", 
                            discord_id_str
                        )
                    except Exception as e:
                        print(f"[DB ERROR] Gagal update sync_discord flag: {e}")

                    # 5. (Opsional) Sinkronisasi Nama Lengkap ke Nickname Discord
                    if full_name:
                        # Maksimal panjang nickname discord adalah 32 karakter
                        clean_name = full_name.title()[:32]
                        if member.display_name != clean_name and member.id != guild.owner_id:
                            try:
                                await member.edit(nick=clean_name)
                                print(f"🔄 Auto-Sync (Web->DC): Mengubah nama {member.name} menjadi {clean_name}")
                            except discord.Forbidden:
                                pass # Abaikan jika bot tidak punya izin ganti nama

                except ValueError:
                    pass # discord_id bukan angka

        except Exception as e:
            print(f"❌ Error in sync_web_verification loop: {e}")

    @sync_web_verification.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()

    async def panggil_gemini_api(self, prompt, image_data, mime_type):
        if not gemini_key:
            raise Exception("API Key Gemini belum terbaca!")

        # Tunggu slot rate limit sebelum request ke Gemini
        await self.gemini_limiter.acquire()

        clean_key = gemini_key.strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent?key={clean_key}"
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
                    # Kena rate limit dari Google, tunggu lalu retry sekali
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

    # ==============================================================
    # FUNGSI UNTUK MEMUNCULKAN ULANG PESAN SURUH UPLOAD
    # ==============================================================
    async def send_halt_message(self, channel, member, is_retry=False):
        sapaan = "Ayo coba lagi" if is_retry else "Berhenti di situ"

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

        # Pastikan path gambar valid sebelum bikin discord.File
        asset_path = "assets/tutorial_upload.png"
        if not os.path.isfile(asset_path):
            print(f"❌ Asset '{asset_path}' tidak ditemukan. Halt message dikirim tanpa gambar tutorial.")
            await channel.send(
                content=(
                    f"🚨 **HALT!** {sapaan}, {member.mention}!\n\n"
                    f"Untuk masuk, **upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
                    f"Atau verifikasi lebih cepat melalui **Website Resmi Telyu Jekardah!**\n"
                    f"⚠️ **PENTING:** Pastikan **Nama Lengkap, Nomor Registrasi (11 Angka), Prodi, Kampus Jakarta**, dan tahun **2026/2027** terlihat dengan jelas ya!\n\n"
                    f"📄 **Link Drive di bawah ini CUMA buat LIHAT CONTOH format SKL yang valid, BUKAN tempat upload ya:**\n"
                    f"https://drive.google.com/drive/folders/157xVAUCZHl7PSMP-Zj4brYPwXDY9baXd?usp=sharing\n\n"
                    f"Ssst... ruangan ini cuma buat upload gambar, jadi dilarang chat. Langsung drop fotonya aja!"
                )
            )
            return

        file = discord.File(asset_path, filename="tutorial_upload.png")

        await channel.send(
            content=(
                f"🚨 **HALT!** {sapaan}, {member.mention}!\n\n"
                f"Untuk masuk, **upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
                f"Atau verifikasi lebih cepat melalui **Website Resmi Telyu Jekardah!**\n"
                f"⚠️ **PENTING:** Pastikan **Nama Lengkap, Nomor Registrasi (11 Angka), Prodi, Kampus Jakarta**, dan tahun **2026/2027** terlihat dengan jelas ya!\n\n"
                f"📄 **Link Drive di bawah ini CUMA buat LIHAT CONTOH format SKL yang valid, BUKAN tempat upload ya:**\n"
                f"https://drive.google.com/drive/folders/157xVAUCZHl7PSMP-Zj4brYPwXDY9baXd?usp=sharing\n\n"
                f"Ssst... ruangan ini cuma buat upload gambar, jadi dilarang chat. Langsung drop fotonya aja!"
            ),
            embed=embed,
            file=file
        )

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

        role_member = discord.utils.get(message.guild.roles, name="MEMBER")
        if role_member and role_member in message.author.roles:
            try: await message.delete()
            except: pass
            peringatan_lolos = await message.channel.send(
                f"⚠️ **Eits {message.author.mention}, kamu kan udah lolos verifikasi!** Nggak perlu upload SKL atau chat di sini lagi ya. Cuss langsung beraktivitas di dalam server!"
            )
            await peringatan_lolos.delete(delay=5)
            return

        is_valid_image = False
        attachment = None
        if message.attachments:
            attachment = message.attachments[0]
            if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg']):
                is_valid_image = True

        if not is_valid_image:
            try: await message.delete()
            except: pass

            if message.author.id not in self.warned_users:
                self.warned_users.add(message.author.id)
                peringatan = await message.channel.send(
                    f"⚠️ **Tahan {message.author.mention}!** Ruangan ini khusus buat **upload foto SKL** (jpg/png). Tolong jangan ngirim chat di mari ya.\n"
                    f"📲 Klik ikon **(+)** di pojok kiri bawah kolom chat buat upload fotonya."
                )
                await peringatan.delete(delay=5)
            return

        # Sapu bersih pesan bot sebelumnya
        async for msg in message.channel.history(limit=50):
            if msg.author == self.bot.user and message.author.mention in msg.content:
                try: await msg.delete()
                except: pass

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200: return
                    image_data = await resp.read()

            try: await message.delete()
            except: pass

            nama_depan = message.author.display_name.split()[0]
            discord_username = message.author.name

            prompt = "Salin seluruh teks yang ada di gambar ini dengan teliti. Pastikan kamu membaca baris Nomor Registrasi (11 angka), Program Studi, Tahun, dan Nama Kampus. Jangan berikan penjelasan."
            hasil_mentah = await self.panggil_gemini_api(prompt, image_data, attachment.content_type)

            if "KODE_BLOKIR_SENSOR" in hasil_mentah:
                err_msg = await message.channel.send(
                    f"❌ **Waduh {nama_depan}, sistem Google pusing baca dokumenmu!** {message.author.mention}\n"
                    "**SOLUSI:** Pastikan foto nggak blur dan teks kelihatan jelas. Coba upload ulang gambarnya!"
                )
                await asyncio.sleep(5)
                try: await err_msg.delete()
                except: pass
                await self.send_halt_message(message.channel, message.author, is_retry=True)

            else:
                teks = " ".join(hasil_mentah.lower().split())

                # Cek 11 Angka
                match_noreg = re.search(r'\b\d{11}\b', teks)

                if not match_noreg:
                    err_msg = await message.channel.send(
                        f"❌ **Verifikasi Gagal, {nama_depan}** {message.author.mention}.\n"
                        f"Sistem tidak bisa menemukan **11 Angka Nomor Registrasi** di fotomu! Pastikan bagian tersebut tidak terpotong atau blur."
                    )
                    await asyncio.sleep(5)
                    try: await err_msg.delete()
                    except: pass
                    await self.send_halt_message(message.channel, message.author, is_retry=True)
                    return

                no_reg = match_noreg.group(0)

                # Cek Database
                record = await self.bot.pool.fetchrow("SELECT username FROM skl_registry WHERE no_reg = $1", no_reg)

                if record:
                    if record['username'] != discord_username:
                        err_msg = await message.channel.send(
                            f"🚨 **PELANGGARAN TERDETEKSI!** {message.author.mention}\n"
                            f"Nomor registrasi **{no_reg}** sudah tertaut dengan akun Discord lain (`{record['username']}`). Kamu tidak bisa menggunakan Dokumen SKL milik orang lain!"
                        )
                        await asyncio.sleep(5)
                        try: await err_msg.delete()
                        except: pass
                        await self.send_halt_message(message.channel, message.author, is_retry=True)
                        return
                    else:
                        pass

                # Cek Prodi dan Kampus
                syarat_kampus = "jakarta" in teks or "telkom university" in teks
                syarat_tahun = "2026" in teks

                # Mapping lebih lengkap & tanpa kata "informasi" untuk menghindari tabrakan
                role_mapping = {
                    "dkv": "DKV",
                    "desain komunikasi visual": "DKV",
                    "s1 desain komunikasi visual": "DKV",
                    
                    "teknologi informasi": "TI",
                    "s1 teknologi informasi": "TI",
                    "ti": "TI",
                    
                    "informatika": "INFOR",
                    "s1 informatika": "INFOR",
                    "teknik informatika": "INFOR",
                    "if": "INFOR",
                    
                    "sistem informasi": "SISFOR",
                    "s1 sistem informasi": "SISFOR",
                    "si": "SISFOR",
                    "sisfor": "SISFOR",
                    
                    "teknik telekomunikasi": "TEKTEL",
                    "s1 teknik telekomunikasi": "TEKTEL",
                    "telekomunikasi": "TEKTEL",
                    "tektel": "TEKTEL",
                    "tt": "TEKTEL",
                }

                prodi_terdeteksi = None
                role_target_name = None

                for keyword, r_name in role_mapping.items():
                    if keyword in teks:
                        prodi_terdeteksi = keyword.title()
                        role_target_name = r_name
                        break

                syarat_prodi = role_target_name is not None

                if syarat_kampus and syarat_tahun and syarat_prodi:
                    acc_msg = await message.channel.send(
                        f"✅ **Verifikasi Berhasil!** Halo **{nama_depan}** {message.author.mention}, dokumen SKL lu lolos untuk prodi **{role_target_name}**. Cuss cek room welcome-center!"
                    )
                    await asyncio.sleep(5)
                    try: await acc_msg.delete()
                    except: pass

                    if role_member:
                        await message.author.add_roles(role_member)

                    role_prodi = discord.utils.get(message.guild.roles, name=role_target_name)
                    if role_prodi:
                        try:
                            await message.author.add_roles(role_prodi)
                        except discord.Forbidden:
                            pass

                        # === EKSTRAKSI NAMA LENGKAP DARI SKL ===
                        # Gunakan Gemini sekali lagi untuk extract nama lengkap
                        nama_lengkap_skl = None
                        try:
                            prompt_nama = (
                                "Dari teks berikut, ekstrak HANYA nama lengkap siswa/mahasiswa. "
                                "Kembalikan HANYA nama lengkapnya saja, tanpa penjelasan lain.\n\n"
                                f"Teks dokumen:\n{hasil_mentah}"
                            )
                            # Panggil Gemini text-only (tanpa gambar) untuk parsing nama
                            await self.gemini_limiter.acquire()
                            clean_key = gemini_key.strip()
                            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent?key={clean_key}"
                            payload_nama = {
                                "contents": [{"parts": [{"text": prompt_nama}]}]
                            }
                            async with aiohttp.ClientSession() as sess:
                                async with sess.post(url, json=payload_nama) as resp_nama:
                                    if resp_nama.status == 200:
                                        data_nama = await resp_nama.json()
                                        raw_nama = data_nama.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
                                        # Bersihkan: hapus tanda kutip, newline, dll
                                        raw_nama = raw_nama.strip('"\'').strip()
                                        if len(raw_nama) >= 3 and len(raw_nama) <= 100:
                                            nama_lengkap_skl = raw_nama.title()  # Capitalize properly
                                            print(f"📝 Nama lengkap SKL terdeteksi: {nama_lengkap_skl}")
                        except Exception as e:
                            print(f"[WARN] Gagal extract nama lengkap: {e}")

                        # === SIMPAN KE DATABASE (TERMASUK NAMA LENGKAP) ===
                        try:
                            await self.bot.pool.execute(
                                "INSERT INTO skl_registry (no_reg, username) VALUES ($1, $2) ON CONFLICT (no_reg) DO NOTHING",
                                no_reg, discord_username
                            )
                            await self.bot.pool.execute(
                                "INSERT INTO maba_roles (username, role_name, full_name) VALUES ($1, $2, $3) ON CONFLICT (username) DO UPDATE SET role_name = EXCLUDED.role_name, full_name = EXCLUDED.full_name",
                                discord_username, role_target_name, nama_lengkap_skl
                            )
                            
                            # (BARU) Simpan ke tabel 'users' web juga agar sinkron dua arah jika perlu!
                            # Update tabel users dengan discord_id
                            discord_id_str = str(message.author.id)
                            prodi_web_mapping = {
                                "DKV": "Desain Komunikasi Visual",
                                "TI": "Teknologi Informasi",
                                "INFOR": "Informatika",
                                "SISFOR": "Sistem Informasi",
                                "TEKTEL": "Teknik Telekomunikasi"
                            }
                            web_prodi = prodi_web_mapping.get(role_target_name, "Umum")
                            
                            await self.bot.pool.execute(
                                """
                                UPDATE users 
                                SET is_verified = true, prodi = $1, full_name = $2 
                                WHERE discord_id = $3
                                """,
                                web_prodi, nama_lengkap_skl, discord_id_str
                            )
                        except Exception as e:
                            print(f"[DB ERROR] Gagal input ke database: {e}")

                    else:
                        print(f"⚠️ [DISCORD-UPLOAD] Role prodi {role_target_name} tidak ditemukan di server!")

                    pengumuman_channel = self.bot.get_channel(self.pengumuman_id)
                    if pengumuman_channel is None:
                        try: pengumuman_channel = await self.bot.fetch_channel(self.pengumuman_id)
                        except: pass
                    
                    if pengumuman_channel:
                        embed_pengumuman = discord.Embed(
                            title="🎉 MAHASISWA BARU TELAH TIBA!",
                            description=f"Mari sambut **{nama_depan}** ({message.author.mention}) dari prodi **{role_target_name}** yang baru aja lolos verifikasi gerbang utama!\nSelamat bergabung di kampus, jangan lupa mampir ke kantin virtual!",
                            color=discord.Color.gold()
                        )
                        embed_pengumuman.set_thumbnail(url=message.author.display_avatar.url)
                        try:
                            await pengumuman_channel.send(embed=embed_pengumuman)
                            print(f"✅ [DISCORD-UPLOAD] Berhasil mengirim pengumuman untuk {discord_username}")
                        except Exception as e:
                            print(f"❌ [DISCORD-UPLOAD] Gagal kirim pengumuman: {e}")
                    else:
                        print(f"⚠️ [DISCORD-UPLOAD] Pengumuman channel tidak ditemukan!")

                else:
                    err_msg = await message.channel.send(
                        f"❌ **Verifikasi Gagal, {nama_depan}** {message.author.mention}.\n"
                        f"Dokumen lu kurang lengkap nih! Pastikan **Nama, Prodi, Kampus Jakarta, dan Tahun 2026/2027** benar-benar kelihatan di fotonya. Silakan upload ulang atau panggil Admin."
                    )
                    await asyncio.sleep(5)
                    try: await err_msg.delete()
                    except: pass
                    await self.send_halt_message(message.channel, message.author, is_retry=True)

        except Exception as e:
            err_msg = await message.channel.send(f"⚠️ Waduh, sistem pusing: {e}")
            await asyncio.sleep(5)
            try: await err_msg.delete()
            except: pass
            await self.send_halt_message(message.channel, message.author, is_retry=True)

        finally:
            try: await message.delete()
            except: pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        # Mengirim notifikasi ke channel pengumuman (bisa diubah ke channel lain jika mau)
        pengumuman_channel = self.bot.get_channel(self.pengumuman_id)

        if pengumuman_channel:
            embed_leave = discord.Embed(
                title="👋 Seseorang Telah Pergi...",
                description=f"Sayonara **{member.display_name}** ({member.name}) telah keluar dari server Telyu Jekardah.",
                color=discord.Color.red()
            )
            embed_leave.set_thumbnail(url=member.display_avatar.url)

            await pengumuman_channel.send(embed=embed_leave)
            print(f"Member keluar: {member.name}")


async def setup(bot):
    await bot.add_cog(AutoGate(bot))
