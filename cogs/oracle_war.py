import discord
from discord.ext import commands, tasks
import oci
import time
import asyncio
from datetime import datetime, timedelta

# ==========================================
# 1. KONFIGURASI ORACLE CLOUD
# ==========================================
try:
    config = oci.config.from_file(file_location="config")
    compute_client = oci.core.ComputeClient(config)
    OCI_READY = True
except Exception as e:
    print(f"⚠️ [OracleWar] OCI config tidak ditemukan, fitur Oracle dinonaktifkan: {e}")
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

def try_create_instance():
    try:
        response = compute_client.launch_instance(
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
        return "SUCCESS"
    except Exception as e:
        # Menangkap SEMUA error dengan aman agar bot tidak stuck
        error_msg = str(e)
        if "Out of capacity" in error_msg or "Out of host capacity" in error_msg:
            return "CAPACITY"
        else:
            return f"ERROR SISTEM: {error_msg}"

# ==========================================
# 3. CLASS COG ORACLE WAR
# ==========================================
class OracleWar(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Variabel untuk menyimpan pesan yang akan terus diedit
        self.status_message = None

    @commands.Cog.listener()
    async def on_ready(self):
        print("✅ Modul War Oracle berhasil dimuat!")
        if not OCI_READY:
            print("⚠️ OCI belum dikonfigurasi, war task tidak dijalankan.")
            return
        # Menyalakan war secara otomatis saat bot online
        if not self.war_task.is_running():
            self.war_task.start()
            print("🚀 Proses war langsung berjalan otomatis!")

    @tasks.loop(minutes=2)
    async def war_task(self):
        if not OCI_READY:
            self.war_task.stop()
            return
        channel = self.bot.get_channel(TARGET_CHANNEL_ID)
        if not channel:
            print("❌ Channel tujuan tidak ditemukan. Pastikan TARGET_CHANNEL_ID benar.")
            return

        waktu_mulai = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M:%S")
        pesan_awal = f"⏳ `[{waktu_mulai}]` Memulai percobaan membuat server ke sistem Oracle..."
        
        # Jika belum ada pesan yang dikirim, kirim pesan baru. Jika sudah ada, edit pesan lamanya.
        if self.status_message is None:
            self.status_message = await channel.send(pesan_awal)
        else:
            try:
                await self.status_message.edit(content=pesan_awal)
            except discord.NotFound:
                # Berjaga-jaga jika pesan terhapus manual oleh seseorang di Discord
                self.status_message = await channel.send(pesan_awal)
        
        # Eksekusi fungsi pembuatan server
        status = await asyncio.to_thread(try_create_instance)
        
        # Waktu selesai percobaan
        waktu_selesai = (datetime.utcnow() + timedelta(hours=7)).strftime("%H:%M:%S")
        
        if status == "SUCCESS":
            pesan_sukses = f"🎉 `[{waktu_selesai}]` **BERHASIL!!!** Server Oracle sudah dibuat! Segera cek dasbor, misi selesai!"
            await self.status_message.edit(content=pesan_sukses)
            print("🎉 BERHASIL! Server sudah dibuat!")
            self.war_task.stop()
            
        elif status == "CAPACITY":
            pesan_gagal = f"❌ `[{waktu_selesai}]` Out of capacity. Mencoba lagi dalam 2 menit."
            await self.status_message.edit(content=pesan_gagal)
            print("❌ Masih penuh (Out of capacity). Menunggu 2 menit...")
            
        else:
            pesan_error = f"⚠️ `[{waktu_selesai}]` {status}. Mencoba lagi dalam 2 menit."
            await self.status_message.edit(content=pesan_error)
            print(f"⚠️ {status}")

    # Mengubah command manual menjadi fitur darurat (opsional)
    @commands.command()
    async def startwar(self, ctx):
        if ctx.channel.id != TARGET_CHANNEL_ID: return
        if not OCI_READY:
            await ctx.send("⚠️ OCI belum dikonfigurasi. Fitur Oracle tidak tersedia.")
            return
        if self.war_task.is_running():
            await ctx.send("⏳ Bot sudah otomatis berjalan di latar belakang!")
        else:
            self.status_message = None # Reset pesan agar membuat baru
            self.war_task.start()
            await ctx.send("🚀 Memulai ulang loop war!")

    @commands.command()
    async def stopwar(self, ctx):
        if ctx.channel.id != TARGET_CHANNEL_ID: return
        if self.war_task.is_running():
            self.war_task.stop()
            await ctx.send("🛑 **War Oracle dihentikan sementara.**")
        else:
            await ctx.send("War memang sedang tidak berjalan.")

# ==========================================
# 4. FUNGSI SETUP COG
# ==========================================
async def setup(bot):
    await bot.add_cog(OracleWar(bot))