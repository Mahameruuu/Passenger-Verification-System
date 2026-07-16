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
    FaceNotRegisteredError,
    InvalidImageError,
    NoFaceDetectedError,
    PassengerNotFoundError,
)
from app.models.gcp import EventType, Person, VerificationStatus
from app.repositories.detection_event import DetectionEventRepository
from app.repositories.embedding import EmbeddingRepository, SearchHit
from app.repositories.person import PersonRepository
from app.services.face.engine import DetectedFace, FaceEngine, get_face_engine
from app.services.storage import StorageService

SELFIE_PREFIX = "selfie"


@dataclass
class MatchResult:
    matched: bool
    similarity: float
    threshold: float
    person: Person | None
    faces_detected: int
    probe_det_score: float
    runner_up_similarity: float | None = None
    selfie_url: str | None = None


class MatchService:
    """Verifikasi live: selfie → embedding FaceNet → similarity search pgvector."""

    def __init__(
        self,
        db: Session,
        engine: FaceEngine | None = None,
        storage: StorageService | None = None,
    ) -> None:
        self.db = db
        # Backend dipilih via config (FACE_ENGINE) dan dimuat sekali per proses.
        self.engine = engine or get_face_engine()
        self.storage = storage or StorageService()
        self.embeddings = EmbeddingRepository(db)
        self.persons = PersonRepository(db)
        self.events = DetectionEventRepository(db)
        self.threshold = settings.face_match_threshold

    async def verify(self, person_id: uuid.UUID, file: UploadFile) -> MatchResult:
        """1:1 — cocokkan selfie dengan embedding milik SATU orang."""
        person = self.persons.get_by_id(person_id)
        if person is None:
            raise PassengerNotFoundError(f"Orang {person_id} tidak ditemukan.")

        if not self.embeddings.list_by_person(person_id):
            raise FaceNotRegisteredError(
                f"{person.full_name} belum punya wajah terdaftar. "
                "Jalankan registrasi wajah lebih dulu."
            )

        probe, faces_detected, selfie_key = await self._extract_probe(file)

        # pgvector tetap dipakai untuk 1:1 — hasilnya disaring ke orang ini saja.
        # Ini juga menjaga kesamaan definisi skor antara 1:1 dan 1:N.
        hits = self.embeddings.search(probe.embedding, top_k=settings.face_search_top_k)
        own = [h for h in hits if h.person_id == person_id]

        if own:
            similarity = own[0].similarity
        else:
            # Tidak masuk top-K global → jelas jauh. Ambil skor persisnya supaya
            # angka yang dilaporkan tetap benar, bukan sekadar "tidak cocok".
            similarity = self._exact_similarity(probe.embedding, person_id)

        matched = similarity >= self.threshold
        self._record(
            selfie_key, similarity, matched, person if matched else None, probe
        )

        return MatchResult(
            matched=matched,
            similarity=similarity,
            threshold=self.threshold,
            person=person,
            faces_detected=faces_detected,
            probe_det_score=probe.det_score,
            selfie_url=self.storage.presigned_url(selfie_key),
        )

    async def identify(self, file: UploadFile) -> MatchResult:
        """1:N — cari siapa pemilik wajah ini lewat similarity search pgvector."""
        probe, faces_detected, selfie_key = await self._extract_probe(file)

        hits: list[SearchHit] = self.embeddings.search(probe.embedding)
        if not hits:
            raise FaceNotRegisteredError(
                "Belum ada wajah terdaftar di sistem "
                f"(model {settings.face_model_version})."
            )

        best = hits[0]
        # Runner-up harus dari ORANG yang berbeda. Embedding kedua milik orang
        # yang sama bukan "kandidat pesaing" — melaporkannya sebagai runner-up
        # akan menyembunyikan keraguan sistem yang sesungguhnya.
        runner_up = next(
            (h.similarity for h in hits[1:] if h.person_id != best.person_id), None
        )

        matched = best.similarity >= self.threshold
        person = self.persons.get_by_id(best.person_id) if matched else None

        self._record(selfie_key, best.similarity, matched, person, probe)

        return MatchResult(
            matched=matched,
            similarity=best.similarity,
            threshold=self.threshold,
            person=person,
            faces_detected=faces_detected,
            probe_det_score=probe.det_score,
            runner_up_similarity=runner_up,
            selfie_url=self.storage.presigned_url(selfie_key),
        )

    def _exact_similarity(self, probe: np.ndarray, person_id: uuid.UUID) -> float:
        """Similarity terhadap embedding terbaik milik satu orang."""
        best = -1.0
        for row in self.embeddings.list_by_person(person_id):
            if row.model_version != settings.face_model_version:
                continue
            reference = np.asarray(row.vector, dtype=np.float32)
            denominator = np.linalg.norm(probe) * np.linalg.norm(reference)
            if denominator:
                best = max(best, float(np.dot(probe, reference) / denominator))
        return best

    def _record(
        self,
        selfie_key: str,
        similarity: float,
        matched: bool,
        person: Person | None,
        probe: DetectedFace,
    ) -> None:
        """Catat setiap percobaan verifikasi — termasuk yang gagal."""
        try:
            self.events.create(
                event_type=EventType.FACE_VERIFICATION,
                person_id=person.person_id if person else None,
                confidence_score=similarity,
                verification_status=(
                    VerificationStatus.MATCHED if matched
                    else VerificationStatus.NOT_MATCHED
                ),
                raw_image_key=selfie_key,
                ocr_result={
                    "det_score": round(probe.det_score, 4),
                    "model_version": settings.face_model_version,
                    "threshold": self.threshold,
                },
            )
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise

    async def _extract_probe(
        self, file: UploadFile
    ) -> tuple[DetectedFace, int, str]:
        data, content_type = await self.storage.read_upload(file)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise InvalidImageError("Selfie tidak bisa dibaca (file rusak?).")

        faces = self.engine.detect(image)
        if not faces:
            raise NoFaceDetectedError(
                "Tidak ada wajah terdeteksi pada selfie. Pastikan wajah terlihat "
                "jelas dan menghadap kamera."
            )

        # Berbeda dengan registrasi (yang menolak >1 wajah), di sini wajah
        # TERBESAR yang dipakai — itu yang paling dekat ke kamera. Orang yang
        # kebetulan lewat di latar belakang tidak boleh menggagalkan verifikasi.
        probe = max(faces, key=lambda f: f.width * f.height)

        extension = ".jpg" if content_type == "image/jpeg" else ".png"
        stored = self.storage.put_bytes(
            data, self.storage.build_key(SELFIE_PREFIX, extension), content_type
        )
        return probe, len(faces), stored.key
