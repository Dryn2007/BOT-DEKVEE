import discord
from discord.ext import commands
import aiohttp
import os
import base64

# Ambil API key dari .env
gemini_key = os.getenv("GEMINI_API_KEY")

# ==========================================
# SISTEM AUTO-GATE (FULL OTOMATIS BACA PRODI)
# ==========================================
class AutoGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # MASUKKAN ID ROOM POS SATPAM DI SINI
        self.pos_satpam_id = 1526900951678587013
        # ID welcome-center sudah dihapus karena tidak dipakai lagi

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
                
                # Cek filter Google
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
            # Peringatan diubah agar maba tidak menutupi teks prodinya
            await pos_satpam.send(
                f"🚨 **HALT!** Berhenti di situ, {member.mention}!\n\n"
                f"Untuk masuk, **upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
                f"⚠️ **PENTING:** Tolong **coret/sensor Nama Lengkap, Nomor Pendaftaran, dan Foto Wajahmu** agar aman.\n"
                f"Pastikan teks **Kampus Jakarta**, tahun **2026/2027**, dan **Program Studi** kamu tetap terlihat jelas."
            )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.channel.id != self.pos_satpam_id:
            return
            
        if message.attachments:
            attachment = message.attachments[0]
            if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg']):
                await message.add_reaction("⏳")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as resp:
                            if resp.status != 200: return
                            image_data = await resp.read()

                    nama_depan = message.author.display_name.split()[0]
                    
                    # AI hanya menyalin teks saja
                    prompt = "Salin dan ketik ulang seluruh teks yang bisa kamu baca di gambar ini. Jangan berikan penjelasan."
                    hasil_mentah = await self.panggil_gemini_api(prompt, image_data, attachment.content_type)
                    
                    if "KODE_BLOKIR_SENSOR" in hasil_mentah:
                        tolak_msg = await message.channel.send(
                            f"❌ **Waduh {nama_depan}, sistem Google menolak membaca dokumenmu!** {message.author.mention}\n"
                            "👉 **SOLUSI:** Sensor bagian Nama Lengkap, Nomor Pendaftaran, dan Foto Wajah. Jika masih gagal, crop gambar agar fokus ke teks Kampus, Tahun, dan Prodi saja."
                        )
                        await tolak_msg.delete(delay=30)
                    else:
                        # Logika Python (Bukan AI) yang menentukan Lolos & Jurusannya
                        teks = hasil_mentah.lower()
                        
                        # 1. Cek Kampus & Tahun
                        syarat_kampus = "jakarta" in teks or "telkom university" in teks
                        syarat_tahun = "2026" in teks
                        
                        # 2. Cek Jurusan (Prodi)
                        prodi_terdeteksi = None
                        if "desain komunikasi visual" in teks or "dkv" in teks:
                            prodi_terdeteksi = "DKV"
                        elif "teknologi informasi" in teks or "tekinfo" in teks:
                            prodi_terdeteksi = "TEKINFO"
                        elif "sistem informasi" in teks or "sisfor" in teks:
                            prodi_terdeteksi = "SISFOR"
                        elif "teknik telekomunikasi" in teks or "telekomunikasi" in teks or "tektel" in teks:
                            prodi_terdeteksi = "TEKTEL"

                        # 3. Eksekusi Hasil
                        if syarat_kampus and syarat_tahun and prodi_terdeteksi:
                            
                            # Berikan Role MEMBER
                            role_member = discord.utils.get(message.guild.roles, name="MEMBER")
                            if role_member: await message.author.add_roles(role_member)
                                
                            # Berikan Role JURUSAN
                            role_prodi = discord.utils.get(message.guild.roles, name=prodi_terdeteksi)
                            if role_prodi: await message.author.add_roles(role_prodi)

                            # Masukkan ke Database Maba
                            try:
                                await self.bot.pool.execute(
                                    "INSERT INTO maba_roles (username, role_name) VALUES ($1, $2)",
                                    message.author.name, prodi_terdeteksi
                                )
                            except Exception as e:
                                print(f"[DB ERROR] Gagal input ke database: {e}")

                            # Sapaan sukses
                            acc_msg = await message.channel.send(
                                f"✅ **Verifikasi Berhasil!** Halo **{nama_depan}** {message.author.mention}, kamu resmi masuk program studi **{prodi_terdeteksi}**! Silakan cek private room kelasmu di sebelah kiri!"
                            )
                            await acc_msg.delete(delay=25)
                        else:
                            tolak_msg = await message.channel.send(
                                f"❌ **Gagal {nama_depan}** {message.author.mention}. Pastikan nama kampus, tahun 2026/2027, dan **Nama Program Studi** terbaca jelas di fotomu!"
                            )
                            await tolak_msg.delete(delay=15)
                            
                except Exception as e:
                    await message.channel.send(f"⚠️ Waduh, sistem pusing: {e}")
                finally:
                    # Selalu hancurkan barang bukti
                    try: await message.delete()
                    except: pass

async def setup(bot):
    await bot.add_cog(AutoGate(bot))