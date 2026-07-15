"""Model yang aktif dipakai: pemetaan skema PostgreSQL GCP.

Model lama (passengers, ktp_documents, face_registrations, boarding_logs,
audit_logs) sudah TIDAK dipakai — skema database sekarang dikelola di luar
aplikasi. File-filenya masih ada untuk referensi, tapi tidak diimpor di sini
supaya tidak ada yang tidak sengaja membuat tabel lama di database GCP.
"""

from app.models.enums import Gender, OCRStatus
from app.models.gcp import (
    Base,
    DetectionEvent,
    EmbeddingMetadata,
    EventType,
    IdCardType,
    Person,
    PersonStatus,
    VerificationStatus,
)

__all__ = [
    "Base",
    "DetectionEvent",
    "EmbeddingMetadata",
    "EventType",
    "Gender",
    "IdCardType",
    "OCRStatus",
    "Person",
    "PersonStatus",
    "VerificationStatus",
]
