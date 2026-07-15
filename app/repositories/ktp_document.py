import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import OCRStatus
from app.models.ktp_document import KTPDocument


class KTPDocumentRepository:
    """Akses data tabel ktp_documents. Tidak melakukan commit —
    transaksi dikendalikan oleh service."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        image_path: str,
        original_filename: str | None,
        content_type: str | None,
        file_size: int | None,
        passenger_id: uuid.UUID | None = None,
        ocr_status: OCRStatus = OCRStatus.PENDING,
    ) -> KTPDocument:
        document = KTPDocument(
            passenger_id=passenger_id,
            image_path=image_path,
            original_filename=original_filename,
            content_type=content_type,
            file_size=file_size,
            ocr_status=ocr_status,
        )
        self.db.add(document)
        self.db.flush()
        return document

    def get_by_id(self, document_id: uuid.UUID) -> KTPDocument | None:
        return self.db.get(KTPDocument, document_id)

    def update_ocr_result(
        self,
        document: KTPDocument,
        *,
        ocr_status: OCRStatus,
        ocr_json: dict | None = None,
        passenger_id: uuid.UUID | None = None,
    ) -> KTPDocument:
        document.ocr_status = ocr_status
        if ocr_json is not None:
            document.ocr_json = ocr_json
        if passenger_id is not None:
            document.passenger_id = passenger_id
        self.db.flush()
        return document

    def list_by_passenger(self, passenger_id: uuid.UUID) -> list[KTPDocument]:
        stmt = (
            select(KTPDocument)
            .where(KTPDocument.passenger_id == passenger_id)
            .order_by(KTPDocument.uploaded_at.desc())
        )
        return list(self.db.scalars(stmt))
