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
            print(f"Database error: {e}")

        await interaction.followup.send(f"🎉 Mantap! Kamu resmi masuk program studi **{self.role_name}**. Silakan cek private room kelasmu di sebelah kiri!", ephemeral=True)
        
        try:
            await interaction.message.delete()
        except Exception:
            pass

# ==========================================
# 2. SISTEM AUTO-GATE (DIRECT API BYPASS)
# ==========================================
class AutoGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # MASUKKAN ID ROOM MASING-MASING DI SINI
        self.pos_satpam_id = 1526900951678587013  # ID room 🛑・pos-satpam
        self.welcome_center_id = 1526567698627035246 # ID room 🎒・welcome-center

    async def panggil_gemini_api(self, prompt, image_data, mime_type):
        """Fungsi rahasia untuk mem-bypass library Google dan tembak API langsung"""
        if not gemini_key:
            raise Exception("Waduh, API Key Gemini belum terbaca dari file .env atau Heroku!")

        # 1. PERBAIKAN: Bersihkan API Key dari spasi atau enter gaib yang bikin URL error 404
        clean_key = gemini_key.strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={clean_key}"
        
        # Gambar harus diubah jadi teks kode rahasia (Base64) agar bisa dikirim via link
        base64_image = base64.b64encode(image_data).decode('utf-8')
        
        # 2. PERBAIKAN: API Google wajib pakai camelCase (inlineData & mimeType)
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": base64_image}}
                ]
            }]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    # Print log ke terminal Heroku agar tahu pesan error aslinya jika gagal lagi
                    error_msg = await resp.text()
                    print(f"DEBUG GEMINI API ERROR: {error_msg}") 
                    raise Exception(f"API Error {resp.status}")
                
                data = await resp.json()
                return data['candidates'][0]['content']['parts'][0]['text']

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot: return
        pos_satpam = self.bot.get_channel(self.pos_satpam_id)
        if pos_satpam:
            pesan = await pos_satpam.send(
                f"🚨 **HALT!** Berhenti di situ, {member.mention}!\n\n"
                f"Ini adalah Pos Satpam kampus. Untuk bisa masuk ke dalam server, **silakan upload foto Surat Kelulusan (SKL)** kamu di sini.\n"
                f"Pastikan pada foto terdapat tulisan **Kampus Jakarta** dan tahun **2026/2027** ya! Sistem AI kami akan mengeceknya secara otomatis."
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
                                await message.channel.send("Gagal mengunduh gambar.")
                                return
                            image_data = await resp.read()

                    prompt = (
                        "Kamu adalah sistem keamanan otomatis kampus. Cari 2 informasi krusial berikut pada gambar:\n"
                        "1. Apakah ada kata 'Kampus Jakarta' ATAU 'Jakarta' ATAU 'Telkom University Jakarta'?\n"
                        "2. Apakah ada tahun '2026/2027' ATAU '2026'?\n\n"
                        "Jika KEDUA syarat terpenuhi, balas HANYA dengan kata 'LOLOS'. "
                        "Jika ada yang tidak terpenuhi, balas HANYA dengan kata 'TOLAK'."
                    )

                    # Kirim gambar langsung ke server Google
                    hasil_mentah = await self.panggil_gemini_api(prompt, image_data, attachment.content_type)
                    hasil = hasil_mentah.strip().upper()

                    if "LOLOS" in hasil:
                        role_member = discord.utils.get(message.guild.roles, name="MEMBER")
                        if role_member: await message.author.add_roles(role_member)
                        
                        acc_msg = await message.channel.send(f"✅ **Verifikasi Berhasil!** {message.author.mention}, akses kampus sudah dibuka. Silakan cek room welcome-center!")
                        await acc_msg.delete(delay=10)

                        welcome_channel = self.bot.get_channel(self.welcome_center_id)
                        if welcome_channel:
                            embed = discord.Embed(
                                title="🎓 Welcome to Telyu Jekardah!",
                                description=(
                                    f"Helo welkam join Telyu Jekardah, {message.author.mention}!\n\n"
                                    "Berkas SKL kamu udah aman. Sebelum mulai berpetualang dan mabar, kamu **wajib** milih program studi dulu nih.\n\n"
                                    "👉 **Silakan pilih satu role jurusan di bawah!**\n"
                                    "*(Tombol ini dikunci khusus untukmu, dan pesan ini nggak akan hilang sampai kamu milih jurusan)*"
                                ),
                                color=discord.Color.blue()
                            )
                            embed.set_thumbnail(url=message.author.display_avatar.url)
                            view = WelcomeRoleView(target_member=message.author, bot=self.bot)
                            await welcome_channel.send(content=f"Cek di mari ngab {message.author.mention}!", embed=embed, view=view)

                    else:
                        tolak_msg = await message.channel.send(f"❌ **Verifikasi Gagal, {message.author.mention}.** Surat tidak terdeteksi sebagai dokumen dari Kampus Jakarta tahun ajaran 2026/2027, atau gambar terlalu buram. Silakan upload ulang atau panggil Admin.")
                        await tolak_msg.delete(delay=15)

                except Exception as e:
                    await message.channel.send(f"⚠️ Waduh, sistem AI lagi pusing: {e}")
                
                finally:
                    try: await message.delete()
                    except: pass

async def setup(bot):
    await bot.add_cog(AutoGate(bot))