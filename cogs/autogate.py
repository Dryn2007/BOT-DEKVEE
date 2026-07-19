import discord
from discord.ext import commands
import aiohttp
import os
import base64
import asyncio
import re  # <--- DITAMBAHKAN UNTUK MELACAK 11 ANGKA REGISTRASI

# Ambil API key dari .env
gemini_key = os.getenv("GEMINI_API_KEY")

# ==========================================
# 2. SISTEM AUTO-GATE (FULL OTOMATIS & ANTI MALING)
# ==========================================
class AutoGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # MASUKKAN ID ROOM MASING-MASING DI SINI
        self.pos_satpam_id = 1526900951678587013
        self.welcome_center_id = 1526567698627035246
        self.pengumuman_id = 1526219303714820186
        
        self.warned_users = set()
        self.is_ready = False

    # Membuat Tabel Baru untuk mencatat Nomor Registrasi di Database
    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_ready:
            await self.bot.pool.execute('''
                CREATE TABLE IF NOT EXISTS skl_registry (
                    no_reg TEXT PRIMARY KEY,
                    username TEXT
                )
            ''')
            self.is_ready = True
            print("✅ Tabel Keamanan SKL (skl_registry) siap!")

    async def panggil_gemini_api(self, prompt, image_data, mime_type):
        if not gemini_key:
            raise Exception("API Key Gemini belum terbaca!")
        
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
                if resp.status != 200:
                    raise Exception(f"API Error {resp.status}")
                data = await resp.json()
                kandidat = data.get('candidates', [{}])[0]

                if kandidat.get('finishReason') in ['PROHIBITED_CONTENT', 'SAFETY']:
                    return "KODE_BLOKIR_SENSOR"

                try:
                    return kandidat['content']['parts'][0]['text']
                except KeyError:
                    raise Exception("Format JSON tidak terbaca.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot: return
        pos_satpam = self.bot.get_channel(self.pos_satpam_id)
        if pos_satpam:
            await pos_satpam.send(
                f"🚨 **HALT!** Berhenti di situ, {member.mention}!\n\n"
                f"Untuk masuk, **upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
                f"⚠️ **PENTING:** Pastikan **Nama Lengkap, Nomor Registrasi (11 Angka), Prodi, Kampus Jakarta**, dan tahun **2026/2027** terlihat dengan jelas ya!\n\n"
                f"📄 **Cek contoh SKL yang valid di sini:** https://drive.google.com/drive/folders/157xVAUCZHl7PSMP-Zj4brYPwXDY9baXd?usp=sharing\n\n"
                f"Ssst... ruangan ini cuma buat upload gambar, jadi dilarang chat. Langsung drop fotonya aja!"
            )

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
            await peringatan_lolos.delete(delay=10)
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
                    f"⚠️ **Tahan {message.author.mention}!** Ruangan ini khusus buat **upload foto SKL** (jpg/png). Tolong jangan ngirim chat di mari ya."
                )
                await peringatan.delete(delay=15)
            return

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
            discord_username = message.author.name # USERNAME ASLI DISCORD, BUKAN NICKNAME SERVER
            
            # PROMPT KITA PERTAJAM AGAR WAJIB MEMBACA NOMOR REGISTRASI
            prompt = "Salin seluruh teks yang ada di gambar ini dengan teliti. Pastikan kamu membaca baris Nomor Registrasi (11 angka), Program Studi, Tahun, dan Nama Kampus. Jangan berikan penjelasan."
            hasil_mentah = await self.panggil_gemini_api(prompt, image_data, attachment.content_type)

            if "KODE_BLOKIR_SENSOR" in hasil_mentah:
                await message.channel.send(
                    f"❌ **Waduh {nama_depan}, sistem Google pusing baca dokumenmu!** {message.author.mention}\n"
                    "**SOLUSI:** Pastikan foto nggak blur dan teks kelihatan jelas. Coba upload ulang gambarnya!"
                )
            else:
                teks = " ".join(hasil_mentah.lower().split())
                
                # ==============================================================
                # SISTEM DETEKSI DAN CEK NOMOR REGISTRASI (11 ANGKA)
                # ==============================================================
                # Mencari pola tepat 11 angka yang berjejer
                match_noreg = re.search(r'\b\d{11}\b', teks)
                
                if not match_noreg:
                    await message.channel.send(
                        f"❌ **Verifikasi Gagal, {nama_depan}** {message.author.mention}.\n"
                        f"Sistem tidak bisa menemukan **11 Angka Nomor Registrasi** di fotomu! Pastikan bagian tersebut tidak terpotong atau blur."
                    )
                    return
                    
                no_reg = match_noreg.group(0)
                
                # CEK APAKAH NOMOR REGISTRASI INI SUDAH PERNAH DIPAKAI SEBELUMNYA
                record = await self.bot.pool.fetchrow("SELECT username FROM skl_registry WHERE no_reg = $1", no_reg)
                
                if record:
                    if record['username'] != discord_username:
                        # ANCAMAN TINGGI! ADA YANG MENCOBA MEMAKAI SKL ORANG LAIN
                        await message.channel.send(
                            f"🚨 **PELANGGARAN TERDETEKSI!** {message.author.mention}\n"
                            f"Nomor registrasi **{no_reg}** sudah tertaut dengan akun Discord lain (`{record['username']}`). Kamu tidak bisa menggunakan Dokumen SKL milik orang lain!"
                        )
                        return
                    else:
                        pass # Jika akunnya sama (misal dia keluar lalu masuk lagi)

                # ==============================================================
                # LANJUT CEK PRODI DAN KAMPUS
                # ==============================================================
                syarat_kampus = "jakarta" in teks or "telkom university" in teks
                syarat_tahun = "2026" in teks
                
                role_mapping = {
                    "dkv": "DKV",
                    "desain komunikasi visual": "DKV",
                    "teknologi informasi": "TEKINFO",
                    "tekinfo": "TEKINFO",
                    "sistem informasi": "SISFOR",
                    "sisfor": "SISFOR",
                    "telekomunikasi": "TEKTEL",
                    "teknik telekomunikasi": "TEKTEL",
                    "tektel": "TEKTEL"
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

                        # ==============================================================
                        # SIMPAN NOMOR REGISTRASI DAN ROLE KE DATABASE
                        # ==============================================================
                        try:
                            # 1. Simpan ke registry anti-maling
                            await self.bot.pool.execute(
                                "INSERT INTO skl_registry (no_reg, username) VALUES ($1, $2) ON CONFLICT (no_reg) DO NOTHING",
                                no_reg, discord_username
                            )
                            # 2. Simpan role
                            await self.bot.pool.execute(
                                "INSERT INTO maba_roles (username, role_name) VALUES ($1, $2)",
                                discord_username, role_target_name
                            )
                        except Exception as e:
                            print(f"[DB ERROR] Gagal input ke database: {e}")

                    # PENGUMUMAN
                    welcome_channel = self.bot.get_channel(self.welcome_center_id)
                    if welcome_channel:
                        embed = discord.Embed(
                            title="🎓 Welcome to Telyu Jekardah!",
                            description=(
                                f"Helo welkam join Telyu Jekardah, kak **{nama_depan}**! {message.author.mention}\n\n"
                                f"Sistem berhasil membaca dokumen SKL-mu. Kamu telah otomatis diberikan Role **{role_target_name}**! 🎉\n\n"
                                "👉 **Silakan langsung meluncur ke private room kelasmu di sebelah kiri!**"
                            ),
                            color=discord.Color.green()
                        )
                        embed.set_thumbnail(url=message.author.display_avatar.url)
                        await welcome_channel.send(content=f"Cek di mari ngab **{nama_depan}**!", embed=embed)
                    
                    pengumuman_channel = self.bot.get_channel(self.pengumuman_id)
                    if pengumuman_channel:
                        embed_pengumuman = discord.Embed(
                            title="🎉 MAHASISWA BARU TELAH TIBA!",
                            description=f"Mari sambut **{nama_depan}** ({message.author.mention}) dari prodi **{role_target_name}** yang baru aja lolos verifikasi gerbang utama!\nSelamat bergabung di kampus, jangan lupa mampir ke kantin virtual!",
                            color=discord.Color.gold()
                        )
                        embed_pengumuman.set_thumbnail(url=message.author.display_avatar.url)
                        await pengumuman_channel.send(embed=embed_pengumuman)

                else:
                    await message.channel.send(
                        f"❌ **Verifikasi Gagal, {nama_depan}** {message.author.mention}.\n"
                        f"Dokumen lu kurang lengkap nih! Pastikan **Nama, Prodi, Kampus Jakarta, dan Tahun 2026/2027** benar-benar kelihatan di fotonya. Silakan upload ulang atau panggil Admin.\n"
                        f"📄 **Cek contoh SKL yang bener di sini:** https://drive.google.com/drive/folders/157xVAUCZHl7PSMP-Zj4brYPwXDY9baXd?usp=sharing"
                    )

        except Exception as e:
            await message.channel.send(f"⚠️ Waduh, sistem pusing: {e}")
        finally:
            try: await message.delete()
            except: pass

async def setup(bot):
    await bot.add_cog(AutoGate(bot))