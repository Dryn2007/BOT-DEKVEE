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


def is_private_call(channel) -> bool:
    """True kalau channel berada di kategori room privat (auto private call)."""
    if channel is None:
        return False
    return getattr(channel, "category_id", None) == PRIVATE_CALL_CATEGORY_ID
