import discord
from discord.ext import commands
import time

class AntiSpamGif(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Buku catatan: Mencatat kapan GIF terakhir dikirim di SEBUAH CHANNEL
        self.channel_tracker = {}
        # Buku catatan: Mencatat apakah peringatan sudah dikirim di jeda waktu ini
        self.peringatan_terkirim = {}
        
        # --- PENGATURAN JEDA GLOBAL ---
        self.COOLDOWN = 10 # Jeda 10 detik untuk semua orang di channel tersebut

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        teks_pesan = message.content.lower()
        ada_stiker = len(message.stickers) > 0
        ada_gif = False
        
        if any(lampiran.filename.lower().endswith('.gif') for lampiran in message.attachments):
            ada_gif = True
            
        daftar_situs_gif = ['tenor.com', 'giphy.com', 'klipy', '.gif']
        if any(situs in teks_pesan for situs in daftar_situs_gif):
            ada_gif = True

        if not (ada_stiker or ada_gif):
            return

        # --- LOGIKA JEDA GLOBAL (PER CHANNEL) ---
        channel_id = message.channel.id
        waktu_sekarang = time.time()
        
        # Ambil waktu terakhir GIF dikirim di channel ini (jika belum ada, anggap 0)
        waktu_terakhir = self.channel_tracker.get(channel_id, 0)

        # Cek apakah saat ini MASIH DALAM masa jeda 10 detik
        if waktu_sekarang - waktu_terakhir < self.COOLDOWN:
            try:
                # Pesan selalu dihapus karena melanggar jeda
                await message.delete()
                
                # Kirim peringatan HANYA JIKA belum ada peringatan di masa jeda ini
                if not self.peringatan_terkirim.get(channel_id, False):
                    sisa_waktu = int(self.COOLDOWN - (waktu_sekarang - waktu_terakhir))
                    peringatan = await message.channel.send(
                        f"⏳ {message.author.mention}, stiker/GIF di channel ini sedang *cooldown*! Tunggu {sisa_waktu} detik lagi."
                    )
                    await peringatan.delete(delay=5)
                    
                    # Tandai bahwa peringatan sudah dikirim agar tidak spam
                    self.peringatan_terkirim[channel_id] = True
                    
            except discord.Forbidden:
                pass
        else:
            # Jika SUDAH LEWAT 10 detik, GIF diizinkan!
            # Catat waktu baru, dan reset status peringatan menjadi False (belum dikirim)
            self.channel_tracker[channel_id] = waktu_sekarang
            self.peringatan_terkirim[channel_id] = False

async def setup(bot):
    await bot.add_cog(AntiSpamGif(bot))