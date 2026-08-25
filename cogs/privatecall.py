import discord
from discord.ext import commands, tasks
import asyncio
import traceback
import sqlite3
import time

# ID kategori & channel log dipusatkan di roomconfig.py (root repo) supaya
# cog lain (voicelog, leveling, afkkick) tidak menyalin ID-nya lagi.
from roomconfig import PRIVATE_CALL_CATEGORY_ID as CATEGORY_PRIVAT_ID, LOG_CHANNEL_ID

# ====================================================================
# 0. KONFIGURASI ID & DATABASE
# ====================================================================
ROOM_CALL_ID = 1528283280003174560
DB_FILE = "private_calls.db"

# Inisialisasi Database
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                vc_id INTEGER PRIMARY KEY,
                creator_id INTEGER,
                expire_time REAL,
                empty_since REAL
            )
        """)
        conn.commit()

init_db()


# ====================================================================
# 0b. HELPER AKSES ROOM
# ====================================================================
def user_has_room_access(vc, member) -> bool:
    """True kalau member memang bagian dari room ini (boleh mengundang orang lain)."""
    if member in vc.members:                        # sedang nongkrong di dalam room
        return True
    if vc.overwrites_for(member).view_channel:      # diundang saat room dibuat / ditambah belakangan
        return True
    return bool(member.guild_permissions.manage_channels)


# ====================================================================
# 1. VIEW UNTUK TOMBOL KONTROL ROOM (PERSISTENT)
# ====================================================================
class DeleteRoomView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Tambah User", style=discord.ButtonStyle.primary, emoji="➕", custom_id="add_private_room_user")
    async def btn_add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.channel
        cog = interaction.client.get_cog("PrivateCallCog")

        if cog is None or not isinstance(vc, discord.VoiceChannel) or vc.id not in cog.active_rooms:
            await interaction.response.send_message(
                "❌ Data room tidak ditemukan atau sudah kadaluarsa.", ephemeral=True
            )
            return

        if not user_has_room_access(vc, interaction.user):
            await interaction.response.send_message(
                "❌ Hanya orang yang ada di dalam room ini yang bisa menambahkan user baru!", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="➕ Tambah User ke Room Ini",
            description=(
                "Pilih user yang mau diundang (maksimal 10 sekali kirim).\n\n"
                "User yang dipilih langsung bisa **melihat** dan **masuk** ke room ini."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(
            embed=embed, view=AddUserView(vc, interaction.user), ephemeral=True
        )

    @discord.ui.button(label="Hapus Room Sekarang", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="delete_private_room")
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc_id = interaction.channel.id
        
        # Cek database siapa pembuatnya
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT creator_id FROM rooms WHERE vc_id = ?", (vc_id,))
            row = cursor.fetchone()

        if row:
            creator_id = row[0]
            if interaction.user.id == creator_id or interaction.user.guild_permissions.manage_channels:
                await interaction.response.send_message("⏳ Menghapus room...", ephemeral=True)
                try: 
                    await interaction.channel.delete(reason="Dihapus manual oleh pemilik room.")
                except Exception: 
                    pass
                # Data DB otomatis terhapus lewat event on_guild_channel_delete
            else:
                await interaction.response.send_message("❌ Hanya pembuat room yang bisa menggunakan tombol ini!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Data room tidak ditemukan atau sudah kadaluarsa.", ephemeral=True)


# ====================================================================
# 1b. VIEW UNTUK MENAMBAH USER KE ROOM YANG SUDAH JALAN
# ====================================================================
class AddUserView(discord.ui.View):
    def __init__(self, vc, invoker):
        super().__init__(timeout=180.0)
        self.vc = vc
        self.invoker = invoker

        self.select_users = discord.ui.UserSelect(
            placeholder="Pilih user yang mau diundang...",
            min_values=1,
            max_values=10,
            row=0
        )
        self.select_users.callback = self.defer_callback
        self.add_item(self.select_users)

        self.btn_add = discord.ui.Button(label="Tambah", style=discord.ButtonStyle.success, emoji="✅", row=1)
        self.btn_add.callback = self.add_callback
        self.add_item(self.btn_add)

        self.btn_cancel = discord.ui.Button(label="Batal", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)
        self.btn_cancel.callback = self.cancel_callback
        self.add_item(self.btn_cancel)

    async def defer_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    async def cancel_callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="❌ Dibatalkan. Tidak ada user yang ditambahkan.", embed=None, view=None
        )

    async def add_callback(self, interaction: discord.Interaction):
        if not self.select_users.values:
            await interaction.response.send_message(
                "⚠️ Pilih dulu minimal 1 user dari dropdown di atas.", ephemeral=True
            )
            return

        await interaction.response.defer()
        guild = interaction.guild
        ditambahkan, dilewati = [], []

        for user in self.select_users.values:
            member = guild.get_member(user.id)
            if member is None:
                dilewati.append(f"{user.mention} (bukan member server)")
            elif member.bot:
                dilewati.append(f"{member.mention} (bot)")
            elif self.vc.overwrites_for(member).view_channel:
                dilewati.append(f"{member.mention} (sudah punya akses)")
            else:
                try:
                    await self.vc.set_permissions(
                        member,
                        view_channel=True,
                        connect=True,
                        reason=f"Ditambahkan ke room privat oleh {interaction.user}"
                    )
                    ditambahkan.append(member)
                except Exception as e:
                    dilewati.append(f"{member.mention} (gagal: `{e.__class__.__name__}`)")

        await self.finish(interaction, ditambahkan, dilewati)

    async def finish(self, interaction: discord.Interaction, ditambahkan, dilewati):
        embed = discord.Embed(title="➕ Hasil Tambah User", color=discord.Color.green() if ditambahkan else discord.Color.orange())
        if ditambahkan:
            embed.add_field(
                name=f"✅ Berhasil ditambahkan ({len(ditambahkan)})",
                value="\n".join(f"• {m.mention}" for m in ditambahkan),
                inline=False
            )
        if dilewati:
            embed.add_field(
                name=f"⚠️ Dilewati ({len(dilewati)})",
                value="\n".join(f"• {t}" for t in dilewati),
                inline=False
            )

        try:
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:
            pass

        if not ditambahkan:
            return

        mentions = ", ".join(m.mention for m in ditambahkan)

        # Notifikasi publik di chat room (mention bikin user baru langsung ke-ping)
        try:
            await self.vc.send(
                f"➕ {mentions} ditambahkan ke room ini oleh {interaction.user.mention}. Selamat datang!"
            )
        except Exception:
            pass

        cog = interaction.client.get_cog("PrivateCallCog")
        if cog:
            await cog.log_to_admin(
                "➕ User Ditambahkan ke Room Privat",
                f"**Room:** {self.vc.mention} (`{self.vc.name}`)\n"
                f"**Ditambahkan oleh:** {interaction.user.mention}\n"
                f"**User baru:** {mentions}",
                discord.Color.blurple()
            )


# ====================================================================
# 2. MODAL (POP-UP) UNTUK INPUT NAMA ROOM
# ====================================================================
class RoomNameModal(discord.ui.Modal, title='Custom Nama Room Privat'):
    room_name = discord.ui.TextInput(
        label='Masukkan Nama Room (Maks 30 huruf)',
        placeholder='Contoh: Mabar Valorant Santai',
        required=True,
        min_length=1,
        max_length=30
    )

    def __init__(self, main_view, grid_index, cog, selected_users, duration_seconds, duration_label):
        super().__init__()
        self.main_view = main_view
        self.grid_index = grid_index
        self.cog = cog
        self.selected_users = selected_users
        self.duration_seconds = duration_seconds
        self.duration_label = duration_label

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        creator = interaction.user
        
        custom_name = self.room_name.value
        full_name = f"📞・{custom_name} Privat"

        # 1. Atur Hak Akses: HANYA pembuat dan teman yang di-tag yang bisa melihat
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False), 
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
            
            # 3. Simpan ke Database
            now = time.time()
            expire_time = now + self.duration_seconds
            empty_since = now # Mulai dihitung kosong sampai creator masuk

            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "INSERT INTO rooms (vc_id, creator_id, expire_time, empty_since) VALUES (?, ?, ?, ?)", 
                    (vc.id, creator.id, expire_time, empty_since)
                )
                conn.commit()
            
            self.cog.active_rooms.add(vc.id)

            # 4. Kirim dan Pin pesan di dalam Room
            embed_pin = discord.Embed(
                title="⚙️ Kontrol Room Privat",
                description=(
                    f"Room ini **tersembunyi dari publik**.\n\n"
                    f"➕ **Tambah User:** siapa pun yang ada di room ini boleh mengundang teman lain lewat tombol di bawah.\n\n"
                    f"⚠️ **Perhatian:**\n"
                    f"* Room ini akan otomatis terhapus jika kosong (tidak ada satupun orang) selama **1 Hari (24 Jam)**.\n"
                    f"* Room ini memiliki batas hidup maksimum selama **{self.duration_label}**.\n\n"
                    f"Klik tombol 🗑️ di bawah jika kamu ingin menghapusnya lebih awal."
                ),
                color=discord.Color.red()
            )
            try:
                msg = await vc.send(content=f"Selamat datang, {creator.mention}!", embed=embed_pin, view=DeleteRoomView())
                await msg.pin()
            except Exception as e:
                print(f"Gagal mengirim/pin pesan di dalam room: {e}")

            # 5. Lepaskan Grid
            await self.main_view.unlock_grid(self.grid_index)

            # 6. Ubah pesan UI menjadi Sukses
            embed = discord.Embed(
                title="🎉 Room Berhasil Dibuat!",
                description=f"Room privat rahasiamu telah siap: {vc.mention}\n\n*Silakan cek obrolan (chat) di dalam room tersebut untuk kontrol dan info lebih lanjut.*",
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)

            # Simpan log pembuatan room ke channel Admin (opsional)
            await self.cog.log_to_admin("✅ Room Dibuat", f"**Pembuat:** {creator.mention}\n**Room:** {vc.mention} (`{vc.name}`)\n**Durasi Max:** {self.duration_label}", discord.Color.green())

            self.cog.success_interactions[vc.id] = interaction
            
        except Exception as e:
            print(f"[PrivateCall Modal] Gagal membuat VC: {e!r}")
            await interaction.response.edit_message(content="❌ Terjadi kesalahan saat membuat room.", embed=None, view=None)
            await self.main_view.unlock_grid(self.grid_index)
            await asyncio.sleep(5)
            try: await interaction.delete_original_response()
            except: pass


# ====================================================================
# 3. MENU PEMILIHAN TEMAN & DURASI
# ====================================================================
class PrivateCallConfigView(discord.ui.View):
    def __init__(self, main_view, grid_index, cog):
        super().__init__(timeout=600.0) 
        self.main_view = main_view
        self.grid_index = grid_index
        self.cog = cog

        self.select_users = discord.ui.UserSelect(
            placeholder="Tag teman (Kosongkan jika untuk sendiri)...", 
            min_values=0, 
            max_values=10,
            row=0
        )
        self.select_users.callback = self.defer_callback
        self.add_item(self.select_users)

        self.select_duration = discord.ui.Select(
            placeholder="Pilih durasi room maksimum (Wajib)...",
            min_values=1,
            max_values=1,
            row=1,
            options=[
                discord.SelectOption(label="1 Hari", value="86400", description="Max bertahan 24 jam", emoji="🕐"),
                discord.SelectOption(label="3 Hari", value="259200", description="Max bertahan 3 hari", emoji="🕒"),
                discord.SelectOption(label="7 Hari", value="604800", description="Max bertahan 7 hari", emoji="🕖")
            ]
        )
        self.select_duration.callback = self.defer_callback
        self.add_item(self.select_duration)

        self.btn_create = discord.ui.Button(label="Buat Room Sekarang", style=discord.ButtonStyle.success, emoji="✅", row=2)
        self.btn_create.callback = self.create_callback
        self.add_item(self.btn_create)

        self.btn_cancel = discord.ui.Button(label="Batal", style=discord.ButtonStyle.danger, emoji="✖️", row=2)
        self.btn_cancel.callback = self.cancel_callback
        self.add_item(self.btn_cancel)

    async def defer_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

    async def create_callback(self, interaction: discord.Interaction):
        selected_users = self.select_users.values

        if not self.select_duration.values:
            await interaction.response.send_message("⚠️ Silakan pilih **Durasi Room** terlebih dahulu!", ephemeral=True)
            return

        duration_seconds = float(self.select_duration.values[0])
        duration_label = [opt.label for opt in self.select_duration.options if opt.value == self.select_duration.values[0]][0]
        
        modal = RoomNameModal(self.main_view, self.grid_index, self.cog, selected_users, duration_seconds, duration_label)
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
# 4. DASHBOARD 4 GRID
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
                    await interaction.response.send_message("⛔ **Loket ini sedang dipakai orang lain!** Silakan pilih Grid yang berwarna hijau.", ephemeral=True)
                    await asyncio.sleep(3)
                    try: await interaction.delete_original_response()
                    except: pass
                    return
            else:
                for i, status in enumerate(self.grid_status):
                    if status == user_id:
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
                title="📞 Buat Panggilan Privat Rahasia",
                description="1. Pilih teman yang boleh masuk (Orang lain TIDAK BISA melihat room ini).\n2. Pilih durasi maksimum room.\n3. Klik **Buat Room Sekarang** dan masukkan nama.\n\n*(Waktu pengisian: 10 Menit)*",
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
            await asyncio.sleep(600.0) 
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
            try: await self.cog.dashboard_message.edit(view=self)
            except Exception: pass


# ====================================================================
# 5. COG UTAMA & DATABASE BACKGROUND LOOP
# ====================================================================
class PrivateCallCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dashboard_message = None
        self.is_spawned = False
        self.success_interactions = {} 
        self.active_rooms = set() 
        
        # Load active rooms from DB
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT vc_id FROM rooms")
            rows = cursor.fetchall()
            for r in rows:
                self.active_rooms.add(r[0])

    async def log_to_admin(self, title, description, color):
        """Fungsi helper untuk mengirim pesan log ke channel khusus"""
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title=title, description=description, color=color)
            embed.timestamp = discord.utils.utcnow()
            try: await log_channel.send(embed=embed)
            except Exception: pass

    async def cog_load(self):
        self.bot.add_view(DeleteRoomView())

    def cog_unload(self):
        self.sweep_rooms_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_spawned:
            self.is_spawned = True
            if not self.sweep_rooms_task.is_running():
                self.sweep_rooms_task.start()
            await asyncio.sleep(3)
            await self.spawn_dashboard()
            await self.refresh_room_controls()

    async def refresh_room_controls(self):
        """Pasang ulang tombol kontrol di pesan pin room yang dibuat sebelum update.

        Komponen tombol tersimpan di message, bukan di class View — jadi room lama
        tetap cuma punya tombol hapus sampai pesannya di-edit ulang.
        """
        INFO_FIELD = "➕ Tambah User"

        for vc_id in list(self.active_rooms):
            vc = self.bot.get_channel(vc_id)
            if not isinstance(vc, discord.VoiceChannel):
                continue

            try:
                # discord.py >= 2.6: pins() adalah async iterator, bukan list
                async for msg in vc.pins(limit=20):
                    if msg.author.id != self.bot.user.id or not msg.embeds:
                        continue
                    embed = msg.embeds[0]
                    if embed.title != "⚙️ Kontrol Room Privat":
                        continue

                    # Room lama: embed-nya belum menyebut fitur tambah user
                    sudah_ada = ("Tambah User" in (embed.description or "")) or \
                                any(INFO_FIELD in f.name for f in embed.fields)
                    if sudah_ada:
                        await msg.edit(view=DeleteRoomView())
                    else:
                        embed.add_field(
                            name=INFO_FIELD,
                            value="Siapa pun yang ada di room ini boleh mengundang teman lain lewat tombol di bawah.",
                            inline=False
                        )
                        await msg.edit(embed=embed, view=DeleteRoomView())
                    break
            except Exception:
                pass

            await asyncio.sleep(1)  # jaga rate limit kalau room-nya banyak

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
            description="Ingin mengobrol tanpa diganggu dan 100% Rahasia?\n\nKlik salah satu **Grid Hijau** di bawah ini untuk merakit *Voice Channel* mu. Hanya kamu dan teman yang di-tag yang bisa melihat dan memasuki room tersebut!",
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

    # LOOP SETIAP 5 MENIT UNTUK CEK KADALUARSA/ROOM KOSONG 24 JAM
    @tasks.loop(minutes=5)
    async def sweep_rooms_task(self):
        now = time.time()
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT vc_id, expire_time, empty_since, creator_id FROM rooms")
            rows = cursor.fetchall()

        for vc_id, expire_time, empty_since, creator_id in rows:
            delete_reason = None
            
            if now > expire_time:
                delete_reason = "Durasi maksimal room telah habis."
            elif empty_since is not None and now > (empty_since + 86400):
                delete_reason = "Room kosong lebih dari 24 jam (1 Hari)."

            if delete_reason:
                vc = self.bot.get_channel(vc_id)
                room_name = vc.name if vc else "Unknown (Sudah Terhapus)"
                
                # Kirim log ke channel admin
                await self.log_to_admin(
                    title="🗑️ Room Otomatis Dihapus",
                    description=f"**Nama Room:** `{room_name}`\n**Pembuat:** <@{creator_id}>\n**Alasan:** {delete_reason}",
                    color=discord.Color.orange()
                )

                if vc:
                    try: await vc.delete(reason=delete_reason)
                    except Exception: pass
                
                # Hapus dari Cache dan Database
                if vc_id in self.active_rooms:
                    self.active_rooms.remove(vc_id)
                with sqlite3.connect(DB_FILE) as conn:
                    conn.execute("DELETE FROM rooms WHERE vc_id = ?", (vc_id,))
                    conn.commit()

    @sweep_rooms_task.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()

    # MENDETEKSI KELUAR MASUK MEMBER UNTUK MENGATUR "EMPTY_SINCE"
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel and before.channel.id in self.active_rooms:
            await self.update_room_empty_status(before.channel)
        if after.channel and after.channel.id in self.active_rooms:
            await self.update_room_empty_status(after.channel)

    async def update_room_empty_status(self, vc):
        members_count = len([m for m in vc.members if not m.bot])
        now = time.time()
        
        with sqlite3.connect(DB_FILE) as conn:
            if members_count == 0:
                conn.execute("UPDATE rooms SET empty_since = ? WHERE vc_id = ?", (now, vc.id))
            else:
                conn.execute("UPDATE rooms SET empty_since = NULL WHERE vc_id = ?", (vc.id,))
            conn.commit()

    # MENGHAPUS DATA DARI DB JIKA ROOM DIHAPUS MANUAL (OLEH ADMIN ATAU TOMBOL)
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if channel.id in self.active_rooms:
            self.active_rooms.remove(channel.id)
            
            # Ambil data creator untuk log manual delete (opsional, jika sempat dicatat DB)
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT creator_id FROM rooms WHERE vc_id = ?", (channel.id,))
                row = cursor.fetchone()
                
                conn.execute("DELETE FROM rooms WHERE vc_id = ?", (channel.id,))
                conn.commit()

            if row:
                await self.log_to_admin(
                    title="🧹 Room Dihapus Manual",
                    description=f"**Nama Room:** `{channel.name}`\n**Pembuat:** <@{row[0]}>\n**Alasan:** Dihapus secara manual oleh pemilik/admin.",
                    color=discord.Color.red()
                )

async def setup(bot):
    await bot.add_cog(PrivateCallCog(bot))