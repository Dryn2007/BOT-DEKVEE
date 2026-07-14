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

        # Hapus role rank lain agar tidak numpuk
        roles_to_remove = [r for r in member.roles if r.name in all_rank_roles and r.name != role_name]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove)
        
        # Tambahkan role baru
        if target_role not in member.roles:
            await member.add_roles(target_role)

    async def send_levelup_announcement(self, member, level):
        channel = self.bot.get_channel(1526479863811149954)
        if channel:
            embed = discord.Embed(
                title="🎉 Rank Up!",
                description=f"Luar biasa {member.mention}! Kamu telah mencapai **Level {level}** dan berevolusi menjadi **{self.get_rank_role(level)}**!",
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

    async def give_xp(self, user_id, amount, member=None):
        result = await self.pool.fetchrow("SELECT * FROM add_xp($1, $2)", user_id, amount)
        new_level = result['new_level']
        leveled_up = result['leveled_up']
        
        if member:
            # Force update role
            await self.update_role(member, new_level)
            
            if leveled_up:
                # Cek role di level sebelumnya vs level sekarang
                old_rank = self.get_rank_role(new_level - 1)
                new_rank = self.get_rank_role(new_level)
                
                # Pengumuman HANYA dikirim jika Rank berubah
                if old_rank != new_rank:
                    await self.send_levelup_announcement(member, new_level)
                    try:
                        await member.send(f"Selamat! Rank kamu naik menjadi **{new_rank}** (Level {new_level})!")
                    except: pass
        
        return new_level

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        last_time = self.cooldowns.get(message.author.id, datetime.min)
        if datetime.now() - last_time < timedelta(seconds=60): return
        
        self.cooldowns[message.author.id] = datetime.now()
        await self.give_xp(message.author.id, 2, message.author)

    # --- COMMAND BARU UNTUK TESTING ---
    @commands.command()
    @commands.has_permissions(administrator=True) # Dibatasi hanya untuk Admin
    async def testxp(self, ctx, amount: int):
        """Fitur untuk ngetest naik level secara instan (Admin Only)"""
        try:
            await ctx.message.delete()
        except:
            pass
        
        # Tambahkan XP sesuai input
        new_level = await self.give_xp(ctx.author.id, amount, ctx.author)
        
        msg = await ctx.send(f"🔧 **Test Mode:** Berhasil menyuntikkan `{amount} XP` ke {ctx.author.mention}! (Sekarang Level: **{new_level}**)")
        await asyncio.sleep(5)
        await msg.delete()
    # ----------------------------------

    @commands.command()
    async def rank(self, ctx):
        # 1. Menghapus pesan command !rank dari chat
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass # Lewati jika bot tidak punya akses hapus pesan
        except Exception:
            pass

        # Ambil data XP dari database
        data = await self.pool.fetchrow("SELECT * FROM levels WHERE user_id = $1", ctx.author.id)
        if not data:
            await self.give_xp(ctx.author.id, 0, ctx.author)
            data = {'xp': 0, 'level': 1}
        
        # Pastikan role terpasang
        await self.update_role(ctx.author, data['level'])
        
        xp = data['xp']
        lvl = data['level']
        xp_needed = 50 * (lvl**2)
        
        # Kalkulasi persentase bar
        percentage = (xp / xp_needed) * 100 if xp_needed > 0 else 0
        if percentage > 100: percentage = 100
        
        # --- MEMBUAT GAMBAR RANK CARD (EASY-PIL) ---
        
        # Background dasar (Gelap metalik)
        background = Editor(Canvas((900, 300), color="#1A1C1E"))
        
        # Tarik avatar user dari discord
        avatar_url = ctx.author.display_avatar.with_format("png").url
        profile = await load_image_async(str(avatar_url))
        profile = Editor(profile).resize((200, 200)).circle_image()
        
        # Tempel avatar
        background.paste(profile, (50, 50))
        
        # Font premium
        poppins_large = Font.poppins(size=50, variant="bold")
        poppins_medium = Font.poppins(size=35, variant="bold")
        poppins_small = Font.poppins(size=25)
        
        # Tulis Nama User dan Role Hunter (Warna Emas) - EMOJI PEDANG DIHAPUS
        role_name = self.get_rank_role(lvl)
        background.text((280, 80), str(ctx.author.name), font=poppins_large, color="white")
        background.text((280, 140), role_name, font=poppins_medium, color="#FFD700") 
        
        # Tulis Status Level dan XP (Warna Abu Metalik)
        background.text((850, 80), f"Level {lvl}", font=poppins_large, color="white", align="right")
        background.text((850, 140), f"{xp} / {xp_needed} XP", font=poppins_small, color="#C0C0C0", align="right")
        
        # Gambar Progress Bar Premium
        # Background bar (kosong) dengan bingkai tebal
        background.rectangle((280, 200), width=570, height=50, color="#2F3136", radius=25)
        # Bar yang terisi (Emas gelap)
        background.bar((280, 200), max_width=570, height=50, percentage=percentage, color="#DAA520", radius=25)
        
        # Teks persentase di tengah bar
        background.text((280 + (570/2), 225), f"{percentage:.1f}% Complete", font=Font.poppins(size=18, variant="bold"), color="#1A1C1E", align="center")
        
        # Kirim hasil akhir gambar
        file = discord.File(fp=background.image_bytes, filename="rank.png")
        msg = await ctx.send(file=file)
        
        # (Opsional) Gambar rank card ini akan dihapus setelah 20 detik agar chat tidak penuh
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