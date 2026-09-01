# ====================================================================
# KONFIGURASI ROOM PRIVAT — SUMBER TUNGGAL
# --------------------------------------------------------------------
# File ini SENGAJA ditaruh di root (bukan di dalam folder cogs/), karena
# load_cogs() di main.py memuat SEMUA file .py di ./cogs sebagai extension
# dan file tanpa fungsi setup() akan membuat bot gagal start.
#
# Semua cog yang butuh tahu "apakah channel ini room privat?" harus
# mengimpor dari sini, jangan menyalin ID-nya lagi.
# ====================================================================

# ID kategori tempat semua room privat (auto private call) dibuat.
PRIVATE_CALL_CATEGORY_ID = 1528284380022313011

# ID channel untuk log/audit ke admin (pembuatan room, room dihapus,
# user ditambahkan, user dikick karena AFK, dst).
LOG_CHANNEL_ID = 1534469424084418600

# ====================================================================
# ROLE PRODI — SUMBER TUNGGAL
# --------------------------------------------------------------------
# Role SELALU dicari pakai ID, bukan nama: nama role gampang keubah/typo
# di Discord dan bikin verifikasi silent-fail.
#
# Dipakai autogate.py (verifikasi SKL + sync web), voicelog.py (rekap voice
# hanya kelihatan ke seprodi), dashboard.py (statistik per prodi).
#
# NAMBAH PRODI BARU? Isi tiga tempat ini saja:
#   1. ROLE_IDS + PRODI_ROLE_KEYS di file ini
#   2. PRODI_CHAT_ROOMS di file ini (ID room chat prodinya)
#   3. PRODI_KEYWORDS + WEB_PRODI_TO_ROLE di cogs/autogate.py (kata kunci OCR)
# ====================================================================
ROLE_IDS = {
    "MEMBER":  1526575163166949447,
    "TEKINFO": 1526566212077879438,  # S1 Teknologi Informasi
    "DKV":     1526565350731284532,  # S1 Desain Komunikasi Visual
    "TEKTEL":  1526566818024783872,  # S1 Teknik Telekomunikasi
    "INFOR":   1538489249895292951,  # S1 Informatika
    "SISFOR":  1526566441040478352,  # S1 Sistem Informasi
}

# Semua role prodi (tanpa MEMBER). Urutan di sini = urutan tampil di dashboard.
PRODI_ROLE_KEYS = ("DKV", "TEKINFO", "SISFOR", "TEKTEL", "INFOR")

# Versi list ID-nya, buat cog yang cuma butuh "role ini prodi apa bukan".
PRODI_ROLE_IDS = [ROLE_IDS[key] for key in PRODI_ROLE_KEYS]

# ====================================================================
# ROOM CHAT PER PRODI — SUMBER TUNGGAL
# --------------------------------------------------------------------
# Key-nya sama dengan key role di ROLE_IDS, value-nya ID room chat prodi
# tersebut. Dipakai streak.py (streak chat harian) dan autogate.py (nunjukin
# room prodi ke user yang baru lolos verifikasi).
# ====================================================================
PRODI_CHAT_ROOMS = {
    "DKV":     1526599646674161736,
    "TEKINFO": 1526601262861389964,
    "SISFOR":  1526606411591585932,
    "TEKTEL":  1526607541310591028,
    "INFOR":   1544359596775178280,
}


def prodi_room_mention(role_key) -> str | None:
    """Mention room chat prodi (`<#id>`), atau None kalau prodinya belum punya room."""
    room_id = PRODI_CHAT_ROOMS.get(role_key or "")
    return f"<#{room_id}>" if room_id else None


def is_private_call(channel) -> bool:
    """True kalau channel berada di kategori room privat (auto private call)."""
    if channel is None:
        return False
    return getattr(channel, "category_id", None) == PRIVATE_CALL_CATEGORY_ID
