import discord
from discord.ext import commands
from datetime import datetime
import sqlite3

class VoiceLog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_sessions = {}
        
        # --- INISIALISASI DATABASE ---
        self.conn = sqlite3.connect('voicelog.db')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                tanggal TEXT,
                channel_id INTEGER,
                user_id INTEGER,
                durasi REAL
            )
        ''')
        self.conn.commit()

    def get_today_date(self):
        return datetime.now().strftime('%Y-%m-%d')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        # JIKA USER KELUAR VC (Simpan ke Database)
        if before.channel is not None and before.channel != after.channel:
            if member.id in self.voice_sessions:
                session = self.voice_sessions.pop(member.id)
                durasi_sesi = (datetime.now() - session["start_time"]).total_seconds()
                tanggal_hari_ini = self.get_today_date()
                
                self.cursor.execute("INSERT INTO history VALUES (?, ?, ?, ?)", 
                                  (tanggal_hari_ini, session["channel_id"], member.id, durasi_sesi))
                self.conn.commit()

        # JIKA USER MASUK VC
        if after.channel is not None and before.channel != after.channel:
            self.voice_sessions[member.id] = {
                "start_time": datetime.now(),
                "channel_id": after.channel.id
            }

    def build_embed(self, judul, data_durasi, real_time_sessions=None):
        embed = discord.Embed(title=judul, color=discord.Color.blue())
        
        # Tambahkan data real-time jika ada
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
            channel = self.bot.get_channel(chan_id)
            channel_name = channel.name if channel else f"Channel ({chan_id})"
            
            sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
            teks_channel = ""
            
            for uid, secs in sorted_users[:10]:
                user = self.bot.get_user(uid)
                nama_user = user.name if user else f"ID: {uid}"
                
                jam = int(secs // 3600)
                menit = int((secs % 3600) // 60)
                detik = int(secs % 60)
                
                waktu = ""
                if jam > 0: waktu += f"**{jam}j** "
                if menit > 0: waktu += f"**{menit}m** "
                waktu += f"**{detik}d**"
                
                teks_channel += f"👤 **{nama_user}** : {waktu}\n"
                
            embed.add_field(name=f"🔊 {channel_name}", value=teks_channel, inline=False)
            
        return embed

    @commands.command()
    async def vclog(self, ctx, arg=None):
        tanggal_hari_ini = self.get_today_date()

        # 1. LOGIKA UNTUK HISTORY
        if arg and arg.lower() == "history":
            # Hapus pengecualian hari ini. Ambil SEMUA tanggal yang ada.
            self.cursor.execute("SELECT DISTINCT tanggal FROM history ORDER BY tanggal DESC LIMIT 25")
            tanggal_tersedia = [row[0] for row in self.cursor.fetchall()]

            # Paksa masukkan tanggal hari ini jika ada orang yg sedang aktif di VC
            # (meskipun mereka belum keluar dan datanya belum masuk database)
            if tanggal_hari_ini not in tanggal_tersedia and self.voice_sessions:
                tanggal_tersedia.insert(0, tanggal_hari_ini)

            if not tanggal_tersedia:
                await ctx.send("Belum ada data history yang tersimpan.")
                return

            view = HistoryView(self, tanggal_tersedia)
            await ctx.send("Pilih tanggal history yang ingin kamu lihat:", view=view)

        # 2. LOGIKA UNTUK CEK HARI INI SAJA
        else:
            self.cursor.execute("SELECT channel_id, user_id, SUM(durasi) FROM history WHERE tanggal = ? GROUP BY channel_id, user_id", (tanggal_hari_ini,))
            rows = self.cursor.fetchall()
            
            data_hari_ini = {}
            for cid, uid, dur in rows:
                if cid not in data_hari_ini: data_hari_ini[cid] = {}
                data_hari_ini[cid][uid] = dur

            embed = self.build_embed(f"📊 Statistik VC: Hari Ini ({tanggal_hari_ini})", data_hari_ini, self.voice_sessions)
            await ctx.send(embed=embed)


class HistoryDropdown(discord.ui.Select):
    def __init__(self, cog_instance, tanggal_list):
        self.cog_instance = cog_instance
        opsi = [discord.SelectOption(label=tgl, description=f"Lihat statistik pada {tgl}") for tgl in tanggal_list]
        super().__init__(placeholder="Pilih Tanggal History...", min_values=1, max_values=1, options=opsi)

    async def callback(self, interaction: discord.Interaction):
        tanggal_dipilih = self.values[0]
        
        self.cog_instance.cursor.execute("SELECT channel_id, user_id, SUM(durasi) FROM history WHERE tanggal = ? GROUP BY channel_id, user_id", (tanggal_dipilih,))
        rows = self.cog_instance.cursor.fetchall()
        
        data_history = {}
        for cid, uid, dur in rows:
            if cid not in data_history: data_history[cid] = {}
            data_history[cid][uid] = dur

        # CEK PENTING: Jika tanggal yang diklik adalah HARI INI, gabungkan dengan durasi Real-Time!
        sesi_realtime = self.cog_instance.voice_sessions if tanggal_dipilih == self.cog_instance.get_today_date() else None

        embed = self.cog_instance.build_embed(f"📜 History VC: {tanggal_dipilih}", data_history, sesi_realtime)
        await interaction.response.edit_message(content=f"Menampilkan data untuk **{tanggal_dipilih}**:", embed=embed)


class HistoryView(discord.ui.View):
    def __init__(self, cog_instance, tanggal_list):
        super().__init__(timeout=120) 
        self.add_item(HistoryDropdown(cog_instance, tanggal_list))


async def setup(bot):
    await bot.add_cog(VoiceLog(bot))