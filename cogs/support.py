import discord
from discord.ext import commands
import asyncio
import os

# ====================================================================
# 0. KONFIGURASI ID (WAJIB DIISI)
# ====================================================================
# 1. ID Channel tempat tombol "Buka Tiket" berada (Room 1: 🎫・open-a-ticket)
ROOM_TIKET_ID = 1528366284520030281 

# 2. ID Channel tempat informasi Donasi dipajang (Room 2: ☕・buy-us-coffee)
ROOM_DONASI_ID = 1528366365566701651 

# 3. ID KATEGORI tempat room Tiket rahasia akan dibuat oleh bot (🏢 DEKVEE HELPDESK)
TICKET_CATEGORY_ID = 1528368381626159295 

# 4. ID ROLE ADMIN (Agar admin otomatis masuk ke dalam tiket chat)
ADMIN_ROLE_ID = 1528369037032165499 


# ====================================================================
# 1. TOMBOL DI DALAM ROOM TIKET RAHASIA (UNTUK TUTUP TIKET)
# ====================================================================
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Tutup Tiket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="btn_tutup_tiket")
    async def close_ticket_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🔒 Menutup Tiket...",
            description="Tiket ini akan dihapus secara permanen dalam **5 detik**.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason="Tiket ditutup oleh user/admin")
        except:
            pass


# ====================================================================
# 2. TOMBOL UNTUK MEMBUAT TIKET (DI ROOM 1)
# ====================================================================
class CreateTicketView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Open a Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="btn_buka_tiket")
    async def btn_tiket_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        category = guild.get_channel(TICKET_CATEGORY_ID)

        if not category:
            await interaction.response.send_message("❌ **Error:** Kategori Tiket tidak ditemukan. Lapor ke Admin!", ephemeral=True)
            return

        ticket_name = f"ticket-{user.name.lower()}"
        existing_channel = discord.utils.get(category.channels, name=ticket_name)
        
        if existing_channel:
            await interaction.response.send_message(f"⚠️ Kamu sudah memiliki tiket yang terbuka di {existing_channel.mention}!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        admin_role = guild.get_role(ADMIN_ROLE_ID)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }
        
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        try:
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites,
                reason=f"Tiket dibuat oleh {user.name}"
            )
        except Exception as e:
            await interaction.followup.send("❌ Gagal membuat tiket. Pastikan bot punya izin `Manage Channels`.", ephemeral=True)
            return

        await interaction.followup.send(f"✅ **Tiket Berhasil Dibuat!** Silakan masuk ke {ticket_channel.mention} untuk mulai *chat* dengan Admin.", ephemeral=True)

        embed_welcome = discord.Embed(
            title="🎫 Tiket Dukungan Baru",
            description=(
                f"Halo {user.mention}!\n\n"
                "Silakan jelaskan masalah, pertanyaan, atau idemu secara detail di sini. "
                "Admin akan membalas pesanmu sesegera mungkin.\n\n"
                "*Jika masalah sudah selesai, klik tombol **Tutup Tiket** di bawah.*"
            ),
            color=discord.Color.green()
        )
        
        ping_text = f"{user.mention}"
        if admin_role:
            ping_text += f" | {admin_role.mention}"
            
        await ticket_channel.send(content=ping_text, embed=embed_welcome, view=TicketControlView())


# ====================================================================
# 3. COG UTAMA
# ====================================================================
class SupportSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.is_spawned = False
        self.bot.add_view(TicketControlView()) 

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_spawned:
            self.is_spawned = True
            await asyncio.sleep(4) 
            await self.spawn_dashboard()

    async def spawn_dashboard(self):
        await self.bot.wait_until_ready()
        
        channel_tiket = self.bot.get_channel(ROOM_TIKET_ID)
        channel_donasi = self.bot.get_channel(ROOM_DONASI_ID)
        
        # --- 1. SETUP ROOM TIKET ---
        if channel_tiket:
            try: await channel_tiket.purge(limit=50)
            except: pass

            embed_tiket = discord.Embed(
                title="📞 Pusat Bantuan & Support Admin",
                description=(
                    "Ada pertanyaan, butuh bantuan verifikasi, masalah akun, atau mau ngasih ide buat bot?\n\n"
                    "👉 Klik tombol **Open a Ticket** di bawah ini untuk membuka *room chat private* dengan tim kami.\n\n"
                    "*(Pesanmu akan sepenuhnya rahasia dan hanya bisa dilihat oleh Admin)*"
                ),
                color=discord.Color.blurple()
            )
            view_tiket = CreateTicketView(self)
            await channel_tiket.send(embed=embed_tiket, view=view_tiket, silent=True)

        # --- 2. SETUP ROOM DONASI (DENGAN GAMBAR QRIS) ---
        if channel_donasi:
            try: await channel_donasi.purge(limit=50)
            except: pass

            embed_donasi = discord.Embed(
                title="☕ Support Server & Buy Us a Coffee",
                description=(
                    "Thank you for using **DekVee** and being part of our community!\n\n"
                    "Jika kamu merasa terbantu dengan fitur bot dan ingin mendukung kelangsungan server ini, kamu bisa berdonasi melalui QRIS di bawah ini:\n\n"
                    "💳 **QRIS a.n DRII - Art**\n"
                    "*(Support All Payment: GoPay, OVO, Dana, ShopeePay, LinkAja, Mobile Banking, dll)*\n\n"
                    "*Berapapun dukunganmu sangat berarti buat biaya operasional server dan uang kopi developer! ❤️*"
                ),
                color=discord.Color.gold()
            )
            
            # --- LOGIKA ATTACH GAMBAR LOKAL ---
            # Pastikan file image_946309.jpg berada di dalam folder yang sama dengan file bot utama (main.py)
            nama_file_qris = "image_946309.jpg" 
            
            if os.path.exists(nama_file_qris):
                file = discord.File(nama_file_qris, filename="qris_donasi.jpg")
                embed_donasi.set_image(url="attachment://qris_donasi.jpg")
                await channel_donasi.send(file=file, embed=embed_donasi, silent=True)
            else:
                # Jika lupa taruh gambar, bot tetap kirim teks tanpa gambar agar tidak error
                print(f"[WARNING] File {nama_file_qris} tidak ditemukan! Pastikan sudah ditaruh di folder bot.")
                await channel_donasi.send(embed=embed_donasi, silent=True)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def spawnsupport(self, ctx):
        """Memunculkan dashboard support secara paksa"""
        try: await ctx.message.delete()
        except: pass
        await self.spawn_dashboard()

    # Mencegah member nge-chat di kedua channel tersebut (Auto-Clean)
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if message.content.startswith("!spawnsupport"): return
        
        if message.channel.id in [ROOM_TIKET_ID, ROOM_DONASI_ID]:
            try: await message.delete()
            except: pass


async def setup(bot):
    await bot.add_cog(SupportSystem(bot))