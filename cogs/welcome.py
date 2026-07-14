import discord
from discord.ext import commands
import asyncpg

class WelcomeRoleView(discord.ui.View):
    def __init__(self, target_member, bot):
        # timeout=None agar pesan tidak hilang/kadaluarsa kalau maba lama mikir
        super().__init__(timeout=None)
        self.target_member = target_member
        self.bot = bot
        
        # Tombol-tombol jurusan
        self.add_item(RoleButton("DKV", "DKV", "🎨", discord.ButtonStyle.primary))
        self.add_item(RoleButton("Teknologi Informasi", "TEKINFO", "💻", discord.ButtonStyle.success))
        self.add_item(RoleButton("Sistem Informasi", "SISFOR", "📊", discord.ButtonStyle.danger))
        self.add_item(RoleButton("T. Telekomunikasi", "TEKTEL", "📡", discord.ButtonStyle.secondary))

class RoleButton(discord.ui.Button):
    def __init__(self, label, role_name, emoji, color):
        super().__init__(label=label, style=color, emoji=emoji)
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction):
        # Mengambil referensi dari class View di atas
        view: WelcomeRoleView = self.view
        target_member = view.target_member
        bot = view.bot

        # 1. KUNCI EKSKLUSIF: Cek apakah yang ngeklik adalah orang yang di-tag
        if interaction.user.id != target_member.id:
            await interaction.response.send_message(f"⚠️ Eits, tombol ini khusus untuk {target_member.mention}! Tunggu giliran welcome-mu sendiri ya.", ephemeral=True)
            return

        # 2. TAHAN INTERAKSI (Mencegah error "This interaction failed" karena timeout)
        await interaction.response.defer(ephemeral=True)

        # 3. PROSES PEMBERIAN ROLE JURUSAN
        role = discord.utils.get(interaction.guild.roles, name=self.role_name)
        if not role:
            await interaction.followup.send(f"⚠️ Role **{self.role_name}** belum dibuat di server!", ephemeral=True)
            return

        try:
            await target_member.add_roles(role)
        except discord.Forbidden:
            await interaction.followup.send("❌ **GAGAL:** Bot tidak punya izin! Pastikan role Bot DekVee berada di atas role jurusan.", ephemeral=True)
            return

        # 4. SIMPAN USERNAME KE DATABASE (Kunci Permanen)
        try:
            await bot.pool.execute(
                "INSERT INTO maba_roles (username, role_name) VALUES ($1, $2)",
                target_member.name, self.role_name
            )
        except Exception as e:
            print(f"Database error saat menyimpan role maba: {e}")

        # 5. HAPUS PESAN WELCOME & BERI KONFIRMASI
        await interaction.followup.send(f"🎉 Mantap! Kamu resmi masuk program studi **{self.role_name}**. Silakan cek private room kelasmu di sebelah kiri!", ephemeral=True)
        
        try:
            await interaction.message.delete()
        except Exception:
            pass


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Membuat tabel database jika belum ada untuk menampung history role maba
        await self.bot.pool.execute('''
            CREATE TABLE IF NOT EXISTS maba_roles (
                username TEXT PRIMARY KEY,
                role_name TEXT
            )
        ''')

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # --- FITUR BARU: BERIKAN ROLE "MEMBER" SECARA OTOMATIS ---
        default_role = discord.utils.get(member.guild.roles, name="MEMBER")
        if default_role:
            try:
                await member.add_roles(default_role)
            except discord.Forbidden:
                print("Gagal memberikan role MEMBER. Pastikan role bot DekVee berada di atas role MEMBER.")
            except Exception as e:
                print(f"Error pemberian role MEMBER: {e}")
        # ---------------------------------------------------------

        # MASUKKAN ID ROOM 🎒・registrasi-maba DI SINI
        WELCOME_CHANNEL_ID = 1526567698627035246
        channel = self.bot.get_channel(WELCOME_CHANNEL_ID)
        
        if not channel:
            return

        # 1. CEK DATABASE: Apakah username ini sudah pernah join dan milih role?
        data = await self.bot.pool.fetchrow("SELECT role_name FROM maba_roles WHERE username = $1", member.name)
        
        if data:
            # JIKA SUDAH PERNAH (Leave lalu Re-join)
            old_role_name = data['role_name']
            role = discord.utils.get(member.guild.roles, name=old_role_name)
            
            if role:
                await member.add_roles(role)
            
            # Kirim pesan singkat tanpa tombol, lalu hilangkan dalam 15 detik
            await channel.send(
                f"👋 Welcome back {member.mention}! Data kamu sudah tersimpan sebagai **{old_role_name}**. Nggak perlu pilih role lagi, langsung masuk kelas aja!", 
                delete_after=15
            )
            return

        # 2. JIKA MABA BENAR-BENAR BARU
        embed = discord.Embed(
            title="🎓 Welcome to Telyu Jekardah!",
            description=(
                f"Helo welkam join Telyu Jekardah, {member.mention}!\n\n"
                "Sebelum mulai berpetualang dan mabar, kamu **wajib** milih program studi dulu nih.\n\n"
                "👉 **Silakan pilih satu role jurusan di bawah!**\n"
                "*(Tombol ini dikunci khusus untukmu, dan pesan ini nggak akan hilang sampai kamu milih jurusan)*"
            ),
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        # Panggil View yang sudah dikunci untuk member ini
        view = WelcomeRoleView(target_member=member, bot=self.bot)
        
        # Kirim sapaan beserta tombolnya
        await channel.send(content=f"Cek di mari ngab {member.mention}!", embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Welcome(bot))