import discord
from discord.ext import commands
from datetime import datetime
import asyncpg
import os
import asyncio

# ====================================================================
# KONFIGURASI ROLE PRODI (WAJIB DIISI)
# Masukkan ID Role untuk masing-masing Prodi (DKV, TEKINFO, dll) di dalam list ini.
# Cara dapat ID Role: Pengaturan Server -> Roles -> Klik kanan Role -> Copy Role ID
# ====================================================================
PRODI_ROLE_IDS = [
    1526565350731284532, # Ganti dengan ID Role Prodi DKV
    1526566212077879438, # Ganti dengan ID Role Prodi TEKINFO
    1526566441040478352, # Ganti dengan ID Role Prodi SISFOR
    1526566818024783872  # Ganti dengan ID Role Prodi TEKTEL
]

class VoiceLog(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.voice_sessions = {}
        self.is_ready = False # Pengaman agar tidak dijalankan ganda saat reconnect

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_ready:
            # 1. Buat tabel jika belum ada
            await self.pool.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    tanggal TEXT,
                    channel_id BIGINT,
                    user_id BIGINT,
                    durasi REAL
                )
            ''')
            
            # 2. Jalankan tugas sinkronisasi otomatis
            asyncio.create_task(self.sync_active_sessions())
            self.is_ready = True

    async def sync_active_sessions(self):
        """Fitur baru: Memasukkan user yang sudah ada di VC saat bot restart"""
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for member in vc.members:
                    # Masukkan member ke sistem log jika dia bukan bot dan belum tercatat
                    if not member.bot and member.id not in self.voice_sessions:
                        self.voice_sessions[member.id] = {
                            "start_time": datetime.now(),
                            "channel_id": vc.id
                        }
        print("✅ Sinkronisasi Voice Channel selesai! User aktif berhasil dilacak ulang.")

    def get_today_date(self):
        return datetime.now().strftime('%Y-%m-%d')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        # JIKA USER KELUAR VC ATAU PINDAH VC
        if before.channel is not None and before.channel != after.channel:
            if member.id in self.voice_sessions:
                session = self.voice_sessions.pop(member.id)
                durasi_sesi = (datetime.now() - session["start_time"]).total_seconds()
                tanggal_hari_ini = self.get_today_date()
                
                # --- INTEGRASI LEVELING ---
                leveling_cog = self.bot.get_cog('Leveling')
                if leveling_cog:
                    # 1 XP per 120 detik (2 menit)
                    xp_to_add = int(durasi_sesi // 120) 
                    if xp_to_add > 0:
                        await leveling_cog.give_xp(member.id, xp_to_add, member)
                
                await self.pool.execute(
                    "INSERT INTO history (tanggal, channel_id, user_id, durasi) VALUES ($1, $2, $3, $4)", 
                    tanggal_hari_ini, session["channel_id"], member.id, durasi_sesi
                )

        # JIKA USER MASUK VC ATAU PINDAH VC
        if after.channel is not None and before.channel != after.channel:
            self.voice_sessions[member.id] = {
                "start_time": datetime.now(),
                "channel_id": after.channel.id
            }

    # >>> SISTEM FILTERING DITAMBAHKAN DI SINI <<<
    def build_embed(self, guild, requester, judul, data_durasi, real_time_sessions=None):
        embed = discord.Embed(title=judul, color=discord.Color.blue())
        
        # Cek izin admin
        is_admin = requester.guild_permissions.administrator
        # Cari tahu role prodi si peminta
        requester_prodi_roles = set([r.id for r in requester.roles if r.id in PRODI_ROLE_IDS])

        if real_time_sessions:
            for uid, session in real_time_sessions.items():
                ongoing_duration = (datetime.now() - session["start_time"]).total_seconds()
                chan_id = session["channel_id"]
                if chan_id not in data_durasi:
                    data_durasi[chan_id] = {}
                data_durasi[chan_id][uid] = data_durasi[chan_id].get(uid, 0) + ongoing_duration

        if not data_durasi:
            embed.description = "Belum ada data di tanggal ini."
            return embed

        for chan_id, users in data_durasi.items():
            channel = guild.get_channel(chan_id)
            
            # FILTER 1: Lewati channel yang tidak bisa dilihat oleh requester (Kecuali Admin)
            if not is_admin and channel and not channel.permissions_for(requester).view_channel:
                continue
                
            channel_name = channel.name if channel else f"Channel ({chan_id})"
            sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
            teks_channel = ""
            count = 0
            
            for uid, secs in sorted_users:
                # Batasi maksimal 10 orang per channel
                if count >= 10: break
                
                member = guild.get_member(uid)
                
                # FILTER 2: Filter berdasarkan Role Prodi
                if not is_admin:
                    if requester_prodi_roles:
                        if member:
                            member_prodi_roles = set([r.id for r in member.roles if r.id in PRODI_ROLE_IDS])
                            # Jika tidak ada irisan role prodi, sembunyikan user ini
                            if not requester_prodi_roles.intersection(member_prodi_roles):
                                continue 
                        else:
                            # Jika user sudah keluar dari server, sembunyikan untuk keamanan privasi
                            continue 
                    else:
                        # Jika si peminta tidak punya role prodi, dia hanya boleh melihat dirinya sendiri
                        if uid != requester.id:
                            continue

                nama_user = member.display_name if member else f"ID: {uid}"
                jam, menit, detik = int(secs // 3600), int((secs % 3600) // 60), int(secs % 60)
                waktu = f"{f'**{jam}j** ' if jam > 0 else ''}{f'**{menit}m** ' if menit > 0 else ''}**{detik}d**"
                teks_channel += f"👤 **{nama_user}** : {waktu}\n"
                count += 1
                
            if teks_channel != "":
                embed.add_field(name=f"🔊 {channel_name}", value=teks_channel, inline=False)
                
        # Jika setelah difilter embed kosong
        if len(embed.fields) == 0:
            embed.description = "Tidak ada log aktivitas dari Prodimu pada tanggal ini."

        return embed

    @commands.command()
    async def vclog(self, ctx, arg=None):
        # 1. Hapus pesan/chat perintah dari user secara instan
        try:
            await ctx.message.delete()
        except Exception:
            pass

        tanggal_hari_ini = self.get_today_date()
        
        if arg and arg.lower() == "history":
            records = await self.pool.fetch("SELECT DISTINCT tanggal FROM history ORDER BY tanggal DESC LIMIT 25")
            tanggal_tersedia = [row['tanggal'] for row in records]
            if tanggal_hari_ini not in tanggal_tersedia and self.voice_sessions:
                tanggal_tersedia.insert(0, tanggal_hari_ini)
            
            if not tanggal_tersedia:
                msg = await ctx.send("Belum ada data history yang tersimpan.")
                await asyncio.sleep(10)
                try: 
                    await msg.delete() 
                except: 
                    pass
                return
            
            view = HistoryView(self, tanggal_tersedia)
            msg = await ctx.send("Pilih tanggal history yang ingin kamu lihat:", view=view)
            
            # Simpan referensi pesan ke dalam view agar bisa dihapus jika timeout
            view.message = msg 
            
        else:
            records = await self.pool.fetch("SELECT channel_id, user_id, SUM(durasi) as total_durasi FROM history WHERE tanggal = $1 GROUP BY channel_id, user_id", tanggal_hari_ini)
            
            data_hari_ini = {}
            for row in records:
                if row['channel_id'] not in data_hari_ini: 
                    data_hari_ini[row['channel_id']] = {}
                data_hari_ini[row['channel_id']][row['user_id']] = row['total_durasi']
            
            # Pass guild dan requester (ctx.author) untuk memfilter log
            embed = self.build_embed(ctx.guild, ctx.author, f"📊 Statistik VC: Hari Ini ({tanggal_hari_ini})", data_hari_ini, self.voice_sessions)
            msg = await ctx.send(embed=embed)
            
            # 2. Hapus embed balasan hari ini setelah 30 detik
            await asyncio.sleep(30)
            try:
                await msg.delete()
            except:
                pass


class HistoryDropdown(discord.ui.Select):
    def __init__(self, cog_instance, tanggal_list):
        self.cog_instance = cog_instance
        opsi = [discord.SelectOption(label=tgl, description=f"Lihat statistik pada {tgl}") for tgl in tanggal_list]
        super().__init__(placeholder="Pilih Tanggal History...", min_values=1, max_values=1, options=opsi)

    async def callback(self, interaction: discord.Interaction):
        tanggal_dipilih = self.values[0]
        records = await self.cog_instance.pool.fetch("SELECT channel_id, user_id, SUM(durasi) as total_durasi FROM history WHERE tanggal = $1 GROUP BY channel_id, user_id", tanggal_dipilih)
        
        data_history = {}
        for row in records:
            if row['channel_id'] not in data_history: 
                data_history[row['channel_id']] = {}
            data_history[row['channel_id']][row['user_id']] = row['total_durasi']
        
        sesi_realtime = self.cog_instance.voice_sessions if tanggal_dipilih == self.cog_instance.get_today_date() else None
        
        # Pass guild dan requester (interaction.user) untuk memfilter log
        embed = self.cog_instance.build_embed(interaction.guild, interaction.user, f"📜 History VC: {tanggal_dipilih}", data_history, sesi_realtime)
        
        await interaction.response.edit_message(content=f"Menampilkan data untuk **{tanggal_dipilih}**:", embed=embed, view=None)

        # 3. Hapus embed balasan history setelah 30 detik dari saat user memilih
        await asyncio.sleep(30)
        try:
            await interaction.message.delete()
        except:
            pass


class HistoryView(discord.ui.View):
    def __init__(self, cog_instance, tanggal_list):
        super().__init__(timeout=30.0) 
        self.message = None
        self.add_item(HistoryDropdown(cog_instance, tanggal_list))

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.delete()
            except:
                pass


async def setup(bot):
    await bot.add_cog(VoiceLog(bot, bot.pool))