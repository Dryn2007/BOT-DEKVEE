import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio

# Room privat tidak boleh jadi tempat panen XP/koin
from roomconfig import is_private_call

# Import plugin luar untuk membuat gambar Rank Card
from easy_pil import Editor, Canvas, load_image_async, Font

class Leveling(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool
        self.cooldowns = {}
        
        # --- TRACKER UNTUK LIMIT HARIAN, VOICE, & KOIN ---
        self.voice_sessions = {}  # Melacak waktu join voice
        self.daily_tracker = {}   # Melacak jumlah XP harian & chat koin
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
            # chat_count untuk melacak kelipatan 5 chat = 1 koin. coin_earned untuk batas 10 koin
            self.daily_tracker[user_id] = {'chat': 0, 'call': 0, 'chat_count': 0, 'coin_earned': 0}
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

    # Fungsi penambah koin + pencatat riwayat (Log)
    async def add_coins(self, user_id, amount, description="Sistem Leveling"):
        # Tambah saldo koin di tabel levels
        await self.pool.execute("UPDATE levels SET coins = coins + $1 WHERE user_id = $2", amount, user_id)
        # Catat ke riwayat agar muncul di command !koinku
        await self.pool.execute(
            "INSERT INTO coin_logs (user_id, amount, description) VALUES ($1, $2, $3)", 
            user_id, amount, description
        )

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
            # REWARD: Naik Role/Rank dapat 10 Koin
            await self.add_coins(member.id, 10, "Naik Rank/Role")

    async def send_levelup_announcement(self, member, level, is_rank_up=False):
        channel = self.bot.get_channel(1526479863811149954)
        if channel:
            if is_rank_up:
                title_text = "🎉 Rank Up!"
                desc_text = (f"Luar biasa {member.mention}! Kamu telah mencapai **Level {level}** dan "
                             f"berevolusi menjadi **{self.get_rank_role(level)}**!\n"
                             f"🪙 **Reward:** +10 Koin (Total Level & Rank Up)")
            else:
                title_text = "🏆 Level Up!"
                desc_text = (f"Selamat {member.mention}! Kamu naik ke **Level {level}**!\n"
                             f"🪙 **Reward:** +5 Koin")

            embed = discord.Embed(
                title=title_text,
                description=desc_text,
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

    async def give_xp(self, user_id, amount, member=None):
        old_data = await self.pool.fetchrow("SELECT level, xp FROM levels WHERE user_id = $1", user_id)
        old_level = old_data['level'] if old_data else 1
        old_xp = old_data['xp'] if old_data else 0
        
        result = await self.pool.fetchrow("SELECT * FROM add_xp($1, $2)", user_id, amount)
        new_level = result['new_level']
        new_xp = old_xp + amount 

        # REWARD: Setiap kelipatan 50 XP dapat 1 koin (terakumulasi permanen)
        coins_to_add = (new_xp // 50) - (old_xp // 50)
        if coins_to_add > 0:
            await self.add_coins(user_id, coins_to_add, "Mencapai Kelipatan 50 XP")
        
        if member:
            await self.update_role(member, new_level)
            
            if new_level > old_level:
                # REWARD: Naik level dapat 5 Koin
                await self.add_coins(user_id, 5, f"Naik ke Level {new_level}")
                
                old_rank = self.get_rank_role(old_level)
                new_rank = self.get_rank_role(new_level)
                is_rank_up = old_rank != new_rank
                
                await self.send_levelup_announcement(member, new_level, is_rank_up)
                
                try:
                    if is_rank_up:
                        await member.send(f"Selamat! Kamu naik ke Level {new_level} dan Rank kamu naik menjadi **{new_rank}**!\n🪙 Kamu juga mendapatkan total **15 Koin** (Level + Rank)!")
                    else:
                        await member.send(f"Selamat! Kamu naik ke **Level {new_level}**!\n🪙 Kamu juga mendapatkan **5 Koin**!")
                except: 
                    pass
        
        return new_level

    # --- EVENT: CHAT XP (MAX 30/HARI) & CHAT KOIN ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return

        # Chat di dalam room privat tidak dapat XP maupun koin
        if is_private_call(message.channel): return

        last_time = self.cooldowns.get(message.author.id, datetime.min)
        if datetime.now() - last_time < timedelta(seconds=60): return
        
        daily = self.get_user_daily(message.author.id)
        
        # SISTEM KOIN: 5 Chat = 1 Koin (Max 10 per hari)
        daily['chat_count'] += 1
        if daily['chat_count'] >= 5 and daily['coin_earned'] < 10:
            await self.add_coins(message.author.id, 1, "Bonus 5 Chat Harian")
            daily['chat_count'] = 0
            daily['coin_earned'] += 1

        # SISTEM XP CHAT
        if daily['chat'] < 30:
            # Jika XP yang mau diberikan membuat totalnya lebih dari 30, potong sisanya
            xp_to_give = min(2, 30 - daily['chat'])
            
            self.cooldowns[message.author.id] = datetime.now()
            daily['chat'] += xp_to_give
            await self.give_xp(message.author.id, xp_to_give, message.author)

    # --- EVENT: VOICE CALL XP (MAX 50/HARI) ---
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return

        # Room privat tidak dihitung: waktu di sana tidak menghasilkan XP sama sekali
        before_ok = before.channel is not None and not is_private_call(before.channel)
        after_ok = after.channel is not None and not is_private_call(after.channel)

        # Mulai hitung (join VC publik, atau keluar dari room privat ke VC publik)
        if not before_ok and after_ok:
            self.voice_sessions[member.id] = datetime.now()

        # Klaim XP (keluar dari voice, atau masuk ke room privat)
        # Pindah antar VC publik tidak memotong sesi.
        elif before_ok and not after_ok:
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

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def synckoin(self, ctx):
        msg = await ctx.send("⏳ Memulai sinkronisasi koin untuk akumulasi XP dan Level lama...")
        
        # Ambil semua data user dari tabel levels
        records = await self.pool.fetch("SELECT user_id, xp, level, coins FROM levels")
        updated_count = 0
        total_koin_dibagikan = 0
        
        for row in records:
            user_id = row['user_id']
            xp = row['xp'] if row['xp'] else 0
            level = row['level'] if row['level'] else 1
            current_coins = row['coins'] if row['coins'] else 0
            
            # 1. Hitung Koin dari akumulasi XP (1 koin tiap 50 XP)
            koin_xp = xp // 50
            
            # 2. Hitung Koin dari Naik Level (5 koin tiap naik level, level 1 tidak dihitung)
            koin_lvl = (level - 1) * 5 if level > 1 else 0
            
            # 3. Hitung Koin dari Naik Rank/Role (10 koin tiap batas rank)
            rank_thresholds = [5, 10, 20, 35, 50, 75, 100]
            koin_rank = sum(10 for t in rank_thresholds if level >= t)
            
            # Total hak koin mereka
            total_koin_hak = koin_xp + koin_lvl + koin_rank
            
            # Update database jika mereka punya hak koin yang belum diberikan
            if total_koin_hak > current_coins:
                selisih_koin = total_koin_hak - current_coins
                
                # Update koin di tabel levels
                await self.pool.execute("UPDATE levels SET coins = $1 WHERE user_id = $2", total_koin_hak, user_id)
                
                # Catat ke riwayat agar muncul di !koinku
                await self.pool.execute(
                    "INSERT INTO coin_logs (user_id, amount, description) VALUES ($1, $2, $3)", 
                    user_id, selisih_koin, "Kompensasi Akumulasi XP & Level Lama"
                )
                
                updated_count += 1
                total_koin_dibagikan += selisih_koin
                
        await msg.edit(content=f"✅ **Sinkronisasi Berhasil!**\nMembagikan total **{total_koin_dibagikan} Koin** kepada **{updated_count} member** berdasarkan XP lama mereka.")

async def setup(bot):
    await bot.add_cog(Leveling(bot, bot.pool))