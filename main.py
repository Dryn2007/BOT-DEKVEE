import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

# Membaca rahasia dari file .env
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Menyiapkan Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix='!', intents=intents)

# Fungsi untuk memuat semua file yang ada di dalam folder 'cogs'
async def load_cogs():
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            # Menghapus ekstensi .py saat di-load
            await bot.load_extension(f'cogs.{filename[:-3]}')

@bot.event
async def on_ready():
    print(f'Login berhasil sebagai {bot.user}')
    print('Bot siap digunakan!')

# Menjalankan bot dan fitur-fiturnya
async def main():
    await load_cogs()
    await bot.start(TOKEN)

if __name__ == '__main__':
    asyncio.run(main())