import enum


class Gender(str, enum.Enum):
    """Jenis kelamin sesuai KTP."""

    LAKI_LAKI = "LAKI_LAKI"
    PEREMPUAN = "PEREMPUAN"


class RegistrationStatus(str, enum.Enum):
    """Status registrasi penumpang secara keseluruhan (state machine)."""

    DRAFT = "DRAFT"                      # baris dibuat, KTP belum diproses
    KTP_VERIFIED = "KTP_VERIFIED"        # OCR sukses, data identitas terisi
    FACE_REGISTERED = "FACE_REGISTERED"  # wajah sudah terdaftar (tahap berikutnya)
    ACTIVE = "ACTIVE"                    # siap dipakai untuk verifikasi boarding
    REJECTED = "REJECTED"                # ditolak (KTP tidak valid / wajah gagal)


class OCRStatus(str, enum.Enum):
    """Hasil pemrosesan OCR pada satu dokumen KTP."""

    PENDING = "PENDING"    # file tersimpan, OCR belum jalan
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"    # semua field wajib terbaca
    PARTIAL = "PARTIAL"    # sebagian field terbaca, perlu koreksi manual
    FAILED = "FAILED"


class FaceRegistrationStatus(str, enum.Enum):
    """Status satu baris pendaftaran wajah."""

    PENDING = "PENDING"    # foto tersimpan, embedding belum dibuat
    ACTIVE = "ACTIVE"      # wajah aktif yang dipakai saat verifikasi
    REPLACED = "REPLACED"  # digantikan pendaftaran wajah yang lebih baru
    REJECTED = "REJECTED"  # kualitas foto di bawah ambang batas


class BoardingType(str, enum.Enum):
    """Arah pergerakan penumpang saat verifikasi."""

    BOARDING = "BOARDING"        # masuk kapal
    DISEMBARKING = "DISEMBARKING"  # keluar kapal


class BoardingResult(str, enum.Enum):
    """Hasil pencocokan wajah pada satu percobaan verifikasi."""

    MATCHED = "MATCHED"
    NOT_MATCHED = "NOT_MATCHED"
    NO_FACE_DETECTED = "NO_FACE_DETECTED"
    ERROR = "ERROR"
