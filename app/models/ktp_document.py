import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import OCRStatus

if TYPE_CHECKING:
    from app.models.passenger import Passenger


class KTPDocument(Base, UUIDPrimaryKeyMixin):
    """Satu kali upload foto KTP + hasil mentah OCR-nya.

    Riwayat disimpan (bukan di-overwrite) supaya upload ulang / re-OCR
    bisa diaudit. File gambar ada di storage/ktp/..., DB hanya menyimpan path.

    passenger_id NULL = dokumen sudah di-upload tapi belum ditautkan ke
    penumpang, karena NIK baru diketahui setelah OCR berjalan (tahap berikutnya).
    """

    __tablename__ = "ktp_documents"

    passenger_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("passengers.id", ondelete="CASCADE"),
        nullable=True,
    )
    image_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ocr_status: Mapped[OCRStatus] = mapped_column(
        Enum(OCRStatus, name="ocr_status", native_enum=True),
        nullable=False,
        default=OCRStatus.PENDING,
        server_default=OCRStatus.PENDING.value,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    passenger: Mapped["Passenger | None"] = relationship(
        back_populates="ktp_documents"
    )

    __table_args__ = (
        Index("ix_ktp_documents_passenger_id", "passenger_id"),
        Index("ix_ktp_documents_ocr_status", "ocr_status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KTPDocument id={self.id} status={self.ocr_status}>"
