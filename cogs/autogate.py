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
# 2. SISTEM AUTO-GATE (LOGIKA JALAN TIKUS)
# ==========================================
class AutoGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pos_satpam_id = 1526900951678587013
        self.welcome_center_id = 1526567698627035246

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
            await pos_satpam.send(
                f"🚨 **HALT!** Berhenti di situ, {member.mention}!\n\n"
                f"Untuk masuk, **upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
                f"⚠️ **PENTING:** Tolong **coret/sensor Nama Lengkap, Nomor Pendaftaran, dan Foto Wajahmu** agar sistem AI bisa membacanya!\n"
                f"Pastikan teks **Kampus Jakarta** dan tahun **2026/2027** tetap terlihat jelas."
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
                    prompt = "Salin dan ketik ulang seluruh teks yang bisa kamu baca di gambar ini. Jangan berikan komentar, penjelasan, atau peringatan."

                    hasil_mentah = await self.panggil_gemini_api(prompt, image_data, attachment.content_type)
                    
                    if "KODE_BLOKIR_SENSOR" in hasil_mentah:
                        tolak_msg = await message.channel.send(
                            f"❌ **Waduh {nama_depan}, sistem Google menolak membaca dokumenmu!** {message.author.mention}\n"
                            "👉 **SOLUSI:** Sensor bagian Nama Lengkap, Nomor Pendaftaran, dan Foto Wajah. Jika masih gagal, potong (crop) gambarnya hanya di bagian teks tahun dan nama kampus saja!"
                        )
                        await tolak_msg.delete(delay=30)
                    else:
                        # Logika Python (Bukan AI) yang menentukan Lolos atau Tidak
                        teks = hasil_mentah.lower()
                        if ("jakarta" in teks or "telkom university" in teks) and "2026" in teks:
                            role_member = discord.utils.get(message.guild.roles, name="MEMBER")
                            if role_member: await message.author.add_roles(role_member)
                            
                            await message.channel.send(f"✅ **Verifikasi Berhasil!** Halo **{nama_depan}** {message.author.mention}, akses terbuka!")
                            
                            welcome_channel = self.bot.get_channel(self.welcome_center_id)
                            if welcome_channel:
                                embed = discord.Embed(title="🎓 Welcome!", description=f"Helo kak **{nama_depan}**, silakan pilih jurusan!", color=discord.Color.blue())
                                await welcome_channel.send(embed=embed, view=WelcomeRoleView(target_member=message.author, bot=self.bot))
                        else:
                            await message.channel.send(f"❌ **Gagal {nama_depan}**, dokumen tidak terdeteksi sebagai Kampus Jakarta 2026/2027. Pastikan teks terlihat jelas.")
                except Exception as e:
                    await message.channel.send(f"⚠️ Waduh, sistem pusing: {e}")
                finally:
                    try: await message.delete()
                    except: pass

async def setup(bot):
    await bot.add_cog(AutoGate(bot))