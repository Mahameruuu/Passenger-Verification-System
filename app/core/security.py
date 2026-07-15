import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.config import settings

# NIK selalu 16 digit = TEPAT satu blok AES (16 byte). Karena itu tidak perlu
# (dan tidak boleh) ada padding PKCS7: hasilnya harus tetap 16 byte = 32 hex,
# supaya muat di kolom citizen_id VARCHAR(32) di database GCP.
_BLOCK = 16

# Label domain-separation untuk menurunkan IV dari passphrase. IV di-derive dari
# KUNCI (bukan dari NIK) supaya ciphertext tetap DETERMINISTIK — syarat mutlak
# agar NIK bisa dicari (get_by_citizen_id) dan constraint UNIQUE tetap berfungsi.
_IV_LABEL = b"pivs-nik-iv-v1"


def _key() -> bytes:
    """Kunci 256-bit (32 byte) dari passphrase ENCRYPTION_KEY di .env."""
    return hashlib.sha256(settings.encryption_key.encode("utf-8")).digest()


def _iv() -> bytes:
    """IV 16-byte deterministik, diturunkan dari passphrase (bukan dari NIK)."""
    return hashlib.sha256(_IV_LABEL + settings.encryption_key.encode("utf-8")).digest()[:_BLOCK]


def _pad(nik: str) -> bytes:
    # NIK dijamin 16 digit oleh OCR + CheckConstraint; ljust/truncate hanya
    # pengaman agar input ke AES selalu tepat 16 byte.
    return nik.ljust(_BLOCK)[:_BLOCK].encode("utf-8")


def encrypt_nik(nik: str) -> str:
    """Mengenkripsi NIK dengan AES-256-CBC deterministik.

    Hasil enkripsi persis 16 byte (32 karakter hex) sehingga muat di kolom
    citizen_id VARCHAR(32). Bersifat deterministik (IV diturunkan dari kunci,
    bukan acak) supaya NIK yang sama selalu menghasilkan ciphertext yang sama —
    ini yang membuat pencarian NIK dan constraint UNIQUE tetap bekerja.
    """
    if not nik:
        return nik
    cipher = Cipher(algorithms.AES(_key()), modes.CBC(_iv()))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(_pad(nik)) + encryptor.finalize()
    return encrypted.hex()


def decrypt_nik(encrypted_nik: str) -> str:
    """Mendekripsi NIK yang tersimpan sebagai 32 karakter hex.

    Kompatibel mundur: mengenali tiga bentuk data yang mungkin ada di database —
    CBC (baru), ECB (data lama sebelum migrasi mode), dan plaintext (data paling
    lama, 16 digit angka mentah).
    """
    if not encrypted_nik:
        return encrypted_nik
    # Data lama yang belum terenkripsi (bukan 32 hex): kembalikan apa adanya.
    if len(encrypted_nik) != 32:
        return encrypted_nik
    # Coba CBC (format baru) lebih dulu, lalu ECB (format lama).
    for mode in (modes.CBC(_iv()), modes.ECB()):
        plain = _try_decrypt(encrypted_nik, mode)
        if plain is not None:
            return plain
    # 32 karakter tapi bukan ciphertext yang kita kenali: kembalikan apa adanya.
    return encrypted_nik


def _try_decrypt(encrypted_hex: str, mode) -> str | None:
    """Dekripsi satu mode; kembalikan NIK bila hasilnya valid, else None.

    NIK yang valid = 16 digit angka. Kalau hasil dekripsi bukan itu, berarti
    mode-nya salah (mis. mencoba CBC pada data ECB) — kembalikan None agar
    pemanggil mencoba mode berikutnya.
    """
    try:
        data = bytes.fromhex(encrypted_hex)
        cipher = Cipher(algorithms.AES(_key()), mode)
        decryptor = cipher.decryptor()
        out = (decryptor.update(data) + decryptor.finalize()).decode("utf-8").strip()
    except Exception:
        return None
    return out if out.isdigit() and len(out) == 16 else None


def _encrypt_ecb_legacy(nik: str) -> str:
    """Bentuk ECB lama dari sebuah NIK. HANYA untuk pencocokan saat lookup data
    lama yang belum dimigrasi — jangan dipakai untuk menyimpan data baru."""
    cipher = Cipher(algorithms.AES(_key()), modes.ECB())
    encryptor = cipher.encryptor()
    return (encryptor.update(_pad(nik)) + encryptor.finalize()).hex()


def citizen_id_search_forms(nik: str) -> list[str]:
    """Semua bentuk citizen_id yang mungkin tersimpan untuk sebuah NIK.

    Dipakai oleh lookup agar baris lama (ECB / plaintext) tetap ditemukan
    walaupun data baru sudah disimpan sebagai CBC. Urutan tidak penting.
    """
    if not nik:
        return [nik]
    return [encrypt_nik(nik), _encrypt_ecb_legacy(nik), nik]
