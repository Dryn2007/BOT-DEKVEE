import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio

class Leveling(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.cooldowns = {} # Penyimpanan waktu terakhir user chat

    def get_rank_role(self, level):
        if level >= 100: return "Shadow Monarch"
        if level >= 75: return "National Level Hunter"
        if level >= 50: return "S-Rank Hunter"
        if level >= 35: return "A-Rank Hunter"
        if level >= 20: return "B-Rank Hunter"
        if level >= 10: return "C-Rank Hunter"
        if level >= 5: return "D-Rank Hunter"
        return "E-Rank Hunter"

    async def update_role(self, member, level):
        role_name = self.get_rank_role(level)
        all_rank_roles = ["E-Rank Hunter", "D-Rank Hunter", "C-Rank Hunter", "B-Rank Hunter", 
                          "A-Rank Hunter", "S-Rank Hunter", "National Level Hunter", "Shadow Monarch"]
        
        # Cari role di server
        target_role = discord.utils.get(member.guild.roles, name=role_name)
        if not target_role: return

        # Hapus role lama dan tambah role baru
        roles_to_remove = [r for r in member.roles if r.name in all_rank_roles and r.name != role_name]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove)
        
        if target_role not in member.roles:
            await member.add_roles(target_role)

    async def send_levelup_announcement(self, member, level):
        # ID Room Pengumuman
        channel = self.bot.get_channel(1526479863811149954)
        if channel:
            embed = discord.Embed(
                title="🏆 Level Up!",
                description=f"Selamat {member.mention}! Kamu telah mencapai **Level {level}** dan menjadi seorang **{self.get_rank_role(level)}**!",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

    async def give_xp(self, user_id, amount, member=None):
        # Memanggil fungsi SQL dari Supabase
        result = await self.pool.fetchrow("SELECT * FROM add_xp($1, $2)", user_id, amount)
        new_level = result['new_level']
        leveled_up = result['leveled_up']
        
        if leveled_up and member:
            await self.update_role(member, new_level)
            await self.send_levelup_announcement(member, new_level)
            await member.send(f"Selamat! Kamu naik ke level **{new_level}** ({self.get_rank_role(new_level)})!")
        
        return new_level

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        # Cooldown 60 detik untuk chat XP
        last_time = self.cooldowns.get(message.author.id, datetime.min)
        if datetime.now() - last_time < timedelta(seconds=60):
            return
        
        self.cooldowns[message.author.id] = datetime.now()
        await self.give_xp(message.author.id, 2, message.author)

    @commands.command()
    async def rank(self, ctx):
        data = await self.pool.fetchrow("SELECT * FROM levels WHERE user_id = $1", ctx.author.id)
        if not data:
            msg = await ctx.send("Kamu belum punya data XP. Ayo aktif chat atau di VC!")
            await asyncio.sleep(10)
            await msg.delete()
            return
        
        xp = data['xp']
        lvl = data['level']
        xp_needed = 50 * (lvl**2)
        
        embed = discord.Embed(title=f"Rank Profil - {ctx.author.name}", color=discord.Color.gold())
        embed.set_thumbnail(url=ctx.author.display_avatar.url) # Menampilkan foto profil user
        embed.add_field(name="Level", value=str(lvl), inline=True)
        embed.add_field(name="Rank", value=self.get_rank_role(lvl), inline=True)
        embed.add_field(name="XP", value=f"{xp} / {xp_needed}", inline=False)
        
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(10) # Tunggu 10 detik
        await msg.delete()      # Hapus pesan otomatis

    @commands.command()
    async def leaderboard(self, ctx):
        rows = await self.pool.fetch("SELECT user_id, xp, level FROM levels ORDER BY xp DESC LIMIT 10")
        msg = "🏆 **Leaderboard Top 10 Hunter** 🏆\n\n"
        for i, row in enumerate(rows, 1):
            user = self.bot.get_user(row['user_id'])
            name = user.name if user else f"User {row['user_id']}"
            msg += f"{i}. **{name}** - Level {row['level']} ({row['xp']} XP)\n"
        await ctx.send(msg)

async def setup(bot):
    # Langsung gunakan bot.pool yang sudah kita buat di main.py
    await bot.add_cog(Leveling(bot, bot.pool))