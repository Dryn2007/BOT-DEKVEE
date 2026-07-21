import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio

# Import plugin luar untuk membuat gambar Rank Card
from easy_pil import Editor, Canvas, load_image_async, Font

class Leveling(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.cooldowns = {}
        
        # --- TAMBAHAN UNTUK LIMIT HARIAN & VOICE ---
        self.voice_sessions = {}  # Melacak waktu join voice
        self.daily_tracker = {}   # Melacak jumlah XP harian user
        self.current_day = datetime.now().date()

    # Fungsi untuk mereset limit setiap berganti hari
    def check_daily_reset(self):
        today = datetime.now().date()
        if today > self.current_day:
            self.daily_tracker.clear()
            self.current_day = today

    # Fungsi untuk mengambil/membuat data harian user
    def get_user_daily(self, user_id):
        self.check_daily_reset()
        if user_id not in self.daily_tracker:
            self.daily_tracker[user_id] = {'chat': 0, 'call': 0}
        return self.daily_tracker[user_id]

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
        
        target_role = discord.utils.get(member.guild.roles, name=role_name)
        if not target_role: return

        roles_to_remove = [r for r in member.roles if r.name in all_rank_roles and r.name != role_name]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove)
        
        if target_role not in member.roles:
            await member.add_roles(target_role)

    async def send_levelup_announcement(self, member, level, is_rank_up=False):
        channel = self.bot.get_channel(1526479863811149954)
        if channel:
            if is_rank_up:
                title_text = "🎉 Rank Up!"
                desc_text = f"Luar biasa {member.mention}! Kamu telah mencapai **Level {level}** dan berevolusi menjadi **{self.get_rank_role(level)}**!"
            else:
                title_text = "🏆 Level Up!"
                desc_text = f"Selamat {member.mention}! Kamu naik ke **Level {level}**!"

            embed = discord.Embed(
                title=title_text,
                description=desc_text,
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

    async def give_xp(self, user_id, amount, member=None):
        old_data = await self.pool.fetchrow("SELECT level FROM levels WHERE user_id = $1", user_id)
        old_level = old_data['level'] if old_data else 1
        
        result = await self.pool.fetchrow("SELECT * FROM add_xp($1, $2)", user_id, amount)
        new_level = result['new_level']
        
        if member:
            await self.update_role(member, new_level)
            
            if new_level > old_level:
                old_rank = self.get_rank_role(old_level)
                new_rank = self.get_rank_role(new_level)
                is_rank_up = old_rank != new_rank
                
                await self.send_levelup_announcement(member, new_level, is_rank_up)
                
                try:
                    if is_rank_up:
                        await member.send(f"Selamat! Kamu naik ke Level {new_level} dan Rank kamu naik menjadi **{new_rank}**!")
                    else:
                        await member.send(f"Selamat! Kamu naik ke **Level {new_level}**!")
                except: 
                    pass
        
        return new_level

    # --- EVENT: CHAT XP (MAX 30/HARI) ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        last_time = self.cooldowns.get(message.author.id, datetime.min)
        if datetime.now() - last_time < timedelta(seconds=60): return
        
        daily = self.get_user_daily(message.author.id)
        
        # Cek apakah limit chat harian belum menyentuh 30 XP
        if daily['chat'] < 30:
            xp_to_give = 2
            
            # Jika XP yang mau diberikan membuat totalnya lebih dari 30, potong sisanya
            if daily['chat'] + xp_to_give > 30:
                xp_to_give = 30 - daily['chat']
                
            self.cooldowns[message.author.id] = datetime.now()
            daily['chat'] += xp_to_give
            await self.give_xp(message.author.id, xp_to_give, message.author)

    # --- EVENT: VOICE CALL XP (MAX 50/HARI) ---
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return

        # User masuk Voice Channel
        if before.channel is None and after.channel is not None:
            self.voice_sessions[member.id] = datetime.now()
            
        # User keluar Voice Channel
        elif before.channel is not None and after.channel is None:
            join_time = self.voice_sessions.pop(member.id, None)
            
            if join_time:
                # Hitung durasi (1 menit = 1 XP)
                duration = (datetime.now() - join_time).total_seconds()
                minutes_spent = int(duration // 60)
                
                if minutes_spent > 0:
                    daily = self.get_user_daily(member.id)
                    
                    # Cek apakah limit voice harian belum menyentuh 50 XP
                    if daily['call'] < 50:
                        xp_to_give = minutes_spent
                        
                        if daily['call'] + xp_to_give > 50:
                            xp_to_give = 50 - daily['call']
                            
                        daily['call'] += xp_to_give
                        await self.give_xp(member.id, xp_to_give, member)

    # (Command testxp, rank, dan leaderboard tetap sama seperti aslinya)
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def testxp(self, ctx, amount: int):
        try: await ctx.message.delete()
        except: pass
        new_level = await self.give_xp(ctx.author.id, amount, ctx.author)
        msg = await ctx.send(f"🔧 **Test Mode:** Berhasil menyuntikkan `{amount} XP` ke {ctx.author.mention}! (Sekarang Level: **{new_level}**)")
        await asyncio.sleep(5)
        await msg.delete()

    @commands.command()
    async def rank(self, ctx):
        try: await ctx.message.delete()
        except: pass

        data = await self.pool.fetchrow("SELECT * FROM levels WHERE user_id = $1", ctx.author.id)
        if not data:
            await self.give_xp(ctx.author.id, 0, ctx.author)
            data = {'xp': 0, 'level': 1}
        
        await self.update_role(ctx.author, data['level'])
        
        xp = data['xp']
        lvl = data['level']
        xp_needed = 50 * (lvl**2)
        
        percentage = (xp / xp_needed) * 100 if xp_needed > 0 else 0
        if percentage > 100: percentage = 100
        
        background = Editor(Canvas((900, 300), color="#1A1C1E"))
        avatar_url = ctx.author.display_avatar.with_format("png").url
        profile = await load_image_async(str(avatar_url))
        profile = Editor(profile).resize((200, 200)).circle_image()
        background.paste(profile, (50, 50))
        
        poppins_large = Font.poppins(size=40, variant="bold")
        poppins_medium = Font.poppins(size=30, variant="bold")
        poppins_small = Font.poppins(size=22)
        poppins_badge = Font.poppins(size=22, variant="bold")
        
        user_name = str(ctx.author.name)
        if len(user_name) > 15:
            user_name = user_name[:12] + "..."

        background.text((280, 70), user_name, font=poppins_large, color="white")
        background.rectangle((730, 70), width=120, height=45, color="#DAA520", radius=15)
        background.text((790, 82), f"LVL {lvl}", font=poppins_badge, color="#1A1C1E", align="center")
        role_name = self.get_rank_role(lvl)
        background.text((280, 145), role_name, font=poppins_medium, color="#FFD700") 
        background.text((850, 150), f"{xp} / {xp_needed} XP", font=poppins_small, color="#C0C0C0", align="right")
        background.rectangle((280, 200), width=570, height=50, color="#2F3136", radius=25)
        background.bar((280, 200), max_width=570, height=50, percentage=percentage, color="#DAA520", radius=25)
        background.text((280 + (570/2), 213), f"{percentage:.1f}% Complete", font=Font.poppins(size=18, variant="bold"), color="#1A1C1E", align="center")
        
        file = discord.File(fp=background.image_bytes, filename="rank.png")
        msg = await ctx.send(file=file)
        await asyncio.sleep(20)
        await msg.delete()

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
    await bot.add_cog(Leveling(bot, bot.pool))