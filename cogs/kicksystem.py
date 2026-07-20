import discord
from discord.ext import commands

# ====================================================================
# MODAL: INPUT ALASAN KICK
# ====================================================================
class ReasonModal(discord.ui.Modal, title="Alasan Kick"):
    reason = discord.ui.TextInput(
        label="Alasan kick member",
        style=discord.TextStyle.paragraph,
        placeholder="Contoh: Melanggar rules #3 (spam / toxic)",
        required=True,
        max_length=300,
    )

    def __init__(self, cog, targets: list[discord.Member]):
        super().__init__()
        self.cog = cog
        self.targets = targets

    async def on_submit(self, interaction: discord.Interaction):
        # Setelah alasan diisi, tampilkan konfirmasi akhir sebelum eksekusi
        view = ConfirmKickView(self.cog, self.targets, self.reason.value, interaction.user)

        daftar_member = "\n".join(f"• {m.mention} (`{m}`)" for m in self.targets)
        embed = discord.Embed(
            title="⚠️ Konfirmasi Kick Member",
            description=(
                f"Kamu akan mengeluarkan **{len(self.targets)} member** berikut:\n\n"
                f"{daftar_member}\n\n"
                f"**Alasan:**\n{self.reason.value}\n\n"
                "Aksi ini akan langsung dieksekusi jika kamu menekan **Konfirmasi Kick**."
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ====================================================================
# VIEW: KONFIRMASI AKHIR SEBELUM KICK DIEKSEKUSI
# ====================================================================
class ConfirmKickView(discord.ui.View):
    def __init__(self, cog, targets: list[discord.Member], reason: str, author: discord.User):
        super().__init__(timeout=60.0)
        self.cog = cog
        self.targets = targets
        self.reason = reason
        self.author = author

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ Cuma yang membuka panel ini yang bisa konfirmasi.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Konfirmasi Kick", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        me = guild.me

        sukses, gagal = [], []

        for member in self.targets:
            # --- Validasi keamanan sebelum kick ---
            if member.id == guild.owner_id:
                gagal.append(f"{member} (owner server, tidak bisa dikick)")
                continue
            if member.id == interaction.user.id:
                gagal.append(f"{member} (tidak bisa kick diri sendiri)")
                continue
            if member.id == me.id:
                gagal.append(f"{member} (tidak bisa kick bot ini sendiri)")
                continue
            if member.top_role >= me.top_role:
                gagal.append(f"{member} (role member lebih tinggi/sejajar dari bot)")
                continue
            if not guild.me.guild_permissions.kick_members:
                gagal.append(f"{member} (bot tidak punya izin Kick Members)")
                continue

            # --- Coba kirim DM alasan sebelum dikick (opsional, boleh gagal) ---
            try:
                dm_embed = discord.Embed(
                    title=f"Kamu dikeluarkan dari {guild.name}",
                    description=f"**Alasan:** {self.reason}",
                    color=discord.Color.red(),
                )
                await member.send(embed=dm_embed)
            except Exception:
                pass  # DM tertutup / diblokir, lanjut kick tanpa DM

            # --- Eksekusi kick ---
            try:
                await member.kick(reason=f"{self.reason} | Oleh: {interaction.user}")
                sukses.append(str(member))
            except discord.Forbidden:
                gagal.append(f"{member} (bot tidak punya izin/role cukup)")
            except Exception as e:
                gagal.append(f"{member} (error: {e})")

        # --- Laporan hasil ---
        hasil_embed = discord.Embed(
            title="📋 Hasil Eksekusi Kick",
            color=discord.Color.orange(),
        )
        if sukses:
            hasil_embed.add_field(
                name=f"✅ Berhasil dikick ({len(sukses)})",
                value="\n".join(f"• {n}" for n in sukses),
                inline=False,
            )
        if gagal:
            hasil_embed.add_field(
                name=f"❌ Gagal ({len(gagal)})",
                value="\n".join(f"• {n}" for n in gagal),
                inline=False,
            )
        hasil_embed.set_footer(text=f"Alasan: {self.reason}")

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(embed=hasil_embed, view=self)

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="❌ Kick dibatalkan. Tidak ada member yang dikeluarkan.",
            embed=None,
            view=self,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ====================================================================
# VIEW: DROPDOWN PEMILIHAN MEMBER (STEP 1)
# ====================================================================
class KickPanelView(discord.ui.View):
    def __init__(self, cog, author: discord.User):
        super().__init__(timeout=120.0)
        self.cog = cog
        self.author = author
        self.selected_members: list[discord.Member] = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ Cuma yang membuka panel ini yang bisa memakainya.", ephemeral=True
            )
            return False
        return True

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Pilih member yang mau dikick (bisa lebih dari 1)...",
        min_values=1,
        max_values=25,
    )
    async def select_members(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        # Filter: hanya ambil yang benar-benar Member di server ini (bukan User biasa)
        valid_members = [u for u in select.values if isinstance(u, discord.Member)]
        self.selected_members = valid_members

        nama = ", ".join(m.mention for m in valid_members) if valid_members else "(belum ada)"
        await interaction.response.edit_message(
            content=f"👥 Terpilih: {nama}\n\nKlik **Lanjutkan** untuk isi alasan kick.",
            view=self,
        )

    @discord.ui.button(label="Lanjutkan", style=discord.ButtonStyle.primary, emoji="➡️")
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_members:
            await interaction.response.send_message(
                "⚠️ Pilih dulu minimal 1 member dari dropdown di atas.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ReasonModal(self.cog, self.selected_members))

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="❌ Dibatalkan.", view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ====================================================================
# COG UTAMA
# ====================================================================
class KickSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="kickpanel", aliases=["kp"])
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kickpanel(self, ctx: commands.Context):
        """Buka panel kick member dengan dropdown pemilihan + alasan."""
        view = KickPanelView(self, ctx.author)
        await ctx.send(
            "🚪 **Panel Kick Member**\nPilih member yang ingin dikick dari dropdown di bawah ini.",
            view=view,
        )

    @kickpanel.error
    async def kickpanel_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Kamu tidak punya izin **Kick Members** untuk membuka panel ini.")
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("❌ Bot tidak punya izin **Kick Members** di server ini.")
        else:
            await ctx.send(f"❌ Terjadi error: {error}")
            raise error


async def setup(bot):
    await bot.add_cog(KickSystem(bot))