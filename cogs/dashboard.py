import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone

# ID Channel Statistik
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

        # 2. Ambil Data Level & XP dari Database
        records = await self.bot.pool.fetch("SELECT user_id, level, xp FROM levels ORDER BY xp DESC LIMIT 200")
        top_global = []
        top_prodi = {r: [] for r in prodi_roles}

        for row in records:
            member = guild.get_member(row['user_id'])
            if not member or member.bot: continue

            if len(top_global) < 5:
                top_global.append((member, row['level'], row['xp']))

            for role_name in prodi_roles:
                role = discord.utils.get(guild.roles, name=role_name)
                if role and role in member.roles:
                    if len(top_prodi[role_name]) < 3:
                        top_prodi[role_name].append((member, row['level'], row['xp']))

        # 3. Ambil Data Streak Api dari Database
        # Menggunakan try-except berjaga-jaga jika tabel belum dibuat oleh streak.py
        try:
            streak_records = await self.bot.pool.fetch('SELECT prodi_name, current_streak, last_active_date FROM prodi_streaks')
            streaks = {r['prodi_name']: r for r in streak_records}
        except Exception:
            streaks = {}

        today = datetime.now(WIB).date()
        yesterday = today - timedelta(days=1)

        # 4. Bangun UI Dashboard
        embed = discord.Embed(
            title="📊 DASHBOARD STATISTIK KAMPUS",
            description="*Data di bawah ini diperbarui secara otomatis setiap 1 menit.*",
            color=discord.Color.dark_teal()
        )

        # Panel Populasi
        count_text = ""
        for role_name in prodi_roles:
            count_text += f"**{role_name}:** {member_counts[role_name]} Mahasiswa\n"
        embed.add_field(name="👥 POPULASI MAHASISWA", value=count_text, inline=True)

        # Panel Streak Api
        streak_text = ""
        for role_name in prodi_roles:
            s_data = streaks.get(role_name)
            display_streak = 0
            if s_data:
                # Jika hari ini atau kemarin masih aktif, streak ditampilkan. Jika tidak, artinya 0
                if s_data['last_active_date'] >= yesterday:
                    display_streak = s_data['current_streak']
            streak_text += f"**{role_name}:** {display_streak} 🔥\n"
        embed.add_field(name="🔥 STREAK API HARIAN", value=streak_text, inline=True)

        embed.add_field(name="\u200b", value="\u200b", inline=False) # Spacing Kosong

        # Panel Top Global
        global_text = ""
        for i, (mem, lvl, xp) in enumerate(top_global, 1):
            global_text += f"**#{i}** {mem.mention} - LVL {lvl} *( {xp} XP )*\n"
        if not global_text: global_text = "Belum ada data Hunter."
        embed.add_field(name="🏆 TOP 5 HUNTER GLOBAL", value=global_text, inline=False)

        # Panel Top Prodi
        for role_name in prodi_roles:
            prodi_text = ""
            for i, (mem, lvl, xp) in enumerate(top_prodi[role_name], 1):
                prodi_text += f"**#{i}** {mem.display_name} - LVL {lvl}\n"
            if not prodi_text: prodi_text = "-"
            embed.add_field(name=f"🏅 TOP 3 {role_name}", value=prodi_text, inline=True)

        embed.set_footer(text="Telkom University Jakarta Auto-Sync", icon_url=guild.icon.url if guild.icon else None)

        # 5. Kirim ke Discord
        if self.dashboard_message:
            try:
                await self.dashboard_message.edit(embed=embed)
            except discord.NotFound:
                self.dashboard_message = await channel.send(embed=embed)
        else:
            await channel.purge(limit=10)
            self.dashboard_message = await channel.send(embed=embed)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def spawnstats(self, ctx):
        try: await ctx.message.delete()
        except: pass
        await self.update_dashboard()

async def setup(bot):
    await bot.add_cog(Dashboard(bot))