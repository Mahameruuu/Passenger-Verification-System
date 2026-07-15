from __future__ import annotations

import uuid
from dataclasses import dataclass

import cv2
import numpy as np
from fastapi import UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    DuplicateFaceError,
    InvalidImageError,
    MultipleFacesError,
    NoFaceDetectedError,
    PassengerNotFoundError,
)
from app.models.gcp import (
    DetectionEvent,
    EmbeddingMetadata,
    EventType,
    Person,
    VerificationStatus,
)
from app.repositories.detection_event import DetectionEventRepository
from app.repositories.embedding import EmbeddingRepository
from app.repositories.person import PersonRepository
from app.services.face.engine import FaceEngine, HybridFaceEngine
from app.services.face.quality import QualityReport, assess
from app.services.storage import StorageService

FACE_PREFIX = "faces"


def _mask_nik(nik: str | None) -> str:
    """3578150504030007 → 3578********0007. Jangan bocorkan NIK penuh di pesan error."""
    if not nik:
        return "-"
    return nik[:4] + "*" * max(len(nik) - 8, 0) + nik[-4:] if len(nik) >= 8 else nik


@dataclass
class FaceRegistrationResult:
    person: Person
    embedding: EmbeddingMetadata | None
    event: DetectionEvent
    quality: QualityReport
    accepted: bool
    face_url: str | None


class FaceService:
    """Registrasi wajah: deteksi → alignment → quality → crop ke MinIO →
    embedding FaceNet → simpan ke `embedding_metadata` (pgvector).
    """

    def __init__(
        self,
        db: Session,
        engine: FaceEngine | None = None,
        storage: StorageService | None = None,
    ) -> None:
        self.db = db
        self.engine = engine or HybridFaceEngine()
        self.storage = storage or StorageService()
        self.persons = PersonRepository(db)
        self.embeddings = EmbeddingRepository(db)
        self.events = DetectionEventRepository(db)

    async def register(
        self, person_id: uuid.UUID, file: UploadFile
    ) -> FaceRegistrationResult:
        person = self.persons.get_by_id(person_id)
        if person is None:
            raise PassengerNotFoundError(f"Orang {person_id} tidak ditemukan.")

        data, _ = await self.storage.read_upload(file)
        image = self._decode(data)

        faces = self.engine.detect(image)
        if not faces:
            raise NoFaceDetectedError(
                "Tidak ada wajah terdeteksi pada gambar. Pastikan wajah terlihat "
                "jelas dan menghadap kamera."
            )
        if len(faces) > 1:
            # Acuan registrasi harus tunggal. Kalau ada 2 wajah, kita tidak tahu
            # mana miliknya — mendaftarkan wajah yang salah berarti orang lain
            # bisa lolos verifikasi sebagai orang ini.
            raise MultipleFacesError(
                f"Terdeteksi {len(faces)} wajah. Foto registrasi harus berisi "
                "tepat satu wajah."
            )

        face = faces[0]
        quality = assess(face, image)

        # Crop wajah disimpan untuk kedua kasus (lolos maupun ditolak) — foto
        # yang ditolak tetap perlu bisa diperiksa petugas.
        ok, encoded = cv2.imencode(".jpg", face.aligned)
        if not ok:
            raise InvalidImageError("Gagal meng-encode crop wajah.")
        crop = self.storage.put_bytes(
            encoded.tobytes(), self.storage.build_key(FACE_PREFIX, ".jpg")
        )

        if not quality.passed:
            event = self._record(
                person_id=person.person_id,
                raw_image_key=crop.key,
                confidence=quality.score,
                status=VerificationStatus.REJECTED,
                payload=quality.to_json(),
            )
            return FaceRegistrationResult(
                person=person, embedding=None, event=event, quality=quality,
                accepted=False, face_url=self.storage.presigned_url(crop.key),
            )

        # Wajah ini sudah jadi milik orang LAIN? Dicek lewat pgvector, sebelum
        # embedding disimpan. Tanpa ini, siapa pun bisa mendaftarkan wajahnya ke
        # KTP orang lain lalu lolos verifikasi sebagai orang itu.
        self._reject_if_duplicate(face.embedding, person, crop.key, quality)

        try:
            embedding = self.embeddings.create(
                person_id=person.person_id,
                vector=face.embedding,
                model_version=settings.face_model_version,
            )
            event = self.events.create(
                event_type=EventType.FACE_REGISTRATION,
                person_id=person.person_id,
                embedding_id=embedding.embedding_id,
                confidence_score=quality.score,
                verification_status=VerificationStatus.ACCEPTED,
                raw_image_key=crop.key,
                ocr_result=quality.to_json(),
            )
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            self.storage.delete(crop.key)
            raise

        self.db.refresh(embedding)
        self.db.refresh(event)
        self.db.refresh(person)
        return FaceRegistrationResult(
            person=person, embedding=embedding, event=event, quality=quality,
            accepted=True, face_url=self.storage.presigned_url(crop.key),
        )

    def _reject_if_duplicate(
        self,
        embedding: np.ndarray,
        person: Person,
        crop_key: str,
        quality: QualityReport,
    ) -> None:
        hits = self.embeddings.search(
            embedding, top_k=1, exclude_person_id=person.person_id
        )
        if not hits or hits[0].similarity < settings.face_duplicate_threshold:
            return

        hit = hits[0]
        # Percobaannya tetap dicatat — percobaan identitas ganda justru yang
        # paling perlu ditelusuri.
        self._record(
            person_id=person.person_id,
            raw_image_key=crop_key,
            confidence=hit.similarity,
            status=VerificationStatus.REJECTED,
            payload={**quality.to_json(), "duplicate_of": str(hit.person_id)},
        )

        raise DuplicateFaceError(
            f"Wajah ini sudah terdaftar sebagai {hit.full_name} "
            f"(NIK {_mask_nik(hit.citizen_id)}) dengan kemiripan "
            f"{hit.similarity:.2f}. Satu orang tidak boleh terdaftar sebagai dua "
            f"identitas. Bila ini memang orang berbeda, hubungi petugas untuk "
            f"pemeriksaan manual.",
            passenger_id=str(hit.person_id),
            full_name=hit.full_name,
            nik=hit.citizen_id or "-",
            similarity=hit.similarity,
        )

    def _record(
        self,
        *,
        person_id: uuid.UUID,
        raw_image_key: str,
        confidence: float,
        status: str,
        payload: dict,
    ) -> DetectionEvent:
        try:
            event = self.events.create(
                event_type=EventType.FACE_REGISTRATION,
                person_id=person_id,
                confidence_score=confidence,
                verification_status=status,
                raw_image_key=raw_image_key,
                ocr_result=payload,
            )
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            self.storage.delete(raw_image_key)
            raise
        self.db.refresh(event)
        return event

    def list_for_person(self, person_id: uuid.UUID) -> list[EmbeddingMetadata]:
        if not self.persons.exists(person_id):
            raise PassengerNotFoundError(f"Orang {person_id} tidak ditemukan.")
        return self.embeddings.list_by_person(person_id)

    @staticmethod
    def _decode(data: bytes) -> np.ndarray:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise InvalidImageError("Gambar tidak bisa dibaca (file rusak?).")
        return image
