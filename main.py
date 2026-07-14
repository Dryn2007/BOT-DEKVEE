import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
import asyncpg # WAJIB TAMBAHKAN INI

# Membaca rahasia dari file .env
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DB_URL = os.getenv('DATABASE_URL')

# Menyiapkan Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 

bot = commands.Bot(command_prefix='!', intents=intents)

async def load_cogs():
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            await bot.load_extension(f'cogs.{filename[:-3]}')

async def main():
    # 1. Buat pool database di sini agar tersedia untuk seluruh bot
    bot.pool = await asyncpg.create_pool(DB_URL)
    print("Database pool berhasil dibuat!")
    
    # 2. Muat cogs
    await load_cogs()
    
    # 3. Jalankan bot
    await bot.start(TOKEN)

if __name__ == '__main__':
    asyncio.run(main())