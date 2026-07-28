import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone

STATS_CHANNEL_ID = 1526614764799922236 
WIB = timezone(timedelta(hours=7))

class Dashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dashboard_message = None

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.update_dashboard.is_running():
            self.update_dashboard.start()

    def cog_unload(self):
        self.update_dashboard.cancel()

    @tasks.loop(minutes=1)
    async def update_dashboard(self):
        channel = self.bot.get_channel(STATS_CHANNEL_ID)
        if not channel: return
        guild = channel.guild

        prodi_roles = ["DKV", "TEKINFO", "SISFOR", "TEKTEL"]
        
        # 1. Hitung Populasi
        member_counts = {}
        for role_name in prodi_roles:
            role = discord.utils.get(guild.roles, name=role_name)
            member_counts[role_name] = len([m for m in role.members if not m.bot]) if role else 0

        # 2. Ambil Semua Data Level, XP, & Koin dari Database
        # Pastikan kolom 'coins' sudah ada di tabel levels
        records = await self.bot.pool.fetch("SELECT user_id, level, xp, COALESCE(coins, 0) as coins FROM levels")
        
        # Filter member yang valid di server
        valid_members = []
        for row in records:
            member = guild.get_member(row['user_id'])
            if member and not member.bot:
                valid_members.append({'member': member, 'level': row['level'], 'xp': row['xp'], 'coins': row['coins']})

        # 3. Sorting Data (XP dan Koin)
        sorted_by_xp = sorted(valid_members, key=lambda x: x['xp'], reverse=True)
        sorted_by_coins = sorted(valid_members, key=lambda x: x['coins'], reverse=True)

        # Fungsi bantuan untuk filter prodi
        def get_top_prodi(sorted_data, role_name, limit=3):
            role = discord.utils.get(guild.roles, name=role_name)
            if not role: return []
            return [data for data in sorted_data if role in data['member'].roles][:limit]

        # 4. Ambil Data Streak Api
        try:
            streak_records = await self.bot.pool.fetch('SELECT prodi_name, current_streak, last_active_date FROM prodi_streaks')
            streaks = {r['prodi_name']: r for r in streak_records}
        except Exception:
            streaks = {}

        today = datetime.now(WIB).date()
        yesterday = today - timedelta(days=1)

        # 5. Bangun UI Dashboard
        embed = discord.Embed(
            title="📊 DASHBOARD STATISTIK KAMPUS",
            description="*Data di bawah ini diperbarui secara otomatis setiap 1 menit.*",
            color=discord.Color.dark_teal()
        )

        # Panel Populasi & Streak
        count_text = ""
        streak_text = ""
        for role_name in prodi_roles:
            count_text += f"**{role_name}:** {member_counts[role_name]} Mhs\n"
            s_data = streaks.get(role_name)
            display_streak = s_data['current_streak'] if s_data and s_data['last_active_date'] >= yesterday else 0
            streak_text += f"**{role_name}:** {display_streak} 🔥\n"
            
        embed.add_field(name="👥 POPULASI", value=count_text, inline=True)
        embed.add_field(name="🔥 STREAK API", value=streak_text, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=False) # Spacing

        # ================= PANEL XP =================
        global_xp_text = ""
        for i, data in enumerate(sorted_by_xp[:10], 1): # TOP 10 GLOBAL XP
            global_xp_text += f"**#{i}** {data['member'].mention} - LVL {data['level']} *( {data['xp']} XP )*\n"
        embed.add_field(name="🏆 TOP 10 HUNTER GLOBAL (XP)", value=global_xp_text or "Belum ada data.", inline=False)

        for role_name in prodi_roles:
            prodi_xp_data = get_top_prodi(sorted_by_xp, role_name, limit=3)
            prodi_text = "".join([f"**#{i}** {d['member'].display_name} - LVL {d['level']}\n" for i, d in enumerate(prodi_xp_data, 1)])
            embed.add_field(name=f"🏅 TOP 3 {role_name} (XP)", value=prodi_text or "-", inline=True)

        embed.add_field(name="\u200b", value="\u200b", inline=False) # Spacing

        # ================= PANEL KOIN =================
        global_coin_text = ""
        for i, data in enumerate(sorted_by_coins[:10], 1): # TOP 10 GLOBAL KOIN
            global_coin_text += f"**#{i}** {data['member'].mention} - 🪙 **{data['coins']} Koin**\n"
        embed.add_field(name="💰 TOP 10 SULTAN GLOBAL (KOIN)", value=global_coin_text or "Belum ada data.", inline=False)

        for role_name in prodi_roles:
            prodi_coin_data = get_top_prodi(sorted_by_coins, role_name, limit=3)
            prodi_c_text = "".join([f"**#{i}** {d['member'].display_name} - 🪙 {d['coins']}\n" for i, d in enumerate(prodi_coin_data, 1)])
            embed.add_field(name=f"🤑 TOP 3 {role_name} (KOIN)", value=prodi_c_text or "-", inline=True)

        embed.set_footer(text="DekVee Auto-Sync", icon_url=guild.icon.url if guild.icon else None)

        # 6. Kirim ke Discord
        if self.dashboard_message:
            try:
                await self.dashboard_message.edit(embed=embed)
            except discord.NotFound:
                self.dashboard_message = await channel.send(embed=embed, silent=True)
        else:
            await channel.purge(limit=10)
            self.dashboard_message = await channel.send(embed=embed, silent=True)

async def setup(bot):
    await bot.add_cog(Dashboard(bot))