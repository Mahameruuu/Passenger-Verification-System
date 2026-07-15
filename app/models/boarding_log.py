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
from app.models.enums import BoardingResult, BoardingType

if TYPE_CHECKING:
    from app.models.passenger import Passenger


class BoardingLog(Base, UUIDPrimaryKeyMixin):
    """Catatan setiap percobaan verifikasi wajah di gerbang kapal.

    passenger_id NULL = wajah tidak dikenali (percobaan gagal tetap dicatat
    untuk keperluan investigasi). FK memakai RESTRICT: riwayat boarding tidak
    boleh ikut terhapus saat data penumpang dihapus.
    """

    __tablename__ = "boarding_logs"

    passenger_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("passengers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    camera_name: Mapped[str] = mapped_column(String(100), nullable=False)
    boarding_type: Mapped[BoardingType] = mapped_column(
        Enum(BoardingType, name="boarding_type", native_enum=True), nullable=False
    )
    result: Mapped[BoardingResult] = mapped_column(
        Enum(BoardingResult, name="boarding_result", native_enum=True),
        nullable=False,
        default=BoardingResult.MATCHED,
        server_default=BoardingResult.MATCHED.value,
    )
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    captured_image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    boarding_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    passenger: Mapped["Passenger | None"] = relationship(back_populates="boarding_logs")

    __table_args__ = (
        CheckConstraint(
            "match_score IS NULL OR match_score BETWEEN 0 AND 1",
            name="match_score_range",
        ),
        CheckConstraint(
            "result <> 'MATCHED' OR passenger_id IS NOT NULL",
            name="matched_requires_passenger",
        ),
        Index("ix_boarding_logs_passenger_id", "passenger_id"),
        Index("ix_boarding_logs_boarding_time", "boarding_time"),
        # Query paling sering: riwayat 1 penumpang, terbaru dulu.
        Index(
            "ix_boarding_logs_passenger_id_boarding_time",
            "passenger_id",
            text("boarding_time DESC"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BoardingLog id={self.id} type={self.boarding_type}>"
