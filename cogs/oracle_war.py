import discord
from discord.ext import commands, tasks
import oci
import asyncio
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))


def _log(teks):
    """Print yang nggak bisa matiin war.

    Console yang encoding-nya bukan UTF-8 (cp1252 di Windows) melempar
    UnicodeEncodeError begitu ketemu emoji. Kalau itu kejadian di dalam
    tasks.loop, loop-nya berhenti PERMANEN cuma gara-gara gagal nge-print.
    """
    try:
        print(teks)
    except UnicodeEncodeError:
        print(teks.encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass

# ==========================================
# 1. KONFIGURASI ORACLE CLOUD
# ==========================================
# Default SDK OCI: connect timeout 10 detik dan TANPA retry. Jadi hiccup
# jaringan sedetik pun langsung dilaporkan sebagai "ERROR SISTEM", padahal
# request-nya belum sampai ke Oracle. Timeout dilonggarin + retry khusus
# error jaringan biar war-nya nggak berhenti gara-gara itu.
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 90.0
RETRY_MAX_ATTEMPTS = 3
RETRY_TOTAL_SECONDS = 75          # tetap di bawah interval loop (2 menit)
LOOP_MINUTES = 2
PETUNJUK_JARINGAN_SETELAH = 5     # gagal jaringan berturut-turut sebelum kasih petunjuk
MAX_DETAIL_CHAR = 350             # potong detail error biar pesan Discord nggak kepanjangan

# Hati-hati: `oci.exceptions.RequestException` TIDAK menangkap `ConnectTimeout`.
# Keduanya sama-sama turunan requests bawaan SDK (`BaseRequestException`), tapi
# bukan turunan satu sama lain — jadi yang dipakai induk bersamanya, biar semua
# kegagalan level koneksi (connect/read timeout, DNS, koneksi putus) kena.
# `ServiceError` bukan turunannya, jadi urutan except-nya tetap aman.
ERROR_JARINGAN = (
    getattr(oci.exceptions, "BaseRequestException", oci.exceptions.RequestException),
    oci.exceptions.RequestException,
    oci.exceptions.ConnectTimeout,
)


def _bikin_retry_strategy():
    """Retry HANYA untuk error jaringan (timeout/connection) dan throttle 429.

    5xx sengaja nggak di-retry: "Out of host capacity" itu HTTP 500, dan kalau
    di-retry di dalam satu percobaan, cadence war-nya jadi ngawur plus rawan
    kena throttle dari Oracle. Timeout & connection error SELALU di-retry oleh
    checker-nya SDK, nggak terpengaruh setelan 5xx ini.
    """
    return oci.retry.RetryStrategyBuilder(
        max_attempts_check=True,
        max_attempts=RETRY_MAX_ATTEMPTS,
        total_elapsed_time_check=True,
        total_elapsed_time_seconds=RETRY_TOTAL_SECONDS,
        service_error_check=True,
        service_error_retry_on_any_5xx=False,
        retry_base_sleep_time_seconds=3,
        retry_max_wait_between_calls_seconds=15,
        backoff_type=oci.retry.BACKOFF_FULL_JITTER_VALUE,
    ).get_retry_strategy()


try:
    config = oci.config.from_file(file_location="config")
    compute_client = oci.core.ComputeClient(
        config,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        retry_strategy=_bikin_retry_strategy(),
    )
    OCI_READY = True
except Exception as e:
    _log(f"⚠️ [OracleWar] OCI config tidak ditemukan, fitur Oracle dinonaktifkan: {e}")
    config = None
    compute_client = None
    OCI_READY = False

compartment_id = "ocid1.tenancy.oc1..aaaaaaaavotqdahvfb5b2epny5764gvur36v47vvhibzjw2glvghkjwptycq"
availability_domain = "Hpyp:AP-BATAM-1-AD-1"
shape = "VM.Standard.A1.Flex"

# ==========================================
# 2. PENGATURAN ROOM KHUSUS DISCORD
# ==========================================
TARGET_CHANNEL_ID = 1530011990150349031

def _jam():
    return datetime.now(WIB).strftime("%H:%M:%S")


def try_create_instance():
    """Coba bikin instance sekali.

    Return (jenis, detail) — jenis salah satu dari:
      SUCCESS  : instance jadi
      CAPACITY : stok shape di AD itu habis (normal, tinggal nunggu)
      NETWORK  : request nggak sampai ke Oracle (timeout/koneksi putus)
      LIMIT    : kena throttle Oracle (HTTP 429)
      AUTH     : kredensial/izin ditolak (nggak akan sembuh sendiri)
      ERROR    : sisanya
    """
    try:
        compute_client.launch_instance(
            oci.core.models.LaunchInstanceDetails(
                compartment_id=compartment_id,
                availability_domain=availability_domain,
                display_name="ServerOtomatis",
                shape=shape,
                shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=2,
                    memory_in_gbs=12
                ),
                source_details=oci.core.models.InstanceSourceViaImageDetails(
                    source_type="image",
                    image_id="ocid1.image.oc1.ap-batam-1.aaaaaaaaoxrbgllkvwabhxgnfmvxetdamw7i5kfkj33izyt7i74efrigmvgq",
                    boot_volume_size_in_gbs=200
                ),
                create_vnic_details=oci.core.models.CreateVnicDetails(
                    subnet_id="ocid1.subnet.oc1.ap-batam-1.aaaaaaaa7q6oo53etlhk5n7q4kp33afmtbvuxsj2ey53kwtwdeugx45rh6yq",
                    assign_public_ip=True
                ),
                metadata={"ssh_authorized_keys": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCrEoIFFVxUzDDgGWLdlfrhqrmwDoSHDgQj9rlcBNn+TTrkY3mwjKtxPMPRBqUeWvFRIVQUDdT2Ur19/smyT9T3Oh6OKSsjbisSJO8OmyWYCVvajx5wbmzvUIt1dI4sP7jNFyF+Ljw6nLaLd2fFUbCumhy8NUsMJhRMcf28kTCWgn14edNB1pjgXnQMmQhSL5Mgr6hZ9Mrzb5qy7W1j4RSJza2CEQySdMQfvcMFX5TRDqd9W8aeEypsjUMyOoy9MzhLnpH7RxTolCjz60HkvQPpvYs726wfJzlMbwO2jtRBz/3iMjAOv/8EENO2KLPAWi/JcxzjidFe4EpymPVdE5OH oracle-vps-2026"}
            )
        )
        return "SUCCESS", ""

    except oci.exceptions.ServiceError as e:
        # Sampai ke Oracle, tapi ditolak/gagal di sisi mereka.
        detail = f"HTTP {e.status} {e.code}: {e.message}"
        teks = f"{e.code} {e.message}".lower()
        if "capacity" in teks:
            return "CAPACITY", detail
        if e.status == 429:
            return "LIMIT", detail
        if e.status in (401, 403):
            return "AUTH", detail
        return "ERROR", detail

    except ERROR_JARINGAN as e:
        # ConnectTimeout / connection error: request BELUM sampai ke Oracle.
        return "NETWORK", str(e)

    except Exception as e:
        return "ERROR", f"{type(e).__name__}: {e}"


# ==========================================
# 3. CLASS COG ORACLE WAR
# ==========================================
class OracleWar(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Satu pesan status yang terus diedit, biar room-nya nggak kebanjiran.
        self.status_message = None
        self.percobaan = 0
        self.gagal_jaringan_berturut = 0
        self.mulai_sejak = None

    @commands.Cog.listener()
    async def on_ready(self):
        _log("✅ Modul War Oracle berhasil dimuat!")
        if not OCI_READY:
            _log("⚠️ OCI belum dikonfigurasi, war task tidak dijalankan.")
            return
        # Menyalakan war secara otomatis saat bot online
        if not self.war_task.is_running():
            self.war_task.start()
            _log("🚀 Proses war langsung berjalan otomatis!")

    def durasi_war(self):
        if self.mulai_sejak is None:
            return "0 menit"
        menit = int((datetime.now(WIB) - self.mulai_sejak).total_seconds() // 60)
        if menit < 60:
            return f"{menit} menit"
        return f"{menit // 60} jam {menit % 60} menit"

    async def set_status(self, teks):
        """Update pesan status; kirim baru kalau pesan lamanya sudah dihapus.

        Semua error Discord ditelan di sini dengan sengaja: tasks.loop BERHENTI
        permanen kalau ada exception yang nggak ketangkep, jadi war-nya nggak
        boleh mati cuma karena gagal ngedit satu pesan.
        """
        channel = self.bot.get_channel(TARGET_CHANNEL_ID)
        if channel is None:
            _log("❌ [OracleWar] Channel tujuan tidak ditemukan. Pastikan TARGET_CHANNEL_ID benar.")
            return

        try:
            if self.status_message is None:
                self.status_message = await channel.send(teks)
            else:
                await self.status_message.edit(content=teks)
        except discord.NotFound:
            try:
                self.status_message = await channel.send(teks)
            except Exception as e:
                _log(f"⚠️ [OracleWar] Gagal kirim pesan status: {e}")
        except Exception as e:
            _log(f"⚠️ [OracleWar] Gagal update pesan status: {e}")


    @tasks.loop(minutes=LOOP_MINUTES)
    async def war_task(self):
        if not OCI_READY:
            self.war_task.stop()
            return

        if self.mulai_sejak is None:
            self.mulai_sejak = datetime.now(WIB)
        self.percobaan += 1

        await self.set_status(
            f"⏳ `[{_jam()}]` Percobaan #{self.percobaan} — nyoba bikin server di Oracle..."
        )

        try:
            jenis, detail = await asyncio.to_thread(try_create_instance)
        except Exception as e:
            # Jaring terakhir: apa pun yang lolos tetap nggak boleh matiin loop.
            jenis, detail = "ERROR", f"{type(e).__name__}: {e}"

        if jenis == "NETWORK":
            self.gagal_jaringan_berturut += 1
        else:
            self.gagal_jaringan_berturut = 0

        ringkas = detail if len(detail) <= MAX_DETAIL_CHAR else detail[:MAX_DETAIL_CHAR] + "…"
        lagi = f"Nyoba lagi {LOOP_MINUTES} menit."

        if jenis == "SUCCESS":
            await self.set_status(
                f"🎉 `[{_jam()}]` **BERHASIL!!!** Server Oracle sudah dibuat setelah "
                f"{self.percobaan} percobaan ({self.durasi_war()}). Segera cek dasbor, misi selesai!"
            )
            _log("🎉 BERHASIL! Server sudah dibuat!")
            self.war_task.stop()

        elif jenis == "CAPACITY":
            await self.set_status(
                f"❌ `[{_jam()}]` Out of capacity — percobaan #{self.percobaan}, "
                f"war jalan {self.durasi_war()}. {lagi}"
            )
            _log(f"❌ Masih penuh (Out of capacity). Percobaan #{self.percobaan}.")

        elif jenis == "NETWORK":
            petunjuk = ""
            if self.gagal_jaringan_berturut >= PETUNJUK_JARINGAN_SETELAH:
                petunjuk = (
                    f"\n🔌 Sudah **{self.gagal_jaringan_berturut}x gagal jaringan berturut-turut** — "
                    f"kemungkinan koneksi keluar dari server tempat bot jalan yang kena blok/lambat "
                    f"ke `oraclecloud.com`, jadi bukan Oracle-nya yang bermasalah."
                )
            await self.set_status(
                f"📡 `[{_jam()}]` Koneksi ke Oracle **timeout**, request belum sampai ke sana "
                f"(percobaan #{self.percobaan}). {lagi}{petunjuk}"
            )
            _log(f"📡 Timeout jaringan ke OCI (berturut-turut: {self.gagal_jaringan_berturut}). {detail}")

        elif jenis == "LIMIT":
            await self.set_status(
                f"🐢 `[{_jam()}]` Kena rate limit Oracle (429) di percobaan #{self.percobaan}. {lagi}\n"
                f"`{ringkas}`"
            )
            _log(f"🐢 Rate limit dari OCI: {detail}")

        elif jenis == "AUTH":
            await self.set_status(
                f"🔑 `[{_jam()}]` **War dihentikan** — kredensial/izin OCI ditolak, ini nggak akan "
                f"sembuh sendiri walau dicoba terus.\n`{ringkas}`\n"
                f"Benerin API key-nya di OCI Console, lalu jalankan `!startwar` lagi."
            )
            _log(f"🔑 Kredensial OCI ditolak, war dihentikan: {detail}")
            self.war_task.stop()

        else:
            await self.set_status(
                f"⚠️ `[{_jam()}]` Error dari Oracle di percobaan #{self.percobaan}. {lagi}\n"
                f"`{ringkas}`"
            )
            _log(f"⚠️ Error OCI: {detail}")

    # Mengubah command manual menjadi fitur darurat (opsional)
    @commands.command()
    async def startwar(self, ctx):
        if ctx.channel.id != TARGET_CHANNEL_ID:
            return
        if not OCI_READY:
            await ctx.send("⚠️ OCI belum dikonfigurasi. Fitur Oracle tidak tersedia.")
            return
        if self.war_task.is_running():
            await ctx.send("⏳ Bot sudah otomatis berjalan di latar belakang!")
        else:
            # Reset hitungan supaya laporannya mulai dari nol lagi.
            self.status_message = None
            self.percobaan = 0
            self.gagal_jaringan_berturut = 0
            self.mulai_sejak = None
            self.war_task.start()
            await ctx.send("🚀 Memulai ulang loop war!")

    @commands.command()
    async def stopwar(self, ctx):
        if ctx.channel.id != TARGET_CHANNEL_ID:
            return
        if self.war_task.is_running():
            self.war_task.stop()
            await ctx.send(
                f"🛑 **War Oracle dihentikan sementara.** "
                f"Total {self.percobaan} percobaan dalam {self.durasi_war()}."
            )
        else:
            await ctx.send("War memang sedang tidak berjalan.")


# ==========================================
# 4. FUNGSI SETUP COG
# ==========================================
async def setup(bot):
    await bot.add_cog(OracleWar(bot))
