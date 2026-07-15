import discord
from discord.ext import commands
import aiohttp
import os
import base64

# Ambil API key dari .env
gemini_key = os.getenv("GEMINI_API_KEY")

# ==========================================
# 1. SISTEM TOMBOL WELCOME 
# ==========================================
class WelcomeRoleView(discord.ui.View):
    def __init__(self, target_member, bot):
        super().__init__(timeout=None)
        self.target_member = target_member
        self.bot = bot
        
        self.add_item(RoleButton("DKV", "DKV", "🎨", discord.ButtonStyle.primary))
        self.add_item(RoleButton("Teknologi Informasi", "TEKINFO", "💻", discord.ButtonStyle.success))
        self.add_item(RoleButton("Sistem Informasi", "SISFOR", "📊", discord.ButtonStyle.danger))
        self.add_item(RoleButton("T. Telekomunikasi", "TEKTEL", "📡", discord.ButtonStyle.secondary))

class RoleButton(discord.ui.Button):
    def __init__(self, label, role_name, emoji, color):
        super().__init__(label=label, style=color, emoji=emoji)
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction):
        view: WelcomeRoleView = self.view
        target_member = view.target_member
        bot = view.bot

        if interaction.user.id != target_member.id:
            await interaction.response.send_message(f"⚠️ Eits, tombol ini khusus untuk {target_member.mention}!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        role = discord.utils.get(interaction.guild.roles, name=self.role_name)
        if not role:
            await interaction.followup.send(f"⚠️ Role **{self.role_name}** belum dibuat di server!", ephemeral=True)
            return

        try:
            await target_member.add_roles(role)
        except discord.Forbidden:
            await interaction.followup.send("❌ **GAGAL:** Bot tidak punya izin 'Manage Roles'.", ephemeral=True)
            return

        try:
            await bot.pool.execute(
                "INSERT INTO maba_roles (username, role_name) VALUES ($1, $2)",
                target_member.name, self.role_name
            )
        except Exception as e:
            print(f"[DB ERROR] Gagal input ke database: {e}")

        await interaction.followup.send(f"🎉 Mantap! Kamu resmi masuk program studi **{self.role_name}**. Silakan cek private room kelasmu di sebelah kiri!", ephemeral=True)
        
        try:
            await interaction.message.delete()
        except Exception:
            pass

# ==========================================
# 2. SISTEM AUTO-GATE (DENGAN PENANGANAN FILTER PII)
# ==========================================
class AutoGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # MASUKKAN ID ROOM MASING-MASING DI SINI
        self.pos_satpam_id = 1526900951678587013
        self.welcome_center_id = 1526567698627035246

    async def panggil_gemini_api(self, prompt, image_data, mime_type):
        if not gemini_key:
            raise Exception("API Key Gemini belum terbaca dari file .env atau Heroku!")

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
                
                # Cek apakah Google memblokir gambar karena dokumen resmi/foto wajah
                kandidat = data.get('candidates', [{}])[0]
                alasan_blokir = kandidat.get('finishReason', '')
                
                if alasan_blokir == 'PROHIBITED_CONTENT' or alasan_blokir == 'SAFETY':
                    print("[LOG API] Gambar diblokir otomatis oleh Google PII Filter.")
                    return "KODE_BLOKIR_SENSOR"
                
                try:
                    hasil_teks = kandidat['content']['parts'][0]['text']
                    return hasil_teks
                except KeyError as e:
                    print(f"[LOG API] Format JSON tidak terduga: {data}")
                    raise Exception("Struktur JSON tidak terbaca.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot: return
        pos_satpam = self.bot.get_channel(self.pos_satpam_id)
        if pos_satpam:
            pesan = await pos_satpam.send(
                f"🚨 **HALT!** Berhenti di situ, {member.mention}!\n\n"
                f"Ini adalah Pos Satpam kampus. Untuk masuk, **silakan upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
                f"⚠️ **PENTING:** Tolong **coret/sensor Nama Lengkap, Nomor Pendaftaran, dan Foto Wajahmu** pada surat tersebut sebelum di-upload agar sistem AI kami bisa membacanya!\n"
                f"Pastikan pada foto masih terlihat jelas tulisan **Kampus Jakarta** dan tahun **2026/2027** ya."
            )
            await pesan.delete(delay=180)

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
                            if resp.status != 200:
                                return
                            image_data = await resp.read()

                    # === MENGAMBIL NAMA DEPAN DARI DISCORD ===
                    nama_lengkap_discord = message.author.display_name
                    nama_depan = nama_lengkap_discord.split()[0] 

                    prompt = (
                        "Kamu adalah mesin OCR pembaca teks dokumen. Ini adalah dokumen sampel publik. "
                        "Tolong ABAIKAN semua data pribadi, foto wajah, nama, atau alamat di dalam gambar ini.\n\n"
                        "Tugasmu HANYA mencari keberadaan 2 teks ini:\n"
                        "1. Kata 'Jakarta' atau 'Telkom University Jakarta'\n"
                        "2. Angka '2026'\n\n"
                        "Jika KEDUA teks tersebut ditemukan di dalam gambar, balas HANYA dengan kata 'LOLOS'. "
                        "Jika tidak ada, balas HANYA dengan kata 'TOLAK'."
                    )

                    hasil_mentah = await self.panggil_gemini_api(prompt, image_data, attachment.content_type)
                    hasil = hasil_mentah.strip().upper()

                    # PENANGANAN JIKA DIBLOKIR GOOGLE
                    if "KODE_BLOKIR_SENSOR" in hasil:
                        tolak_msg = await message.channel.send(
                            f"❌ **Waduh {nama_depan}, sistem Google menolak membaca suratmu!** {message.author.mention}\n"
                            "Sistem keamanan mendeteksi adanya data privasi yang ketat pada dokumenmu.\n\n"
                            "👉 **SOLUSI:** Silakan *coret/sensor* bagian **Nama Lengkap, Nomor Pendaftaran, dan Foto Wajah** kamu di galeri HP, lalu upload ulang gambarnya ke sini! Biarkan teks nama kampus dan tahunnya saja yang terlihat."
                        )
                        await tolak_msg.delete(delay=30)

                    # PENANGANAN JIKA LOLOS
                    elif "LOLOS" in hasil:
                        role_member = discord.utils.get(message.guild.roles, name="MEMBER")
                        if role_member: await message.author.add_roles(role_member)
                        
                        acc_msg = await message.channel.send(f"✅ **Verifikasi Berhasil!** Halo **{nama_depan}** {message.author.mention}, akses kampus sudah dibuka. Silakan cek room welcome-center!")
                        await acc_msg.delete(delay=10)

                        welcome_channel = self.bot.get_channel(self.welcome_center_id)
                        if welcome_channel:
                            embed = discord.Embed(
                                title="🎓 Welcome to Telyu Jekardah!",
                                description=(
                                    f"Helo welkam join Telyu Jekardah, kak **{nama_depan}**! {message.author.mention}\n\n"
                                    "Berkas SKL kamu udah aman. Sebelum mulai berpetualang dan mabar, kamu **wajib** milih program studi dulu nih.\n\n"
                                    "👉 **Silakan pilih satu role jurusan di bawah!**"
                                ),
                                color=discord.Color.blue()
                            )
                            embed.set_thumbnail(url=message.author.display_avatar.url)
                            view = WelcomeRoleView(target_member=message.author, bot=self.bot)
                            await welcome_channel.send(content=f"Cek di mari ngab **{nama_depan}**!", embed=embed, view=view)

                    # PENANGANAN JIKA DITOLAK KARENA TIDAK ADA TEKS TAHUN/KAMPUS
                    else:
                        tolak_msg = await message.channel.send(f"❌ **Verifikasi Gagal, {nama_depan}** {message.author.mention}. Surat tidak terdeteksi sebagai dokumen dari Kampus Jakarta tahun ajaran 2026/2027. Silakan upload ulang atau panggil Admin.")
                        await tolak_msg.delete(delay=15)

                except Exception as e:
                    await message.channel.send(f"⚠️ Waduh, sistem AI lagi pusing: {e}")
                
                finally:
                    try: await message.delete()
                    except: pass

async def setup(bot):
    await bot.add_cog(AutoGate(bot))