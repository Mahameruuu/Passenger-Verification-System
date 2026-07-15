from __future__ import annotations

import uuid

from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import KTPDocumentNotFoundError
from app.models.gcp import DetectionEvent, EventType, VerificationStatus
from app.repositories.detection_event import DetectionEventRepository
from app.services.storage import StorageService

KTP_PREFIX = "ktp"


class KTPService:
    """Upload foto KTP ke MinIO, catat object key-nya ke `detection_event`.

    OCR belum dijalankan di sini — event dicatat dengan verification_status
    PENDING agar bisa diproses menyusul.
    """

    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()
        self.events = DetectionEventRepository(db)

    async def upload(self, file: UploadFile) -> DetectionEvent:
        stored = await self.storage.save_upload(file, prefix=KTP_PREFIX)

        try:
            event = self.events.create(
                event_type=EventType.KTP_REGISTRATION,
                raw_image_key=stored.key,
                verification_status=VerificationStatus.PENDING,
                ocr_result={
                    "upload": {
                        "original_filename": file.filename,
                        "content_type": stored.content_type,
                        "file_size": stored.size,
                    }
                },
            )
            self.db.commit()
        except SQLAlchemyError:
            # Insert gagal → objek yang terlanjur diunggah harus dibuang, jangan
            # sampai ada file yatim di MinIO yang tidak dikenal database.
            self.db.rollback()
            self.storage.delete(stored.key)
            raise

        self.db.refresh(event)
        return event

    def get(self, event_id: uuid.UUID) -> DetectionEvent:
        event = self.events.get_by_id(event_id)
        if event is None or event.event_type != EventType.KTP_REGISTRATION:
            raise KTPDocumentNotFoundError(f"Dokumen KTP {event_id} tidak ditemukan.")
        return event

    def image_url(self, event: DetectionEvent) -> str | None:
        if not event.raw_image_key:
            return None
        return self.storage.presigned_url(event.raw_image_key)


__all__ = ["KTPService", "KTP_PREFIX", "VerificationStatus"]
