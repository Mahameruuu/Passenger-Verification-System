import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KTPDocumentResponse(BaseModel):
    """Satu event upload KTP (detection_event). Gambar tidak dikirim — hanya key."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="event_id")
    person_id: uuid.UUID | None = Field(
        default=None,
        description="NULL sampai OCR membaca NIK dan menautkan dokumen ke person.",
    )
    raw_image_key: str | None = Field(
        default=None, description="Object key di MinIO, mis. ktp/2026/07/<uuid>.jpg"
    )
    image_url: str | None = Field(
        default=None, description="Presigned URL sementara untuk menampilkan gambar."
    )
    ocr_status: str | None = Field(
        default=None,
        validation_alias="verification_status",
        description="PENDING sampai OCR dijalankan.",
    )
    event_timestamp: datetime


class ErrorResponse(BaseModel):
    detail: str
