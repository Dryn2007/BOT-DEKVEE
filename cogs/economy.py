import discord
from discord.ext import commands

class CoinHistoryUI(discord.ui.View):
    def __init__(self, bot, user_id):
        super().__init__(timeout=15) # Tombol expired dalam 15 detik
        self.bot = bot
        self.user_id = user_id

    @discord.ui.button(label="Cek Riwayat Koin", style=discord.ButtonStyle.primary, emoji="📜")
    async def check_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Hanya pemilik koin yang bisa klik
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ini bukan dompetmu!", ephemeral=True)
            return

        # Ambil 10 riwayat terakhir dari database
        records = await self.bot.pool.fetch(
            "SELECT amount, description, log_date FROM coin_logs WHERE user_id = $1 ORDER BY log_date DESC LIMIT 10", 
            self.user_id
        )

        if not records:
            await interaction.user.send("Kamu belum memiliki riwayat penggunaan koin.")
        else:
            history_text = "📜 **10 Riwayat Koin Terakhirmu:**\n\n"
            for row in records:
                # Format: [2026-04-25] +5 Koin | Mendapatkan Role
                date_str = row['log_date'].strftime("%Y-%m-%d %H:%M")
                sign = "+" if row['amount'] > 0 else ""
                history_text += f"`[{date_str}]` **{sign}{row['amount']} Koin** | {row['description']}\n"
            
            # Kirim ke DM (Pribadi)
            try:
                await interaction.user.send(history_text)
            except discord.Forbidden:
                await interaction.response.send_message("Aku tidak bisa mengirim DM kepadamu. Pastikan DM kamu terbuka untuk server ini!", ephemeral=True)
                return

        # Edit pesan sebelumnya untuk mematikan tombol setelah diklik
        button.disabled = True
        await interaction.response.edit_message(view=self)

class Economy(commands.Cog):
    def __init__(self, bot, pool):
        self.bot = bot
        self.pool = pool

    @commands.command(name="koinku")
    async def check_coins(self, ctx):
        # Hapus chat "!koinku" dari user
        try: await ctx.message.delete()
        except: pass

        # Ambil jumlah koin
        data = await self.pool.fetchrow("SELECT coins FROM levels WHERE user_id = $1", ctx.author.id)
        current_coins = data['coins'] if data and data['coins'] is not None else 0

        embed = discord.Embed(
            title="🪙 Dompet Koin",
            description=f"Halo {ctx.author.mention}, kamu saat ini memiliki **{current_coins} Koin**.",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url="https://link-foto-koin-png-kamu.png") # Ganti link koin
        
        view = CoinHistoryUI(self.bot, ctx.author.id)
        
        # Pesan bot akan hilang dalam 5 detik
        await ctx.send(embed=embed, view=view, delete_after=5.0)

async def setup(bot):
    await bot.add_cog(Economy(bot, bot.pool))