import discord
from discord.ext import commands, tasks
import asyncpg

class Dashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Masukkan ID room 📈・server-stats di bawah ini
        self.channel_id = 1526614764799922236 
        self.dashboard_message = None

    @commands.Cog.listener()
    async def on_ready(self):
        # Jalankan loop update otomatis HANYA SETELAH bot menyala
        if not self.update_dashboard.is_running():
            self.update_dashboard.start()

    def cog_unload(self):
        # Hentikan loop jika cog dimatikan
        self.update_dashboard.cancel()

    @tasks.loop(minutes=15) # Dashboard akan refresh setiap 15 menit
    async def update_dashboard(self):
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return

        guild = channel.guild

        # 1. HITUNG JUMLAH MAHASISWA PER PRODI
        prodi_roles = ["DKV", "TEKINFO", "SISFOR", "TEKTEL"]
        member_counts = {}
        for role_name in prodi_roles:
            role = discord.utils.get(guild.roles, name=role_name)
            # Menghitung member yang punya role tersebut, abaikan bot
            member_counts[role_name] = len([m for m in role.members if not m.bot]) if role else 0

        # 2. AMBIL DATA LEVEL DARI DATABASE (Ambil top 200 untuk disaring)
        records = await self.bot.pool.fetch("SELECT user_id, level, xp FROM levels ORDER BY xp DESC LIMIT 200")

        top_global = []
        top_prodi = {r: [] for r in prodi_roles}

        # 3. PROSES PENYARINGAN DATA
        for row in records:
            member = guild.get_member(row['user_id'])
            if not member or member.bot:
                continue

            # Masukkan ke Top 5 Global
            if len(top_global) < 5:
                top_global.append((member, row['level'], row['xp']))

            # Masukkan ke Top 3 per Prodi
            for role_name in prodi_roles:
                role = discord.utils.get(guild.roles, name=role_name)
                if role and role in member.roles:
                    if len(top_prodi[role_name]) < 3:
                        top_prodi[role_name].append((member, row['level'], row['xp']))

        # 4. BANGUN UI DASHBOARD (EMBED)
        embed = discord.Embed(
            title="📊 DASHBOARD STATISTIK KAMPUS",
            description="*Data di bawah ini diperbarui secara otomatis setiap 15 menit.*",
            color=discord.Color.dark_teal()
        )

        # Bagian 1: Populasi
        count_text = ""
        for role_name in prodi_roles:
            count_text += f"**{role_name}:** {member_counts[role_name]} Mahasiswa\n"
        embed.add_field(name="👥 POPULASI MAHASISWA", value=count_text, inline=False)

        # Bagian 2: Top 5 Global
        global_text = ""
        for i, (mem, lvl, xp) in enumerate(top_global, 1):
            global_text += f"**#{i}** {mem.mention} - LVL {lvl} *( {xp} XP )*\n"
        if not global_text: global_text = "Belum ada data Hunter."
        embed.add_field(name="🏆 TOP 5 HUNTER GLOBAL", value=global_text, inline=False)

        # Bagian 3: Top 3 Per Prodi
        for role_name in prodi_roles:
            prodi_text = ""
            for i, (mem, lvl, xp) in enumerate(top_prodi[role_name], 1):
                prodi_text += f"**#{i}** {mem.display_name} - LVL {lvl}\n"
            if not prodi_text: prodi_text = "-"
            embed.add_field(name=f"🏅 TOP 3 {role_name}", value=prodi_text, inline=True)

        embed.set_footer(text="Telkom University Jakarta Auto-Sync", icon_url=guild.icon.url if guild.icon else None)

        # 5. KIRIM ATAU TIMPA PESAN LAMA
        if self.dashboard_message:
            try:
                await self.dashboard_message.edit(embed=embed)
            except discord.NotFound:
                self.dashboard_message = await channel.send(embed=embed)
        else:
            # Sapu bersih chat lama agar channel tetap rapi, lalu kirim ulang
            await channel.purge(limit=10)
            self.dashboard_message = await channel.send(embed=embed)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def spawnstats(self, ctx):
        """Command rahasia Admin untuk memunculkan dashboard secara paksa"""
        try:
            await ctx.message.delete()
        except:
            pass
        await self.update_dashboard()

async def setup(bot):
    await bot.add_cog(Dashboard(bot))