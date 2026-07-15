import uuid
from datetime import date, datetime

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Gender
from app.schemas.ktp import KTPDocumentResponse


class PersonResponse(BaseModel):
    """Identitas dari tabel `person`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="person_id")
    nik: str | None = Field(default=None, validation_alias="citizen_id")
    full_name: str
    id_card_type: str | None = None
    status: str
    registration_status: str | None = Field(default=None, validation_alias="status")
    created_at: datetime
    updated_at: datetime

    @field_validator("nik", mode="before")
    @classmethod
    def decrypt_citizen_id(cls, v: Any) -> Any:
        if isinstance(v, str):
            from app.core.security import decrypt_nik
            return decrypt_nik(v)
        return v


class ParsedKTP(BaseModel):
    """Field hasil OCR.

    Hanya `nik` dan `full_name` yang punya kolom di tabel `person`. Sisanya
    (TTL, jenis kelamin, alamat) tersimpan di `detection_event.ocr_result`
    sebagai JSON — tidak bisa di-query sebagai kolom.
    """

    nik: str | None = None
    full_name: str | None = None
    birth_place: str | None = None
    birth_date: date | None = None
    gender: Gender | None = None
    address: str | None = None


class ConfirmKTPRequest(BaseModel):
    """Data KTP yang sudah dikonfirmasi/dikoreksi petugas, siap disimpan ke DB.

    Dikirim saat menekan tombol Simpan. NIK & Nama wajib; sisanya opsional
    (tersimpan di detection_event.ocr_result, bukan sebagai kolom person).
    """

    nik: str = Field(..., pattern=r"^\d{16}$", description="16 digit angka")
    full_name: str = Field(..., min_length=1)
    birth_place: str | None = None
    birth_date: date | None = None
    gender: Gender | None = None
    address: str | None = None


class OCRResultResponse(BaseModel):
    ocr_status: str = Field(
        description=(
            "SUCCESS — NIK & Nama terbaca, person tersimpan. "
            "PARTIAL — field wajib tidak lengkap; person TIDAK dibuat. "
            "FAILED — tidak ada teks yang terbaca."
        )
    )
    parsed: ParsedKTP
    confidence: float = Field(description="Rata-rata keyakinan OCR (0–1)")
    warnings: list[str] = Field(default_factory=list)
    person_created: bool = Field(
        description="True bila baris `person` baru dibuat dari dokumen ini."
    )
    person: PersonResponse | None = None
    document: KTPDocumentResponse


# Alias lama supaya schema lain tidak perlu ikut berubah.
PassengerResponse = PersonResponse
