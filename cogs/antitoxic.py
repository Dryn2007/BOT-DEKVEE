import discord
from discord.ext import commands
import re

# Kamus Super Lengkap: Kata Dasar, Singkatan Gaul, dan Typo Umum
# Diurutkan sesuai abjad A-Z agar mudah ditambahkan ke depannya.
KATA_KASAR = [
    # A
    'abjing', 'ajg', 'anj', 'anjg', 'anjimg', 'anjing', 'anjir', 'anjrit', 
    'anjrot', 'anying', 'assu', 'asu',
    
    # B
    'babi', 'bacod', 'bacot', 'bajingan', 'banci', 'bangke', 'bangsad', 
    'bangsat', 'bawi', 'bawuk', 'bbi', 'bcd', 'bct', 'bego', 'bejad', 
    'bencong', 'berengsek', 'bgsd', 'bgst', 'biji', 'bjgn', 'bjing', 
    'bloon', 'bodat', 'bodoh', 'brengsek', 'brgsk', 'bst', 'bugil', 
    'bundir', 'burik', 'burit',
    
    # C
    'celeng', 'cemen', 'cipok', 'cok', 'colai', 'coli', 'colmek', 
    'cukimai', 'cukimay', 'culun',
    
    # D
    'dancok', 'dancuk', 'dick', 'dildo', 'dnc', 'dnck', 'dncuk', 
    'dongo', 'dungu',
    
    # E
    'encuk', 'ewe', 'ewok',
    
    # G
    'gay', 'gblk', 'gembel', 'germo', 'gigolo', 'gila', 'goblog', 'goblok',
    
    # H
    'haram', 'hencet', 'hentai', 'homo', 'hostes',
    
    # I
    'idiot',
    
    # J
    'jablai', 'jablay', 'jahanam', 'jancok', 'jancuk', 'jangkik', 
    'jembut', 'jingan', 'jmbt', 'jnc', 'jnck', 'jncok',
    
    # K
    'kafir', 'kampang', 'kampret', 'keparat', 'kimak', 'kintil', 'kirik', 
    'klentit', 'klitoris', 'kntl', 'kntol', 'kobtol', 'koit', 'kontl', 
    'kontol', 'koplak', 'koplok', 'kunyuk', 'kutang',
    
    # L
    'lanjiau', 'lesbi', 'lnt', 'lont', 'lonte',
    
    # M
    'maho', 'mampus', 'masturbasi', 'matane', 'mati', 'mek', 'memek', 
    'meninggoy', 'mesum', 'mmk', 'mnyt', 'modar', 'modyar', 'mokad', 
    'monyet', 'mucikari',
    
    # N
    'najis', 'ndhasmu', 'nenen', 'ngentot', 'ngewe', 'ngt', 'ngtt', 
    'ngulum', 'ngw', 'nigga', 'nigger', 'njg', 'njing', 'njir', 'nyet',
    
    # O
    'onani', 'orgasme',
    
    # P
    'pantat', 'pantek', 'payudara', 'pecun', 'pejuh', 'pekok', 'pelacur', 
    'peler', 'peli', 'penis', 'pentil', 'pepek', 'perek', 'perkosa', 
    'piatu', 'porno', 'ppk', 'psk', 'puki', 'pukimak',
    
    # S
    'sarap', 'sedeng', 'selangkangan', 'sempak', 'senggama', 'sepong', 
    'setan', 'setubuh', 'silit', 'sinting', 'sodomi',
    
    # T
    'telanjang', 'telaso', 'tempik', 'tete', 'tewas', 'titit', 'tll', 
    'toket', 'tolol', 'tusbol',
    
    # V
    'vagina',
    
    # Y
    'yatim'
]

class AntiToxicManual(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def bersihkan_pesan(self, teks):
        # 1. Jadikan semua huruf kecil
        teks = teks.lower()
        
        # 2. Hapus karakter pemisah yang sering dipakai untuk memotong kata
        teks = re.sub(r'[\.\-\_\,\/\|\*\@\#\+]', '', teks)
        
        # 3. Ubah angka leet speak menjadi huruf (4=a, 1=i, 0=o, 3=e, 5=s, 8=b)
        leet_map = str.maketrans('410358', 'aioesb')
        teks = teks.translate(leet_map)
        
        # 4. Hapus huruf yang berulang berlebihan (contoh: aaaaasssuuuu -> asu)
        teks = re.sub(r'(.)\1+', r'\1', teks)
        
        return teks

    @commands.Cog.listener()
    async def on_message(self, message):
        # Abaikan pesan dari bot atau pesan kosong
        if message.author.bot or not message.content:
            return

        # Cuci teksnya terlebih dahulu menggunakan fungsi bersihkan_pesan
        pesan_bersih = self.bersihkan_pesan(message.content)
        
        # Pecah menjadi per kata agar filter tidak salah sensor kata normal 
        # (misal: "kocok" aman karena berbeda dengan "cok")
        pesan_per_kata = pesan_bersih.split()
        
        # Cek apakah ada kata yang cocok dengan kamus
        for kata in pesan_per_kata:
            if kata in KATA_KASAR:
                try:
                    await message.delete()
                    peringatan = await message.channel.send(
                        f"⚠️ {message.author.mention}, tolong jaga ketikanmu! "
                        f"Pesanmu mengandung kata yang dilarang."
                    )
                    # Hapus peringatan setelah 5 detik agar chat tidak kotor
                    await peringatan.delete(delay=5)
                except discord.NotFound:
                    # Mengabaikan error jika pesan ternyata sudah dihapus oleh user/bot lain
                    pass
                break # Berhenti mengecek kata lain jika sudah menemukan 1 pelanggaran

async def setup(bot):
    await bot.add_cog(AntiToxicManual(bot))