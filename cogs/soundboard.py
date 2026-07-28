import discord
from discord.ext import commands
import asyncio

class Soundboard(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool

    async def deduct_coins(self, user_id, amount):
        data = await self.pool.fetchrow("SELECT coins FROM levels WHERE user_id = $1", user_id)
        if data and data['coins'] >= amount:
            await self.pool.execute("UPDATE levels SET coins = coins - $1 WHERE user_id = $2", amount, user_id)
            return True
        return False

    @commands.command(name="sb")
    async def play_soundboard(self, ctx, sound_name: str):
        try: await ctx.message.delete()
        except: pass

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("Masuk room voice dulu ya!", delete_after=5.0)
            return

        # Potong 2 koin untuk 1x pencet soundboard
        has_enough_coins = await self.deduct_coins(ctx.author.id, 2)
        if not has_enough_coins:
            await ctx.send("Koin kamu tidak cukup! Butuh 2 koin untuk Soundboard.", delete_after=5.0)
            return

        # Logika memutar file audio soundboard (misal pakai FFmpeg)
        # ...
        
        # Notifikasi sukses yang tidak berisik (tanpa suara, langsung hilang)
        await ctx.send(f"🔊 {ctx.author.mention} memutar soundboard `{sound_name}` (-2 Koin)", delete_after=3.0)

async def setup(bot):
    await bot.add_cog(Soundboard(bot, bot.pool))