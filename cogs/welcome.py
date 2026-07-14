import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()
CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID'))

# Di Cogs, kita menggunakan Class
class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = self.bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(f'Selamat datang di server, **{member.display_name}**! 🎉 Semoga betah ya!')

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        channel = self.bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(f'Selamat tinggal, **{member.display_name}**. Sampai jumpa lagi! 👋')

# Syarat wajib agar file ini bisa terbaca oleh main.py
async def setup(bot):
    await bot.add_cog(Welcome(bot))