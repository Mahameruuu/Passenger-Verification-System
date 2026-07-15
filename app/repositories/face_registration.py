import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FaceRegistrationStatus
from app.models.face_registration import FaceRegistration


class FaceRegistrationRepository:
    """Akses data tabel face_registrations. Tidak melakukan commit."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        passenger_id: uuid.UUID,
        face_image_path: str,
        embedding_path: str | None,
        quality_score: float | None,
        registration_status: FaceRegistrationStatus,
    ) -> FaceRegistration:
        registration = FaceRegistration(
            passenger_id=passenger_id,
            face_image_path=face_image_path,
            embedding_path=embedding_path,
            quality_score=(
                Decimal(f"{quality_score:.4f}") if quality_score is not None else None
            ),
            registration_status=registration_status,
        )
        self.db.add(registration)
        self.db.flush()
        return registration

    def get_active(self, passenger_id: uuid.UUID) -> FaceRegistration | None:
        stmt = select(FaceRegistration).where(
            FaceRegistration.passenger_id == passenger_id,
            FaceRegistration.registration_status == FaceRegistrationStatus.ACTIVE,
        )
        return self.db.scalar(stmt)

    def list_by_passenger(self, passenger_id: uuid.UUID) -> list[FaceRegistration]:
        stmt = (
            select(FaceRegistration)
            .where(FaceRegistration.passenger_id == passenger_id)
            .order_by(FaceRegistration.created_at.desc())
        )
        return list(self.db.scalars(stmt))

    def list_all_active(self) -> list[FaceRegistration]:
        """Semua wajah acuan yang aktif — dipakai untuk pencarian 1:N.

        Belum ada pgvector, jadi semua embedding dimuat ke memori dan
        dibandingkan satu per satu. Cukup untuk ribuan penumpang; di atas itu
        pgvector (indeks ANN) menjadi wajib.
        """
        stmt = select(FaceRegistration).where(
            FaceRegistration.registration_status == FaceRegistrationStatus.ACTIVE,
            FaceRegistration.embedding_path.is_not(None),
        )
        return list(self.db.scalars(stmt))

    def set_verification_score(
        self, registration: FaceRegistration, score: float
    ) -> None:
        registration.verification_score = Decimal(f"{score:.4f}")
        self.db.flush()

    def mark_replaced(self, registration: FaceRegistration) -> None:
        registration.registration_status = FaceRegistrationStatus.REPLACED
        # Wajib di-flush sebelum ACTIVE yang baru di-insert: ada partial unique
        # index yang hanya mengizinkan SATU baris ACTIVE per penumpang.
        self.db.flush()
