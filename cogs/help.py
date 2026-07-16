import discord
from discord.ext import commands
import asyncio
import traceback
from datetime import timedelta


# >>> CLASS UNTUK TOMBOL "OK" DI PESAN PERINGATAN <<<
class WarningView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=10.0)

    @discord.ui.button(label="OK Paham", style=discord.ButtonStyle.secondary, emoji="👍")
    async def ok_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except Exception:
            pass


class HelpDropdown(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        opsi = [
            discord.SelectOption(
                label="Leveling & Rank",
                description="Panduan sistem XP dan Role Hunter",
                emoji="🏆"
            ),
            discord.SelectOption(
                label="Voice Log",
                description="Panduan statistik durasi Voice Channel",
                emoji="🔊"
            ),
            discord.SelectOption(
                label="Admin Menu",
                description="Panduan command khusus Admin/Owner",
                emoji="👑"
            )
        ]
        super().__init__(placeholder="Pilih fitur yang ingin dilihat...", min_values=1, max_values=1, options=opsi)

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        view.message = interaction.message

        # 1. CEK SISTEM ANTREAN / LOCKING
        if view.locked_user is not None and view.locked_user != interaction.user.id:
            alert_embed = discord.Embed(
                title="⛔ SISTEM SIBUK!",
                description=f"Menu bantuan sedang digunakan oleh user lain!\n\nMohon antre, giliranmu akan tiba **<t:{view.expire_ts}:R>**.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(
                embed=alert_embed,
                ephemeral=True,
                view=WarningView(),
                delete_after=10.0
            )
            return

        try:
            # 2. KUNCI DASHBOARD & RESTART TIMER
            view.locked_user = interaction.user.id
            view.start_timer()

            expire_dt = discord.utils.utcnow() + timedelta(seconds=20)
            view.expire_ts = int(expire_dt.timestamp())

            # 3. TENTUKAN ISI PESAN
            val = self.values[0]
            embed = discord.Embed()

            if val == "Leveling & Rank":
                embed = discord.Embed(
                    title="🏆 Panduan Leveling & Rank",
                    description="Bot menggunakan sistem Hybrid! Kamu dapat XP dari Chat (2 XP) dan dari VC (1 XP / 2 Menit).",
                    color=discord.Color.gold()
                )
                embed.add_field(name="`!rank`", value="Melihat profil level, rank Hunter, dan progress bar XP kamu saat ini.", inline=False)
                embed.add_field(name="`!leaderboard`", value="Melihat 10 Hunter dengan level dan XP tertinggi di server.", inline=False)

            elif val == "Voice Log":
                embed = discord.Embed(
                    title="🔊 Panduan Voice Log",
                    description="Bot mencatat berapa lama kamu nongkrong di Voice Channel secara permanen.",
                    color=discord.Color.blue()
                )
                embed.add_field(name="`!vclog`", value="Menampilkan total statistik durasi seluruh member di VC untuk hari ini.", inline=False)
                embed.add_field(name="`!vclog history`", value="Menampilkan menu untuk melihat data durasi VC pada tanggal/hari sebelumnya.", inline=False)

            elif val == "Admin Menu":
                embed = discord.Embed(
                    title="👑 Panduan Admin Menu",
                    description="Command khusus yang hanya bisa diakses oleh petinggi server.",
                    color=discord.Color.red()
                )
                embed.add_field(name="`!clear`", value="**Akses:** Owner Bot\n**Fungsi:** Menghapus (purge) pesan di channel (default 5, max 100).", inline=False)
                embed.add_field(name="`!spawnstats`", value="**Akses:** Administrator\n**Fungsi:** Command rahasia untuk memaksa dashboard statistik muncul ulang.", inline=False)
                embed.add_field(name="`!testxp <jumlah>`", value="**Akses:** Administrator\n**Fungsi:** Mode testing untuk suntik XP ke akun sendiri secara instan.", inline=False)
                embed.add_field(name="`!spawnhelp`", value="**Akses:** Administrator\n**Fungsi:** Command rahasia untuk memunculkan ulang dashboard help secara paksa.", inline=False)

            # >>> SOLUSI BUG HP: Waktu dimasukkan ke description agar tidak stuck <<<
            embed.description += f"\n\n⏳ *(Menu ini akan otomatis tertutup <t:{view.expire_ts}:R>)*"

            # 4. UBAH STATUS UI
            self.placeholder = f"Sedang melihat: {val}"
            
            # MATIKAN DROPDOWN AGAR MEMBER LAIN TIDAK BISA KLIK
            self.disabled = True 
            
            # MUNCULKAN TOMBOL SELESAI
            view.done_button.disabled = False
            if view.done_button not in view.children:
                view.add_item(view.done_button)

            teks_status = f"🔒 **Sedang dibaca oleh {interaction.user.mention}**"

            await interaction.response.edit_message(content=teks_status, embed=embed, view=view)

            try:
                view.cog.dashboard_message = await interaction.original_response()
            except Exception as e:
                print(f"[HelpDropdown] Gagal sinkronisasi pesan: {e!r}")

        except Exception as e:
            print(f"[HelpDropdown callback] GAGAL untuk user {interaction.user}: {e!r}")
            traceback.print_exc()
            view.locked_user = None
            view.expire_ts = None
            view.cancel_timer()


class DoneButton(discord.ui.Button):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        # WARNA TOMBOL DIUBAH MENJADI MERAH DAN TEKSNYA DIPERJELAS
        super().__init__(label="Selesai (Sedang Digunakan)", style=discord.ButtonStyle.danger, emoji="🛑", disabled=False)

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        view.message = interaction.message

        if view.locked_user is not None and view.locked_user != interaction.user.id:
            alert_embed = discord.Embed(
                title="⛔ AKSES DITOLAK!",
                description="Tombol ini hanya bisa ditekan oleh user yang sedang membaca menu saat ini.\nSilakan tunggu gilirannya!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(
                embed=alert_embed,
                ephemeral=True,
                view=WarningView(),
                delete_after=10.0
            )
            return

        await view.reset_dashboard(interaction)


class HelpDashboardView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.locked_user = None
        self.timeout_task = None
        self.expire_ts = None
        self.message = None

        self.dropdown = HelpDropdown(self)
        self.done_button = DoneButton(self)
        self.add_item(self.dropdown)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        print(f"\n[HelpDashboardView ERROR] item={item} user={interaction.user} error={error!r}")
        traceback.print_exc()
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message(
                    "❌ Terjadi kesalahan internal saat memuat menu.",
                    ephemeral=True,
                    view=WarningView(),
                    delete_after=10.0
                )
            except Exception:
                pass

    def start_timer(self):
        self.cancel_timer()
        self.timeout_task = self.cog.bot.loop.create_task(self.timer_logic())

    def cancel_timer(self):
        current = asyncio.current_task()
        if self.timeout_task and self.timeout_task is not current:
            self.timeout_task.cancel()
        self.timeout_task = None

    async def timer_logic(self):
        try:
            await asyncio.sleep(20.0)
            await self.reset_dashboard()
        except asyncio.CancelledError:
            pass

    async def reset_dashboard(self, interaction=None):
        self.dropdown.placeholder = "Pilih fitur yang ingin dilihat..."
        
        # NYALAKAN KEMBALI DROPDOWN UNTUK UMUM
        self.dropdown.disabled = False 
        
        # HAPUS TOMBOL SELESAI DARI LAYAR
        if self.done_button in self.children:
            self.remove_item(self.done_button)

        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )

        success = False

        if interaction:
            try:
                await interaction.response.edit_message(content=None, embed=embed, view=self)
                success = True
            except Exception as e:
                print(f"[reset_dashboard via interaction] ERROR: {e!r}")
                traceback.print_exc()
                if self.message:
                    try:
                        await self.message.edit(content=None, embed=embed, view=self)
                        success = True
                    except Exception as e2:
                        traceback.print_exc()
        elif self.message:
            try:
                await self.message.edit(content=None, embed=embed, view=self)
                success = True
            except Exception as e:
                print(f"[reset_dashboard via self.message] ERROR: {e!r}")
                traceback.print_exc()
                try:
                    channel = self.cog.bot.get_channel(self.cog.ROOM_HELP_ID)
                    if channel is None:
                        channel = await self.cog.bot.fetch_channel(self.cog.ROOM_HELP_ID)
                    fresh_msg = await channel.fetch_message(self.message.id)
                    await fresh_msg.edit(content=None, embed=embed, view=self)
                    self.message = fresh_msg
                    self.cog.dashboard_message = fresh_msg
                    success = True
                except Exception as e2:
                    traceback.print_exc()
        elif self.cog.dashboard_message:
            try:
                await self.cog.dashboard_message.edit(content=None, embed=embed, view=self)
                self.message = self.cog.dashboard_message
                success = True
            except Exception as e:
                print(f"[reset_dashboard via cog.dashboard_message] ERROR: {e!r}")
                traceback.print_exc()

        self.locked_user = None
        self.expire_ts = None
        self.cancel_timer()


class HelpMenu(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.remove_command('help')
        self.ROOM_HELP_ID = 1526498364932227092
        self.dashboard_message = None
        self.is_spawned = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.is_spawned:
            self.is_spawned = True
            await asyncio.sleep(3)
            await self.spawn_dashboard()

    async def spawn_dashboard(self):
        await self.bot.wait_until_ready()

        channel = self.bot.get_channel(self.ROOM_HELP_ID)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(self.ROOM_HELP_ID)
            except Exception:
                return

        try:
            await channel.purge(limit=100)
        except Exception as e:
            traceback.print_exc()

        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )

        view = HelpDashboardView(self)
        self.dashboard_message = await channel.send(embed=embed, view=view)
        view.message = self.dashboard_message

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def spawnhelp(self, ctx):
        """Memunculkan dashboard help secara paksa"""
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await self.spawn_dashboard()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if message.content.startswith("!spawnhelp"):
            return

        if message.channel.id == self.ROOM_HELP_ID:
            try:
                await message.delete()
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(HelpMenu(bot))