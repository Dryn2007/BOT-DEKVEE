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
# ROOM CHAT PER PRODI — SUMBER TUNGGAL
# --------------------------------------------------------------------
# Key-nya sama dengan key role di ROLE_IDS (cogs/autogate.py), value-nya ID
# room chat prodi tersebut. Dipakai streak.py (hitung streak chat harian) dan
# voicecheck.py (kabar "room voice udah 1 jam" dikirim ke chat prodi member).
#
# Prodi yang belum punya room chat sendiri (mis. INFOR) sengaja nggak
# didaftarkan — member-nya otomatis dikabari di chat voice-nya saja.
# ====================================================================
PRODI_CHAT_ROOMS = {
    "DKV":     1526599646674161736,
    "TEKINFO": 1526601262861389964,
    "SISFOR":  1526606411591585932,
    "TEKTEL":  1526607541310591028,
}


def is_private_call(channel) -> bool:
    """True kalau channel berada di kategori room privat (auto private call)."""
    if channel is None:
        return False
    return getattr(channel, "category_id", None) == PRIVATE_CALL_CATEGORY_ID
