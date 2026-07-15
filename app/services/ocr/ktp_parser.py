from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher

from app.models.enums import Gender
from app.services.ocr.engine import OCRLine

# Label pada KTP → nama field internal. Perbandingan dilakukan setelah
# normalisasi (huruf saja, kapital), dengan toleransi fuzzy karena OCR sering
# salah baca label itu sendiri ("NlK", "A1amat", "Jenls Kelamin").
LABEL_ALIASES: dict[str, str] = {
    "NIK": "nik",
    "NAMA": "full_name",
    "TEMPATTGLLAHIR": "ttl",
    "TEMPATTANGGALLAHIR": "ttl",
    "TEMPATTGLLAHR": "ttl",
    "JENISKELAMIN": "gender",
    "ALAMAT": "address",
    "RTRW": "rt_rw",
    "KELDESA": "kelurahan",
    "DESAKELURAHAN": "kelurahan",
    "KECAMATAN": "kecamatan",
    "AGAMA": "agama",
    "STATUSPERKAWINAN": "status_perkawinan",
    "PEKERJAAN": "pekerjaan",
    "KEWARGANEGARAAN": "kewarganegaraan",
    "BERLAKUHINGGA": "berlaku_hingga",
    "GOLDARAH": "gol_darah",
    "PROVINSI": "provinsi",
    "KOTA": "kota",
    "KABUPATEN": "kota",
}

# Label yang nilainya ikut menyusun alamat lengkap, sesuai urutan tampil di KTP.
ADDRESS_PARTS = ("address", "rt_rw", "kelurahan", "kecamatan")

FIELD_MIN_RATIO = 0.78  # ambang kemiripan label; di bawah ini dianggap bukan label

# OCR rutin tertukar antara huruf dan angka pada NIK yang seharusnya 16 digit.
DIGIT_LOOKALIKES = str.maketrans(
    {"O": "0", "o": "0", "D": "0", "Q": "0",
     "I": "1", "i": "1", "l": "1", "L": "1", "|": "1", "!": "1",
     "Z": "2", "z": "2",
     "E": "3",
     "A": "4",
     "S": "5", "s": "5",
     "G": "6",
     "T": "7",
     "B": "8",
     "g": "9", "q": "9"}
)

# Kebalikannya, untuk LABEL: OCR juga membaca huruf sebagai angka ("N1K", "A1amat").
# Label tidak pernah mengandung angka, jadi angka di label pasti salah baca.
LETTER_LOOKALIKES = str.maketrans(
    {"0": "O", "1": "I", "3": "E", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
)

NIK_PATTERN = re.compile(r"\b(\d{16})\b")
DATE_PATTERN = re.compile(r"(\d{1,2})\s*[-/.\s]\s*(\d{1,2})\s*[-/.\s]\s*(\d{4})")

# Kandidat NIK tanpa label: 16 karakter yang semuanya angka atau huruf mirip angka.
NIK_CANDIDATE_PATTERN = re.compile(r"[0-9OoDQIilL|!ZzEeAaSsGgTtBb]{16,}")


@dataclass
class KTPFields:
    """Hasil parsing satu KTP. Semua field opsional — OCR bisa gagal sebagian."""

    nik: str | None = None
    full_name: str | None = None
    birth_place: str | None = None
    birth_date: date | None = None
    gender: Gender | None = None
    address: str | None = None
    raw_lines: list[dict] = field(default_factory=list)
    fields_found: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def is_complete(self) -> bool:
        """Cukup lengkap untuk membuat baris passengers."""
        return self.nik is not None and self.full_name is not None

    def to_json(self) -> dict:
        """Bentuk yang disimpan ke ktp_documents.ocr_json (bukti mentah + hasil)."""
        return {
            "parsed": {
                "nik": self.nik,
                "full_name": self.full_name,
                "birth_place": self.birth_place,
                "birth_date": self.birth_date.isoformat() if self.birth_date else None,
                "gender": self.gender.value if self.gender else None,
                "address": self.address,
            },
            "fields_found": self.fields_found,
            "raw_lines": self.raw_lines,
            "warnings": self.warnings,
            "confidence": round(self.confidence, 4),
        }


def _normalize_label(text: str) -> str:
    """'N1K' → 'NIK', 'Tempat/Tgl Lahir' → 'TEMPATTGLLAHIR'."""
    return re.sub(r"[^A-Za-z]", "", text.translate(LETTER_LOOKALIKES)).upper()


def _match_label(candidate: str) -> str | None:
    """Cocokkan potongan teks dengan label KTP, toleran terhadap salah baca OCR."""
    key = _normalize_label(candidate)
    if not key or len(key) < 3:
        return None
    if key in LABEL_ALIASES:
        return LABEL_ALIASES[key]

    best_field, best_ratio = None, 0.0
    for alias, field_name in LABEL_ALIASES.items():
        ratio = SequenceMatcher(None, key, alias).ratio()
        if ratio > best_ratio:
            best_field, best_ratio = field_name, ratio
    return best_field if best_ratio >= FIELD_MIN_RATIO else None


def _clean_nik(raw: str) -> tuple[str | None, str | None]:
    """Kembalikan (nik, warning). Huruf yang mirip angka dikoreksi lebih dulu."""
    compact = re.sub(r"[\s.\-]", "", raw)
    direct = NIK_PATTERN.search(compact)
    if direct:
        return direct.group(1), None

    corrected = compact.translate(DIGIT_LOOKALIKES)
    match = NIK_PATTERN.search(corrected)
    if match:
        return match.group(1), (
            f"NIK dikoreksi dari '{raw.strip()}' menjadi '{match.group(1)}' "
            "(huruf mirip angka). Verifikasi manual disarankan."
        )

    digits = re.sub(r"\D", "", corrected)
    if digits:
        return None, (
            f"NIK tidak valid: terbaca {len(digits)} digit, seharusnya 16 "
            f"('{raw.strip()}')."
        )
    return None, f"NIK tidak terbaca dari '{raw.strip()}'."


def _parse_ttl(raw: str) -> tuple[str | None, date | None, str | None]:
    """'JAKARTA, 17-08-1985' → ('JAKARTA', date(1985, 8, 17), warning)."""
    match = DATE_PATTERN.search(raw)
    birth_date, warning = None, None

    if match:
        day, month, year = (int(g) for g in match.groups())
        try:
            birth_date = datetime(year, month, day).date()
        except ValueError:
            warning = f"Tanggal lahir tidak valid: '{match.group(0)}'."
        place_part = raw[: match.start()]
    else:
        warning = f"Tanggal lahir tidak ditemukan pada '{raw.strip()}'."
        place_part = raw

    place = place_part.strip().strip(",").strip()
    place = re.sub(r"\s+", " ", place)
    return (place.upper() or None), birth_date, warning


def _parse_gender(raw: str) -> tuple[Gender | None, str | None]:
    value = _normalize_label(raw)
    if "PEREMPUAN" in value or "WANITA" in value:
        return Gender.PEREMPUAN, None
    if "LAKI" in value or "PRIA" in value:
        return Gender.LAKI_LAKI, None
    return None, f"Jenis kelamin tidak dikenali dari '{raw.strip()}'."


def _clean_value(value: str) -> str:
    """Buang titik dua, tanda hubung, titik di depan nilai yang sering dihasilkan OCR."""
    cleaned = value.strip()
    # Hapus karakter non-alphanumeric di awal nilai (seperti :, -, ., dll)
    cleaned = re.sub(r"^[:\-.\s]+", "", cleaned)
    return cleaned.strip()


def _strip_leading_colon(value: str) -> str:
    """Buang titik dua di depan nilai.

    OCR sering memecah 'Nama : BUDI' menjadi dua box: 'Nama' dan ': BUDI'.
    Tanpa ini, nama tersimpan sebagai ': BUDI'.
    """
    return _clean_value(value)


def _split_label_value(line: str) -> tuple[str | None, str]:
    """Pisahkan 'Nama : BUDI' menjadi ('full_name', 'BUDI')."""
    if ":" in line:
        head, _, tail = line.partition(":")
        field_name = _match_label(head)
        if field_name:
            return field_name, _clean_value(tail)
        return None, _clean_value(line)

    # Baris berisi label saja — OCR memecah label dan nilainya ke box berbeda.
    # Dicoba lebih dulu, supaya 'Tempat/Tgl Lahir' tidak salah terbaca sebagai
    # label 'Tempat/Tgl' dengan nilai 'Lahir'.
    if field_name := _match_label(line):
        return field_name, ""

    # Label & nilai menempel dalam satu box tanpa titik dua ('NIK 3175...').
    words = line.split()
    for take in (3, 2, 1):
        if len(words) > take:
            field_name = _match_label(" ".join(words[:take]))
            if field_name:
                return field_name, _clean_value(" ".join(words[take:]))
    return None, _clean_value(line)


def _strip_trailing_label(value: str) -> str:
    """Buang label lain yang menempel di ekor nilai.

    Contoh: 'LAKI-LAKI Gol. Darah' → 'LAKI-LAKI'. Pada KTP, Jenis Kelamin dan
    Gol. Darah berada di baris yang sama, sehingga OCR sering menggabungkannya.
    """
    words = value.split()
    for i in range(1, len(words)):
        if _match_label(" ".join(words[i:])):
            return " ".join(words[:i]).strip()
    return value.strip()


def parse_ktp(lines: list[OCRLine]) -> KTPFields:
    """Ubah baris-baris OCR mentah menjadi field KTP terstruktur."""
    result = KTPFields()
    result.raw_lines = [
        {"text": ln.text, "confidence": round(ln.confidence, 4)} for ln in lines
    ]
    if lines:
        result.confidence = sum(ln.confidence for ln in lines) / len(lines)

    values: dict[str, str] = {}
    pending: str | None = None  # label yang nilainya ada di baris berikutnya

    for line in lines:
        text = re.sub(r"\s+", " ", line.text).strip()
        if not text:
            continue

        field_name, value = _split_label_value(text)

        if field_name:
            if value:
                values.setdefault(field_name, value)
                pending = None
            else:
                pending = field_name  # nilainya menyusul di baris berikutnya
            continue

        if pending:
            cleaned = _strip_leading_colon(value)
            if cleaned:
                values.setdefault(pending, cleaned)
            pending = None

    # NIK: bisa muncul tanpa label sama sekali (label ikut rusak / tidak terbaca).
    # Cadangannya: cari runtun 16 karakter yang berupa angka atau huruf mirip angka.
    nik_source = values.get("nik")
    if nik_source is None:
        for line in lines:
            compact = re.sub(r"[\s.\-]", "", line.text)
            match = NIK_CANDIDATE_PATTERN.search(compact)
            # Minimal separuhnya sudah berupa angka asli, supaya kata biasa
            # seperti 'KEWARGANEGARAAN' tidak salah dikira NIK.
            if match and sum(c.isdigit() for c in match.group(0)) >= 8:
                nik_source = match.group(0)
                break

    if nik_source:
        result.nik, warning = _clean_nik(_clean_value(nik_source))
        if warning:
            result.warnings.append(warning)
    else:
        result.warnings.append("NIK tidak ditemukan pada dokumen.")

    if name := values.get("full_name"):
        result.full_name = _clean_value(_strip_trailing_label(name)).upper() or None
    else:
        result.warnings.append("Nama tidak ditemukan pada dokumen.")

    if ttl := values.get("ttl"):
        result.birth_place, result.birth_date, warning = _parse_ttl(_clean_value(ttl))
        if warning:
            result.warnings.append(warning)
    else:
        result.warnings.append("Tempat/Tanggal Lahir tidak ditemukan pada dokumen.")

    if gender := values.get("gender"):
        result.gender, warning = _parse_gender(_clean_value(_strip_trailing_label(gender)))
        if warning:
            result.warnings.append(warning)
    else:
        result.warnings.append("Jenis Kelamin tidak ditemukan pada dokumen.")

    # Alamat lengkap disusun dari beberapa label yang terpisah di KTP.
    parts = [_clean_value(values[k]) for k in ADDRESS_PARTS if values.get(k, "").strip()]
    if parts:
        result.address = ", ".join(parts).upper()
    else:
        result.warnings.append("Alamat tidak ditemukan pada dokumen.")

    result.fields_found = values
    return result
