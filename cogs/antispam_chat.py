import discord
from discord.ext import commands
import time

class AntiSpamChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_tracker = {}
        
        # --- PENGATURAN SPAM CHAT ---
        self.BATAS_WAKTU = 5 # Waktu dalam detik
        self.MAKSIMAL_PESAN = 2 # Maksimal pesan yang diizinkan

    @commands.Cog.listener()
    async def on_message(self, message):
        # Abaikan pesan dari bot
        if message.author.bot:
            return

        user_id = message.author.id
        waktu_sekarang = time.time()

        if user_id not in self.user_tracker:
            self.user_tracker[user_id] = []

        self.user_tracker[user_id].append(waktu_sekarang)

        # Hapus data yang lebih dari 5 detik
        self.user_tracker[user_id] = [t for t in self.user_tracker[user_id] if waktu_sekarang - t < self.BATAS_WAKTU]

        # Jika melebihi batas, hapus pesan
        if len(self.user_tracker[user_id]) > self.MAKSIMAL_PESAN:
            try:
                # Pesan selalu dihapus
                await message.delete()
                
                # HANYA kirim peringatan tepat di pelanggaran pertama (pesan ke-3)
                # Pesan ke-4, ke-5 dst hanya akan dihapus diam-diam
                if len(self.user_tracker[user_id]) == self.MAKSIMAL_PESAN + 1:
                    peringatan = await message.channel.send(f"🚨 {message.author.mention}, kamu mengetik terlalu cepat! Tolong jangan spam.")
                    await peringatan.delete(delay=4)
                    
            except discord.Forbidden:
                pass

async def setup(bot):
    await bot.add_cog(AntiSpamChat(bot))