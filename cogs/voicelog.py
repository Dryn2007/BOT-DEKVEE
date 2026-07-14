import discord
from discord.ext import commands
from datetime import datetime
import asyncpg
import os

class VoiceLog(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.voice_sessions = {}

    async def cog_load(self):
        await self.pool.execute('''
            CREATE TABLE IF NOT EXISTS history (
                tanggal TEXT,
                channel_id BIGINT,
                user_id BIGINT,
                durasi REAL
            )
        ''')

    def get_today_date(self):
        return datetime.now().strftime('%Y-%m-%d')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        # JIKA USER KELUAR VC
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

        # JIKA USER MASUK VC
        if after.channel is not None and before.channel != after.channel:
            self.voice_sessions[member.id] = {
                "start_time": datetime.now(),
                "channel_id": after.channel.id
            }

    def build_embed(self, judul, data_durasi, real_time_sessions=None):
        embed = discord.Embed(title=judul, color=discord.Color.blue())
        if real_time_sessions:
            for uid, session in real_time_sessions.items():
                ongoing_duration = (datetime.now() - session["start_time"]).total_seconds()
                chan_id = session["channel_id"]
                if chan_id not in data_durasi: data_durasi[chan_id] = {}
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
                jam, menit, detik = int(secs // 3600), int((secs % 3600) // 60), int(secs % 60)
                waktu = f"{f'**{jam}j** ' if jam > 0 else ''}{f'**{menit}m** ' if menit > 0 else ''}**{detik}d**"
                teks_channel += f"👤 **{nama_user}** : {waktu}\n"
            embed.add_field(name=f"🔊 {channel_name}", value=teks_channel, inline=False)
        return embed

    @commands.command()
    async def vclog(self, ctx, arg=None):
        tanggal_hari_ini = self.get_today_date()
        if arg and arg.lower() == "history":
            records = await self.pool.fetch("SELECT DISTINCT tanggal FROM history ORDER BY tanggal DESC LIMIT 25")
            tanggal_tersedia = [row['tanggal'] for row in records]
            if tanggal_hari_ini not in tanggal_tersedia and self.voice_sessions:
                tanggal_tersedia.insert(0, tanggal_hari_ini)
            if not tanggal_tersedia:
                await ctx.send("Belum ada data history yang tersimpan.")
                return
            view = HistoryView(self, tanggal_tersedia)
            await ctx.send("Pilih tanggal history yang ingin kamu lihat:", view=view)
        else:
            records = await self.pool.fetch("SELECT channel_id, user_id, SUM(durasi) as total_durasi FROM history WHERE tanggal = $1 GROUP BY channel_id, user_id", tanggal_hari_ini)
            data_hari_ini = {row['channel_id']: {row['user_id']: row['total_durasi']} for row in records}
            # Perbaikan sederhana untuk penggabungan data dictionary
            for row in records:
                if row['channel_id'] not in data_hari_ini: data_hari_ini[row['channel_id']] = {}
                data_hari_ini[row['channel_id']][row['user_id']] = row['total_durasi']
            
            embed = self.build_embed(f"📊 Statistik VC: Hari Ini ({tanggal_hari_ini})", data_hari_ini, self.voice_sessions)
            await ctx.send(embed=embed)

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
            if row['channel_id'] not in data_history: data_history[row['channel_id']] = {}
            data_history[row['channel_id']][row['user_id']] = row['total_durasi']
        sesi_realtime = self.cog_instance.voice_sessions if tanggal_dipilih == self.cog_instance.get_today_date() else None
        embed = self.cog_instance.build_embed(f"📜 History VC: {tanggal_dipilih}", data_history, sesi_realtime)
        await interaction.response.edit_message(content=f"Menampilkan data untuk **{tanggal_dipilih}**:", embed=embed, view=None)

class HistoryView(discord.ui.View):
    def __init__(self, cog_instance, tanggal_list):
        super().__init__(timeout=120) 
        self.add_item(HistoryDropdown(cog_instance, tanggal_list))

# FUNGSI SETUP
async def setup(bot):
    # Langsung gunakan bot.pool yang sudah kita buat di main.py
    await bot.add_cog(VoiceLog(bot, bot.pool))