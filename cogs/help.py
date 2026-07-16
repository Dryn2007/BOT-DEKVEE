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

        # IKAT PESAN KE MEMORI VIEW AGAR TIDAK HILANG SAAT RESET
        view.message = interaction.message

        # 1. CEK SISTEM ANTREAN / LOCKING
        if view.locked_user is not None and view.locked_user != interaction.user.id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="🔒 **Sedang digunakan**\nMenu bantuan sedang dipakai oleh user lain. Coba lagi sebentar ya.",
                    color=discord.Color.orange()
                ),
                ephemeral=True,
                view=WarningView(),
                delete_after=10.0
            )
            return

        try:
            # 2. KUNCI DASHBOARD UNTUK USER INI & RESTART TIMER
            view.locked_user = interaction.user.id
            view.start_timer()

            # >>> HITUNG TIMESTAMP KAPAN MENU AKAN TERTUTUP (dipakai HANYA untuk pemegang lock) <<<
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

            # >>> COUNTDOWN INI HANYA TAMPIL UNTUK PEMEGANG LOCK (dashboard di-edit khusus utk interaction ybs) <<<
            embed.add_field(
                name="⏳ Sesi Ditutup",
                value=f"Menu ini akan otomatis tertutup <t:{view.expire_ts}:R>\n(atau klik **Selesai Membaca** jika sudah selesai)",
                inline=False
            )

            # 4. UBAH STATUS UI
            self.placeholder = f"Sedang melihat: {val}"
            view.done_button.disabled = False

            await interaction.response.edit_message(embed=embed, view=view)

            # Sinkronkan referensi pesan (backup, kalau-kalau view.message belum ke-set)
            try:
                view.cog.dashboard_message = await interaction.original_response()
            except Exception as e:
                print(f"[HelpDropdown] Gagal sinkronisasi pesan: {e!r}")

        except Exception as e:
            print(f"[HelpDropdown callback] GAGAL untuk user {interaction.user} (id={interaction.user.id}): {e!r}")
            traceback.print_exc()
            # Lock dilepas paksa supaya user lain tidak ikut ketahan
            view.locked_user = None
            view.expire_ts = None
            view.cancel_timer()


class DoneButton(discord.ui.Button):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        super().__init__(label="Selesai Membaca", style=discord.ButtonStyle.success, emoji="✅", disabled=True)

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        view.message = interaction.message

        # Kalau locked_user sudah None (misal race condition dengan timer),
        # tetap izinkan klik ini untuk sinkronkan ulang tampilan, jangan ditolak mentah-mentah.
        if view.locked_user is not None and view.locked_user != interaction.user.id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="🔒 **Sedang digunakan**\nHanya user yang sedang membaca yang bisa ngeklik ini.",
                    color=discord.Color.orange()
                ),
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
        self.add_item(self.done_button)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        print(f"\n[HelpDashboardView ERROR] item={item} user={interaction.user} error={error!r}")
        traceback.print_exc()
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message(
                    "❌ Terjadi kesalahan internal saat memuat menu. Coba lagi beberapa saat atau hubungi Admin.",
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
        """Batalkan timer dengan aman, hindari sebuah task membatalkan dirinya sendiri."""
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
        self.done_button.disabled = True

        embed = discord.Embed(
            title="🛠️ Pusat Bantuan DekVee",
            description="Selamat datang di Pusat Bantuan!\n\nSilakan pilih menu di bawah ini untuk melihat detail fitur bot.\n\n*(Sistem menggunakan antrean: jika sedang dipakai, harap tunggu giliranmu)*",
            color=discord.Color.dark_theme()
        )

        success = False

        if interaction:
            try:
                await interaction.response.edit_message(embed=embed, view=self)
                success = True
            except Exception as e:
                print(f"[reset_dashboard via interaction] ERROR: {e!r}")
                traceback.print_exc()
                # FALLBACK: kalau edit lewat interaction gagal, coba lewat referensi pesan
                # supaya tampilan tetap ke-refresh walau responsenya gagal.
                if self.message:
                    try:
                        await self.message.edit(embed=embed, view=self)
                        success = True
                    except Exception as e2:
                        print(f"[reset_dashboard fallback via self.message] ERROR: {e2!r}")
                        traceback.print_exc()
        elif self.message:
            try:
                await self.message.edit(embed=embed, view=self)
                success = True
            except Exception as e:
                print(f"[reset_dashboard via self.message] ERROR: {e!r}")
                traceback.print_exc()
                # Coba fetch ulang pesannya, siapa tahu referensi lama sudah basi
                try:
                    channel = self.cog.bot.get_channel(self.cog.ROOM_HELP_ID)
                    if channel is None:
                        channel = await self.cog.bot.fetch_channel(self.cog.ROOM_HELP_ID)
                    fresh_msg = await channel.fetch_message(self.message.id)
                    await fresh_msg.edit(embed=embed, view=self)
                    self.message = fresh_msg
                    self.cog.dashboard_message = fresh_msg
                    success = True
                except Exception as e2:
                    print(f"[reset_dashboard retry via self.message] ERROR: {e2!r}")
                    traceback.print_exc()
        elif self.cog.dashboard_message:
            try:
                await self.cog.dashboard_message.edit(embed=embed, view=self)
                self.message = self.cog.dashboard_message
                success = True
            except Exception as e:
                print(f"[reset_dashboard via cog.dashboard_message] ERROR: {e!r}")
                traceback.print_exc()
        else:
            print("[reset_dashboard] Tidak ada referensi pesan sama sekali untuk di-reset!")

        if not success:
            print("[reset_dashboard] WARNING: gagal update tampilan, tapi lock tetap dilepas paksa.")

        # >>> PERBAIKAN UTAMA: lock & timer SELALU dilepas, apapun hasil edit-nya.
        # Sebelumnya lock hanya dilepas kalau success == True saat dipanggil via interaction,
        # jadi kalau edit_message gagal (mis. tombol "Selesai Membaca" kena error),
        # dashboard bisa terkunci permanen ke satu user. <<<
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
            print(f"[spawn_dashboard] Gagal purge channel: {e!r}")
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