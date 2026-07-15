import discord
from discord.ext import commands
import google.generativeai as genai
import aiohttp
import os
# ==========================================
# 1. KONFIGURASI AI (GEMINI)
# ==========================================
# Masukkan API Key kamu di sini
gemini_api_key = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=gemini_api_key)
model = genai.GenerativeModel('gemini-1.5-flash')

# ==========================================
# 2. SISTEM TOMBOL WELCOME 
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

        # Kunci tombol hanya untuk member yang di-tag
        if interaction.user.id != target_member.id:
            await interaction.response.send_message(f"⚠️ Eits, tombol ini khusus untuk {target_member.mention}!", ephemeral=True)
            return

        # Tahan interaksi agar tidak error timeout
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

        # Simpan ke database
        try:
            await bot.pool.execute(
                "INSERT INTO maba_roles (username, role_name) VALUES ($1, $2)",
                target_member.name, self.role_name
            )
        except Exception as e:
            print(f"Database error: {e}")

        await interaction.followup.send(f"🎉 Mantap! Kamu resmi masuk program studi **{self.role_name}**. Silakan cek private room kelasmu di sebelah kiri!", ephemeral=True)
        
        # Hapus pesan sapaan setelah berhasil memilih
        try:
            await interaction.message.delete()
        except Exception:
            pass

# ==========================================
# 3. SISTEM AUTO-GATE (PEMBACA SKL)
# ==========================================
class AutoGate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # MASUKKAN ID ROOM MASING-MASING DI SINI
        self.pos_satpam_id = 1526900951678587013  # Ganti dengan ID room 🛑・pos-satpam
        self.welcome_center_id = 1526567698627035246 # Ganti dengan ID room 🎒・welcome-center

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if message.channel.id != self.pos_satpam_id:
            return

        # Cek apakah ada gambar yang dikirim
        if message.attachments:
            attachment = message.attachments[0]
            
            if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg']):
                await message.add_reaction("⏳")
                
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as resp:
                            if resp.status != 200:
                                await message.channel.send("Gagal mengunduh gambar, coba lagi ya.")
                                return
                            image_data = await resp.read()

                    image_parts = [{"mime_type": attachment.content_type, "data": image_data}]

                    prompt = (
                        "Kamu adalah sistem keamanan otomatis kampus. Cari 2 informasi krusial berikut pada gambar:\n"
                        "1. Apakah ada kata 'Kampus Jakarta' ATAU 'Jakarta' ATAU 'Telkom University Jakarta'?\n"
                        "2. Apakah ada tahun '2026/2027' ATAU '2026'?\n\n"
                        "Jika KEDUA syarat terpenuhi, balas HANYA dengan kata 'LOLOS'. "
                        "Jika ada yang tidak terpenuhi, balas HANYA dengan kata 'TOLAK'."
                    )

                    response = await model.generate_content_async([prompt, image_parts[0]])
                    hasil = response.text.strip().upper()

                    if "LOLOS" in hasil:
                        # 1. Berikan role MEMBER otomatis
                        role_member = discord.utils.get(message.guild.roles, name="MEMBER")
                        if role_member:
                            await message.author.add_roles(role_member)
                        
                        # 2. Kasih tahu di pos satpam (pesan akan hilang dalam 10 detik)
                        acc_msg = await message.channel.send(f"✅ **Verifikasi Berhasil!** {message.author.mention}, akses kampus sudah dibuka. Silakan cek room welcome-center!")
                        await acc_msg.delete(delay=10)

                        # 3. MUNCULKAN TOMBOL DI WELCOME CENTER
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
                            
                            # Memanggil tombol dan menguncinya untuk author (maba)
                            view = WelcomeRoleView(target_member=message.author, bot=self.bot)
                            await welcome_channel.send(content=f"Cek di mari ngab {message.author.mention}!", embed=embed, view=view)

                    else:
                        # Jika verifikasi ditolak
                        tolak_msg = await message.channel.send(f"❌ **Verifikasi Gagal, {message.author.mention}.** Surat tidak terdeteksi sebagai dokumen dari Kampus Jakarta tahun ajaran 2026/2027, atau gambar terlalu buram. Silakan upload ulang atau panggil Admin.")
                        await tolak_msg.delete(delay=15)

                except Exception as e:
                    await message.channel.send(f"⚠️ Sistem AI lagi pusing: {e}")
                
                finally:
                    # 4. HAPUS FOTO SKL DARI POS SATPAM SECARA INSTAN
                    try:
                        await message.delete()
                    except:
                        pass

async def setup(bot):
    await bot.add_cog(AutoGate(bot))