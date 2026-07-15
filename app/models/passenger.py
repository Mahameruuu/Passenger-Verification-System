from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, Enum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import Gender, RegistrationStatus

if TYPE_CHECKING:
    from app.models.audit_log import AuditLog
    from app.models.boarding_log import BoardingLog
    from app.models.face_registration import FaceRegistration
    from app.models.ktp_document import KTPDocument


class Passenger(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Master identitas penumpang. Satu baris = satu orang, dikunci oleh NIK."""

    __tablename__ = "passengers"

    nik: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    birth_place: Mapped[str | None] = mapped_column(String(100), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[Gender | None] = mapped_column(
        Enum(Gender, name="gender", native_enum=True), nullable=True
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, name="registration_status", native_enum=True),
        nullable=False,
        default=RegistrationStatus.DRAFT,
        server_default=RegistrationStatus.DRAFT.value,
    )

    ktp_documents: Mapped[list["KTPDocument"]] = relationship(
        back_populates="passenger", cascade="all, delete-orphan", passive_deletes=True
    )
    face_registrations: Mapped[list["FaceRegistration"]] = relationship(
        back_populates="passenger", cascade="all, delete-orphan", passive_deletes=True
    )
    boarding_logs: Mapped[list["BoardingLog"]] = relationship(
        back_populates="passenger", passive_deletes=True
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="passenger", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("nik ~ '^[0-9]{16}$'", name="nik_format"),
        CheckConstraint("char_length(full_name) > 0", name="full_name_not_blank"),
        Index("ix_passengers_nik", "nik", unique=True),
        Index("ix_passengers_registration_status", "registration_status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Passenger id={self.id} nik={self.nik}>"
