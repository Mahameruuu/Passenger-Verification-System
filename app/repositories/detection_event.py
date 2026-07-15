import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.gcp import DetectionEvent


class DetectionEventRepository:
    """Akses tabel `detection_event`.

    Ini satu-satunya tabel yang punya `raw_image_key` (object key MinIO), jadi
    di sinilah lokasi file di MinIO dicatat.

    CATATAN: tabel `detection_event` di database TIDAK punya kolom `ocr_result`.
    Parameter `ocr_result` tetap diterima agar pemanggil (OCR, registrasi wajah,
    pencocokan) tidak perlu berubah, tetapi nilainya SENGAJA DIABAIKAN — tidak
    ada kolom untuk menyimpannya.
    """

    # Field yang diterima demi kompatibilitas pemanggil tapi tidak ada kolomnya.
    _IGNORED = frozenset({"ocr_result"})

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        event_type: str,
        person_id: uuid.UUID | None = None,
        embedding_id: uuid.UUID | None = None,
        confidence_score: float | None = None,
        ocr_result: dict[str, Any] | None = None,  # diterima, diabaikan (lihat docstring)
        verification_status: str | None = None,
        raw_image_key: str | None = None,
    ) -> DetectionEvent:
        event = DetectionEvent(
            event_type=event_type,
            event_timestamp=datetime.now(timezone.utc),
            person_id=person_id,
            embedding_id=embedding_id,
            confidence_score=confidence_score,
            verification_status=verification_status,
            raw_image_key=raw_image_key,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def get_by_id(self, event_id: uuid.UUID) -> DetectionEvent | None:
        return self.db.get(DetectionEvent, event_id)

    def update(self, event: DetectionEvent, **fields: Any) -> DetectionEvent:
        for key, value in fields.items():
            if key in self._IGNORED:
                continue  # kolom tidak ada di tabel — abaikan diam-diam
            setattr(event, key, value)
        self.db.flush()
        return event

    def list_by_person(self, person_id: uuid.UUID) -> list[DetectionEvent]:
        stmt = (
            select(DetectionEvent)
            .where(DetectionEvent.person_id == person_id)
            .order_by(DetectionEvent.event_timestamp.desc())
        )
        return list(self.db.scalars(stmt))
