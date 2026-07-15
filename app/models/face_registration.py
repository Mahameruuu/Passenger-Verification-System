import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import FaceRegistrationStatus

if TYPE_CHECKING:
    from app.models.passenger import Passenger


class FaceRegistration(Base, UUIDPrimaryKeyMixin):
    """Pendaftaran wajah penumpang.

    embedding_path masih berupa path file .npy di local storage — placeholder
    sampai pgvector dipakai pada tahap berikutnya (kolom ini nanti diganti /
    didampingi kolom `embedding vector(512)`).
    """

    __tablename__ = "face_registrations"

    passenger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("passengers.id", ondelete="CASCADE"),
        nullable=False,
    )
    face_image_path: Mapped[str] = mapped_column(String(512), nullable=False)
    embedding_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    verification_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    registration_status: Mapped[FaceRegistrationStatus] = mapped_column(
        Enum(FaceRegistrationStatus, name="face_registration_status", native_enum=True),
        nullable=False,
        default=FaceRegistrationStatus.PENDING,
        server_default=FaceRegistrationStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    passenger: Mapped["Passenger"] = relationship(back_populates="face_registrations")

    __table_args__ = (
        CheckConstraint(
            "quality_score IS NULL OR quality_score BETWEEN 0 AND 1",
            name="quality_score_range",
        ),
        CheckConstraint(
            # Cosine similarity, rentangnya -1..1 (bukan 0..1).
            "verification_score IS NULL OR verification_score BETWEEN -1 AND 1",
            name="verification_score_range",
        ),
        Index("ix_face_registrations_passenger_id", "passenger_id"),
        Index("ix_face_registrations_registration_status", "registration_status"),
        # Hanya boleh ada SATU wajah ACTIVE per penumpang.
        Index(
            "uq_face_registrations_passenger_active",
            "passenger_id",
            unique=True,
            postgresql_where=text("registration_status = 'ACTIVE'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FaceRegistration id={self.id} status={self.registration_status}>"
