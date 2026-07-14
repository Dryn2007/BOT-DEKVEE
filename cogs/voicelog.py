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
                # Memanggil Cog Leveling untuk menambahkan XP
                leveling_cog = self.bot.get_cog('Leveling')
                if leveling_cog:
                    # 1 XP per 120 detik (2 menit)
                    xp_to_add = int(durasi_sesi // 120) 
                    if xp_to_add > 0:
                        await leveling_cog.give_xp(member.id, xp_to_add, member)
                # --------------------------
                
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

    # ... (Bagian build_embed, vclog, dll tetap sama) ...
    # Pastikan untuk menyalin bagian bawah class ini (method lainnya) dari kode aslimu
    
    def build_embed(self, judul, data_durasi, real_time_sessions=None):
        embed = discord.Embed(title=judul, color=discord.Color.blue())
        # (Salin kode build_embed kamu dari sebelumnya di sini)
        # ...
        return embed

    @commands.command()
    async def vclog(self, ctx, arg=None):
        # (Salin kode vclog kamu dari sebelumnya di sini)
        # ...
        pass

# Pastikan class HistoryDropdown, HistoryView, dan fungsi setup tetap ada di bawah