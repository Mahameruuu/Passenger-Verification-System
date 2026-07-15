from __future__ import annotations

import uuid
from dataclasses import dataclass

import cv2
import numpy as np
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    InvalidImageError,
    KTPDocumentNotFoundError,
    OCREngineError,
)
from app.models.gcp import DetectionEvent, EventType, Person, VerificationStatus
from app.repositories.detection_event import DetectionEventRepository
from app.repositories.person import PersonRepository
from app.services.ocr.engine import OCREngine, OCRLine, get_shared_engine
from app.services.ocr.ktp_parser import KTPFields, parse_ktp
from app.services.storage import StorageService


@dataclass
class OCRResult:
    event: DetectionEvent
    person: Person | None
    fields: KTPFields
    person_created: bool
    status: str  # VerificationStatus


class OCRService:
    """Baca KTP dari MinIO → ekstrak field → simpan ke `person` →
    perbarui `detection_event` (ocr_result, verification_status, person_id).
    """

    def __init__(
        self,
        db: Session,
        engine: OCREngine | None = None,
        storage: StorageService | None = None,
    ) -> None:
        self.db = db
        # Default ke engine bersama (singleton): model dimuat sekali untuk
        # seluruh proses, bukan tiap request. Lihat get_shared_engine().
        self.engine = engine or get_shared_engine()
        self.storage = storage or StorageService()
        self.events = DetectionEventRepository(db)
        self.persons = PersonRepository(db)

    def process(self, event_id: uuid.UUID) -> OCRResult:
        """Jalankan OCR SAJA — tidak membuat/menyimpan person.

        Ini sengaja read-only terhadap tabel `person`. Alur barunya: OCR dulu →
        hasilnya ditampilkan sebagai form yang bisa dikoreksi petugas → person
        baru dibuat saat petugas menekan Simpan (lihat `confirm`). Dengan begitu
        data hasil OCR yang keliru tidak langsung masuk sebagai identitas.

        Status yang disimpan tetap PENDING (menunggu konfirmasi), atau FAILED
        bila tidak ada teks yang terbaca sama sekali.
        """
        event = self.events.get_by_id(event_id)
        if event is None or event.event_type != EventType.KTP_REGISTRATION:
            raise KTPDocumentNotFoundError(f"Dokumen KTP {event_id} tidak ditemukan.")
        if not event.raw_image_key:
            raise KTPDocumentNotFoundError(f"Dokumen {event_id} tidak punya gambar.")

        lines = self._read_image(event)
        fields = parse_ktp(lines)

        # Hasil OCR mentah disimpan ke ocr_result (jsonb) sebagai jejak. NIK
        # dienkripsi sebelum disimpan. Person BELUM dibuat di sini.
        payload = self._encrypt_payload_nik(fields.to_json(), fields.nik)

        status = VerificationStatus.PENDING if lines else VerificationStatus.FAILED
        self._commit(event, status=status, ocr_result=payload,
                     confidence=fields.confidence)
        return OCRResult(
            event=event, person=None, fields=fields,
            person_created=False, status=status,
        )

    def confirm(self, event_id: uuid.UUID, fields: KTPFields) -> OCRResult:
        """Simpan identitas ke DB dari data yang sudah dikonfirmasi petugas.

        `fields` berisi nilai final (hasil OCR yang mungkin sudah dikoreksi di
        form). NIK & Nama wajib ada. Membuat person baru bila NIK belum ada;
        bila sudah ada, dokumen ditautkan ke person itu tanpa menimpa identitas.
        """
        event = self.events.get_by_id(event_id)
        if event is None or event.event_type != EventType.KTP_REGISTRATION:
            raise KTPDocumentNotFoundError(f"Dokumen KTP {event_id} tidak ditemukan.")
        if not fields.nik or not fields.full_name:
            raise InvalidImageError("NIK dan Nama wajib diisi sebelum menyimpan.")

        payload = self._encrypt_payload_nik(fields.to_json(), fields.nik)

        try:
            person = self.persons.get_by_citizen_id(fields.nik)
            created = person is None

            if person is None:
                person = self.persons.create(
                    full_name=fields.full_name, citizen_id=fields.nik
                )
            else:
                # NIK sudah terdaftar (UNIQUE). Identitas TIDAK ditimpa: nilai
                # yang ada mungkin sudah dikoreksi manual sebelumnya.
                fields.warnings.append(
                    f"NIK {fields.nik} sudah terdaftar sebagai {person.full_name}. "
                    "Dokumen ditautkan ke orang yang ada; identitas tidak ditimpa."
                )
                payload = self._encrypt_payload_nik(fields.to_json(), fields.nik)

            self.events.update(
                event,
                person_id=person.person_id,
                ocr_result=payload,
                verification_status=VerificationStatus.SUCCESS,
                confidence_score=fields.confidence,
            )
            self.db.commit()
        except IntegrityError:
            # Balapan pada UNIQUE(citizen_id) — orang lain menyisipkan NIK yang
            # sama lebih dulu. Ambil yang sudah ada, jangan gagalkan penyimpanan.
            self.db.rollback()
            person = self.persons.get_by_citizen_id(fields.nik)
            if person is None:
                raise
            created = False
            self.events.update(
                event,
                person_id=person.person_id,
                ocr_result=payload,
                verification_status=VerificationStatus.SUCCESS,
                confidence_score=fields.confidence,
            )
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise

        self.db.refresh(event)
        self.db.refresh(person)
        return OCRResult(
            event=event, person=person, fields=fields,
            person_created=created, status=VerificationStatus.SUCCESS,
        )

    @staticmethod
    def _encrypt_payload_nik(payload: dict, nik: str | None) -> dict:
        """Enkripsi NIK di dalam payload ocr_result sebelum disimpan ke DB."""
        if nik:
            from app.core.security import encrypt_nik
            payload["parsed"]["nik"] = encrypt_nik(nik)
            if "nik" in payload.get("fields_found", {}):
                payload["fields_found"]["nik"] = encrypt_nik(payload["fields_found"]["nik"])
        return payload

    def _read_image(self, event: DetectionEvent) -> list[OCRLine]:
        try:
            data = self.storage.get_bytes(event.raw_image_key)
        except Exception:
            self._commit(event, status=VerificationStatus.FAILED)
            raise

        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            self._commit(event, status=VerificationStatus.FAILED)
            raise InvalidImageError("Gambar KTP tidak bisa dibaca (file rusak?).")

        try:
            return self.engine.read_image(image)
        except OCREngineError:
            # Kegagalan engine tetap dicatat, kalau tidak event akan menggantung
            # di PENDING selamanya.
            self._commit(event, status=VerificationStatus.FAILED)
            raise

    def _commit(
        self,
        event: DetectionEvent,
        *,
        status: str,
        ocr_result: dict | None = None,
        confidence: float | None = None,
    ) -> None:
        self.events.update(
            event,
            verification_status=status,
            **({"ocr_result": ocr_result} if ocr_result is not None else {}),
            **({"confidence_score": confidence} if confidence is not None else {}),
        )
        self.db.commit()
        self.db.refresh(event)
