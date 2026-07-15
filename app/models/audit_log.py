import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.passenger import Passenger


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Jejak audit untuk data pribadi (KTP/wajah): siapa melakukan apa, kapan.

    Sengaja generik (entity_type + entity_id) supaya tidak perlu tabel audit
    baru setiap kali ada entitas baru.
    """

    __tablename__ = "audit_logs"

    passenger_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("passengers.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor: Mapped[str] = mapped_column(String(100), nullable=False)  # user/petugas/sistem
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # KTP_UPLOADED, ...
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # passengers
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    passenger: Mapped["Passenger | None"] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_passenger_id", "passenger_id"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog id={self.id} action={self.action}>"
