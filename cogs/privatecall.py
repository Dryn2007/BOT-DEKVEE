import discord
from discord.ext import commands
import asyncio
import traceback

# ====================================================================
# 0. KONFIGURASI ID
# ====================================================================
ROOM_CALL_ID = 1528283280003174560 # ID Channel tempat dashboard 4 Grid berada
CATEGORY_PRIVAT_ID = 1528284380022313011 # ID Kategori tempat Voice Channel akan dibuat

# ====================================================================
# 1. MODAL (POP-UP) UNTUK INPUT NAMA ROOM
# ====================================================================
class RoomNameModal(discord.ui.Modal, title='Custom Nama Room Privat'):
    room_name = discord.ui.TextInput(
        label='Masukkan Nama Room (Maks 30 huruf)',
        placeholder='Contoh: Mabar Valorant Santai',
        required=True,
        min_length=1,
        max_length=30
    )

    def __init__(self, main_view, grid_index, cog, selected_users):
        super().__init__()
        self.main_view = main_view
        self.grid_index = grid_index
        self.cog = cog
        self.selected_users = selected_users

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        creator = interaction.user
        
        # Format nama: 📞・< custom user > Privat
        custom_name = self.room_name.value
        full_name = f"📞・{custom_name} Privat"

        # 1. Atur Hak Akses
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False), 
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True), 
            creator: discord.PermissionOverwrite(view_channel=True, connect=True) 
        }
        
        for user in self.selected_users:
            overwrites[user] = discord.PermissionOverwrite(view_channel=True, connect=True)

        category = guild.get_channel(CATEGORY_PRIVAT_ID)

        try:
            # 2. Buat Voice Channel Baru
            vc = await guild.create_voice_channel(
                name=full_name,
                category=category,
                overwrites=overwrites,
                reason="Auto Private Call System"
            )
            
            # 3. Mulai Timer Grace Period (3 Menit)
            task = self.cog.bot.loop.create_task(self.cog.auto_delete_vc(vc.id, 180.0))
            self.cog.active_vcs[vc.id] = task

            # 4. Lepaskan Grid
            await self.main_view.unlock_grid(self.grid_index)

            # 5. Ubah pesan UI menjadi Sukses
            embed = discord.Embed(
                title="🎉 Room Berhasil Dibuat!",
                description=f"Room privat kamu telah siap: {vc.mention}\n\n*Catatan: Room ini terlihat oleh publik tapi digembok. Room akan hancur jika kosong, atau jika hanya kamu sendirian di dalam sana selama 3 menit.*",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)

            # 6. SIMPAN INTERAKSI INI UNTUK DIHAPUS NANTI SAAT ROOM HANCUR
            self.cog.success_interactions[vc.id] = interaction
            
        except Exception as e:
            print(f"[PrivateCall Modal] Gagal membuat VC: {e!r}")
            await interaction.response.edit_message(content="❌ Terjadi kesalahan saat membuat room. Pastikan bot punya izin `Manage Channels` dan ID Kategori benar.", embed=None, view=None)
            await self.main_view.unlock_grid(self.grid_index)
            # Hapus pesan error dalam 5 detik
            await asyncio.sleep(5)
            try: await interaction.delete_original_response()
            except: pass


# ====================================================================
# 2. MENU PEMILIHAN TEMAN
# ====================================================================
class PrivateCallConfigView(discord.ui.View):
    def __init__(self, main_view, grid_index, cog):
        super().__init__(timeout=120.0) 
        self.main_view = main_view
        self.grid_index = grid_index
        self.cog = cog
        self.selected_users = []

        self.select_users = discord.ui.UserSelect(
            placeholder="Tag teman (Kosongkan jika untuk sendiri)...", 
            min_values=2, # << BISA BIKIN ROOM SENDIRIAN TANPA TAG ORANG
            max_values=10
        )
        self.select_users.callback = self.select_callback
        self.add_item(self.select_users)

        self.btn_create = discord.ui.Button(label="Buat Room Sekarang", style=discord.ButtonStyle.success, emoji="✅")
        self.btn_create.callback = self.create_callback
        self.add_item(self.btn_create)

        self.btn_cancel = discord.ui.Button(label="Batal", style=discord.ButtonStyle.danger, emoji="✖️")
        self.btn_cancel.callback = self.cancel_callback
        self.add_item(self.btn_cancel)

    async def select_callback(self, interaction: discord.Interaction):
        self.selected_users = self.select_users.values
        await interaction.response.defer() 

    async def create_callback(self, interaction: discord.Interaction):
        # Ambil user langsung agar tidak ada bug "belum memilih siapapun"
        selected_users = self.select_users.values
        
        modal = RoomNameModal(self.main_view, self.grid_index, self.cog, selected_users)
        await interaction.response.send_modal(modal)

    async def cancel_callback(self, interaction: discord.Interaction):
        await self.main_view.unlock_grid(self.grid_index)
        try:
            await interaction.response.defer()
            await interaction.delete_original_response()
        except:
            pass

    async def on_timeout(self):
        await self.main_view.unlock_grid(self.grid_index)


# ====================================================================
# 3. DASHBOARD 4 GRID
# ====================================================================
class MainPrivateCallDashboard(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        
        self.grid_status = [None, None, None, None] 
        self.grid_tasks = [None, None, None, None]

        for i in range(4):
            btn = discord.ui.Button(
                label=f"Grid {i+1} (Tersedia)",
                style=discord.ButtonStyle.success,
                custom_id=f"privcall_grid_{i}",
                row=i // 2
            )
            btn.callback = self.make_callback(i)
            self.add_item(btn)

    def make_callback(self, index):
        async def callback(interaction: discord.Interaction):
            user_id = interaction.user.id

            if self.grid_status[index] is not None:
                if self.grid_status[index] != user_id:
                    # PERINGATAN HILANG DALAM 3 DETIK
                    await interaction.response.send_message("⛔ **Loket ini sedang dipakai orang lain!** Silakan pilih Grid yang berwarna hijau.", ephemeral=True)
                    await asyncio.sleep(3)
                    try: await interaction.delete_original_response()
                    except: pass
                    return
            else:
                for i, status in enumerate(self.grid_status):
                    if status == user_id:
                        # PERINGATAN HILANG DALAM 3 DETIK
                        await interaction.response.send_message(f"⚠️ **Kamu masih membuka menu di Grid {i+1}!** Selesaikan dulu di sana.", ephemeral=True)
                        await asyncio.sleep(3)
                        try: await interaction.delete_original_response()
                        except: pass
                        return

                self.grid_status[index] = user_id
                button = self.children[index]
                
                nama = interaction.user.display_name[:10]
                button.label = f"🔒 Dipakai {nama}"
                button.style = discord.ButtonStyle.secondary
                
                await interaction.response.edit_message(view=self)

            config_view = PrivateCallConfigView(self, index, self.cog)
            embed_intro = discord.Embed(
                title="📞 Buat Panggilan Privat",
                description="1. Pilih teman yang boleh masuk di menu bawah.\n2. Klik **Buat Room Sekarang**.\n3. Masukkan nama room sesukamu.\n\n*(Waktu pengisian: 120 Detik)*",
                color=discord.Color.brand_green()
            )
            
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed_intro, view=config_view, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed_intro, view=config_view, ephemeral=True)

            if self.grid_tasks[index]:
                self.grid_tasks[index].cancel()
            self.grid_tasks[index] = self.cog.bot.loop.create_task(self.timer_logic(index))

        return callback

    async def timer_logic(self, index):
        try:
            await asyncio.sleep(120.0) 
            await self.unlock_grid(index)
        except asyncio.CancelledError:
            pass

    async def unlock_grid(self, index):
        if self.grid_status[index] is None:
            return

        self.grid_status[index] = None
        button = self.children[index]
        button.label = f"Grid {index+1} (Tersedia)"
        button.style = discord.ButtonStyle.success

        if self.grid_tasks[index]:
            self.grid_tasks[index].cancel()
            self.grid_tasks[index] = None

        if self.cog.dashboard_message:
            try:
                await self.cog.dashboard_message.edit(view=self)
            except Exception:
                pass


# ====================================================================
# 4. COG UTAMA & PENGHANCUR ROOM OTOMATIS (AUTO-SWEEP)
# ====================================================================
class PrivateCallCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dashboard_message = None
        self.is_spawned = False
        self.active_vcs = {} 
        self.success_interactions = {} # << MENYIMPAN PESAN SUKSES UNTUK DIHAPUS NANTI

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_spawned:
            self.is_spawned = True
            await asyncio.sleep(3)
            await self.spawn_dashboard()

    async def spawn_dashboard(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(ROOM_CALL_ID)
        if not channel:
            try: channel = await self.bot.fetch_channel(ROOM_CALL_ID)
            except Exception: return

        try: await channel.purge(limit=100)
        except: pass

        embed = discord.Embed(
            title="📞 Pusat Panggilan Privat",
            description="Ingin mengobrol tanpa diganggu orang lain?\n\nKlik salah satu **Grid Hijau** di bawah ini untuk merakit *Voice Channel* rahasiamu sendiri. Kamu bisa men-tag siapa saja yang boleh masuk ke dalam room tersebut!",
            color=discord.Color.blurple()
        )

        view = MainPrivateCallDashboard(self)
        self.dashboard_message = await channel.send(embed=embed, view=view, silent=True)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def spawncall(self, ctx):
        try: await ctx.message.delete()
        except: pass
        await self.spawn_dashboard()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot: return
        if message.content.startswith("!spawncall"): return
        if message.channel.id == ROOM_CALL_ID:
            try: await message.delete()
            except: pass

    # FUNGSI UNTUK MENGHAPUS PESAN "ROOM BERHASIL DIBUAT"
    async def delete_success_msg(self, vc_id):
        if vc_id in self.success_interactions:
            inter = self.success_interactions.pop(vc_id)
            try:
                await inter.delete_original_response()
            except Exception:
                pass # Expired setelah 15 menit oleh Discord

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel and before.channel.id in self.active_vcs:
            await self.check_vc_status(before.channel)

        if after.channel and after.channel.id in self.active_vcs:
            await self.check_vc_status(after.channel)

    async def check_vc_status(self, vc):
        members_count = len([m for m in vc.members if not m.bot]) 

        if self.active_vcs[vc.id] is not None:
            self.active_vcs[vc.id].cancel()
            self.active_vcs[vc.id] = None

        if members_count == 0:
            try: await vc.delete()
            except Exception: pass
            
            self.active_vcs.pop(vc.id, None)
            # Hapus notif "Room Berhasil Dibuat"
            await self.delete_success_msg(vc.id)

        elif members_count == 1:
            task = self.bot.loop.create_task(self.auto_delete_vc(vc.id, 180.0))
            self.active_vcs[vc.id] = task

    async def auto_delete_vc(self, vc_id, delay):
        try:
            await asyncio.sleep(delay)
            vc = self.bot.get_channel(vc_id)
            if vc:
                try: await vc.delete()
                except Exception: pass
                
            self.active_vcs.pop(vc_id, None)
            # Hapus notif "Room Berhasil Dibuat"
            await self.delete_success_msg(vc_id)
        except asyncio.CancelledError:
            pass


async def setup(bot):
    await bot.add_cog(PrivateCallCog(bot))