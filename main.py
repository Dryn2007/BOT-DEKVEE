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
    # 0. Jaga-jaga: kalau DATABASE_URL kosong, asyncpg diam-diam nyoba Postgres
    #    lokal dan errornya nyasar ("WinError 64 / connection was closed").
    #    Lebih baik gagal dengan pesan yang jelas.
    if not DB_URL:
        raise SystemExit(
            "[ERROR] DATABASE_URL belum diset.\n"
            "        Tambahkan satu baris ini di file .env:\n"
            "          DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres\n"
            "        Ambil dari Supabase Dashboard -> Connect -> Session pooler (URI)."
        )
    if not TOKEN:
        raise SystemExit("[ERROR] DISCORD_TOKEN belum diset di file .env.")

    # 1. Buat pool database di sini agar tersedia untuk seluruh bot
    #    min/max dikecilkan: default asyncpg (10/10) langsung menghabiskan 10 dari
    #    15 slot session-mode Supabase, jadi instance kedua selalu kena EMAXCONNSESSION.
    bot.pool = await asyncpg.create_pool(
        DB_URL,
        min_size=1,
        max_size=5,
        max_inactive_connection_lifetime=60.0,
        command_timeout=30.0,
    )
    print("Database pool berhasil dibuat!")
    
    # 2. Muat cogs
    await load_cogs()
    
    # 3. Jalankan bot
    await bot.start(TOKEN)

if __name__ == '__main__':
    asyncio.run(main())