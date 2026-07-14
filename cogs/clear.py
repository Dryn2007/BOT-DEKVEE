import discord
from discord.ext import commands

class ClearChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Mengganti izin admin menjadi izin khusus "Pemilik Bot" (Owner)
    @commands.command()
    @commands.is_owner() 
    async def clear(self, ctx, jumlah: int = 5):
        """Menghapus pesan (Hanya bisa digunakan oleh Owner Bot)"""
        
        if jumlah < 1 or jumlah > 100:
            await ctx.send("❌ Masukkan jumlah antara 1 sampai 100.")
            return

        # Menghapus pesan
        dihapus = await ctx.channel.purge(limit=jumlah + 1)
        
        # Mengirim laporan sukses
        laporan = await ctx.send(f"🧹 Berhasil membersihkan **{len(dihapus) - 1}** pesan, Bos!")
        await laporan.delete(delay=3)

    # Pesan error jika yang pakai BUKAN kamu
    @clear.error
    async def clear_error(self, ctx, error):
        # Mengecek apakah errornya karena bukan owner
        if isinstance(error, commands.NotOwner):
            peringatan = await ctx.send("❌ Akses ditolak! Hanya Bos yang bisa pakai command ini.")
            await peringatan.delete(delay=3)

async def setup(bot):
    await bot.add_cog(ClearChat(bot))