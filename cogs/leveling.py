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

    # --- UPDATE: Fungsi pengumuman dinamis ---
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

    # --- UPDATE: Logika pengecekan level & rank yang lebih presisi ---
    async def give_xp(self, user_id, amount, member=None):
        # 1. Ambil data level ASLI sebelum XP ditambahkan
        old_data = await self.pool.fetchrow("SELECT level FROM levels WHERE user_id = $1", user_id)
        old_level = old_data['level'] if old_data else 1
        
        # 2. Tambahkan XP via database
        result = await self.pool.fetchrow("SELECT * FROM add_xp($1, $2)", user_id, amount)
        new_level = result['new_level']
        
        if member:
            # Force update role ke level yang baru
            await self.update_role(member, new_level)
            
            # 3. Cek apakah levelnya benar-benar naik
            if new_level > old_level:
                # 4. Bandingkan Rank Asli (Lama) vs Rank Baru
                old_rank = self.get_rank_role(old_level)
                new_rank = self.get_rank_role(new_level)
                
                # Menentukan apakah momen ini merubah Rank atau tidak
                is_rank_up = old_rank != new_rank
                
                # Kirim pengumuman dinamis
                await self.send_levelup_announcement(member, new_level, is_rank_up)
                
                try:
                    if is_rank_up:
                        await member.send(f"Selamat! Kamu naik ke Level {new_level} dan Rank kamu naik menjadi **{new_rank}**!")
                    else:
                        await member.send(f"Selamat! Kamu naik ke **Level {new_level}**!")
                except: 
                    pass
        
        return new_level

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        last_time = self.cooldowns.get(message.author.id, datetime.min)
        if datetime.now() - last_time < timedelta(seconds=60): return
        
        self.cooldowns[message.author.id] = datetime.now()
        await self.give_xp(message.author.id, 2, message.author)

    @commands.command()
    @commands.has_permissions(administrator=True)
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

    @commands.command()
    async def rank(self, ctx):
        # 1. Menghapus pesan command !rank dari chat
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass
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
        
        # --- UPDATE FONT & UKURAN ---
        # Font sedikit disesuaikan agar tidak gampang nabrak
        poppins_large = Font.poppins(size=45, variant="bold") 
        poppins_medium = Font.poppins(size=32, variant="bold")
        poppins_small = Font.poppins(size=25)
        poppins_badge = Font.poppins(size=22, variant="bold") # Font khusus untuk shape Level
        
        # --- UPDATE: PENCEGAHAN OVERLAP NAMA ---
        # Jika nama lebih dari 13 karakter, potong dan tambahkan "..."
        user_name = str(ctx.author.name)
        if len(user_name) > 13:
            user_name = user_name[:13] + "..."

        # Tulis Nama User (Kiri Atas)
        background.text((280, 70), user_name, font=poppins_large, color="white")
        
        # Tulis Status XP (Kanan Atas) -> Dipindah ke atas agar tidak nabrak Level
        background.text((850, 85), f"{xp} / {xp_needed} XP", font=poppins_small, color="#C0C0C0", align="right")
        
        # Tulis Role Hunter (Kiri Bawah)
        role_name = self.get_rank_role(lvl)
        background.text((280, 140), role_name, font=poppins_medium, color="#FFD700") 
        
        # --- UPDATE BARU: SHAPE/BADGE UNTUK LEVEL (Kanan Bawah) ---
        # Membuat shape/kotak berwarna Emas Gelap
        background.rectangle((730, 130), width=120, height=45, color="#DAA520", radius=15)
        # Teks Level di-center ke dalam kotak tersebut dengan warna sangat gelap
        background.text((790, 142), f"LVL {lvl}", font=poppins_badge, color="#1A1C1E", align="center")
        
        # Gambar Progress Bar Premium
        # Background bar (kosong) dengan bingkai tebal
        background.rectangle((280, 200), width=570, height=50, color="#2F3136", radius=25)
        # Bar yang terisi (Emas gelap)
        background.bar((280, 200), max_width=570, height=50, percentage=percentage, color="#DAA520", radius=25)
        
        # Teks persentase di tengah bar
        background.text((280 + (570/2), 213), f"{percentage:.1f}% Complete", font=Font.poppins(size=18, variant="bold"), color="#1A1C1E", align="center")
        
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