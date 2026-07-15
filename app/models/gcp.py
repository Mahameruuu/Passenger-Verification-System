"""Pemetaan ke skema PostgreSQL GCP (database `cvasdp`).

PENTING: model-model ini hanya MEMETAKAN tabel yang sudah ada. Tidak ada
migration, tidak ada create_all — skema dikelola di luar aplikasi ini.
Kolom di sini harus persis mengikuti database; menambah kolom di sini tidak
akan membuatnya ada di sana.

Skema nyata (hasil introspeksi, PostgreSQL 16.14 + pgvector 0.8.5):

    person             person_id, full_name, id_card_type, citizen_id,
                       status, created_at, updated_at
                       UNIQUE (citizen_id)

    embedding_metadata embedding_id, person_id, vector(512), model_version,
                       created_at
                       INDEX hnsw (vector vector_cosine_ops)  -> operator <=>

    detection_event    event_id, camera_id, event_timestamp, person_id,
                       embedding_id, confidence_score, detection_status
                       (varchar 20), raw_image_key (text), created_at

                       CATATAN: tabel ini TIDAK punya kolom ocr_result,
                       gate_id, event_type, maupun verification_status. Jenis
                       event + status disatukan di `detection_status` sebagai
                       "JENIS:STATUS". Detail JSON (hasil OCR / kualitas wajah /
                       pencocokan) tidak bisa dipersistensikan — tak ada kolom.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import settings


class Base(DeclarativeBase):
    """Base terpisah dari skema lama. Tidak dipakai untuk membuat tabel."""


# --- nilai enum-like. Kolomnya VARCHAR di database, jadi tidak ada tipe ENUM ---


class PersonStatus:
    ACTIVE = "active"
    INACTIVE = "inactive"


class IdCardType:
    KTP = "KTP"


class EventType:
    """Paruh JENIS dari detection_event.detection_status ("JENIS:STATUS").

    Nilai harus pendek: detection_status VARCHAR(20) memuat "JENIS:STATUS", dan
    status terpanjang (NOT_MATCHED) + "VERIFY:" = 18 char. Jangan menambah nilai
    yang membuat total melewati 20.
    """

    KTP_REGISTRATION = "KTP"      # upload + OCR KTP
    FACE_REGISTRATION = "ENROLL"  # pendaftaran wajah
    FACE_VERIFICATION = "VERIFY"  # verifikasi live


class VerificationStatus:
    """Paruh STATUS dari detection_event.detection_status ("JENIS:STATUS")."""

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"            # OCR sebagian; person tidak dibuat
    FAILED = "FAILED"
    ACCEPTED = "ACCEPTED"          # wajah diterima sebagai acuan
    REJECTED = "REJECTED"          # kualitas rendah / wajah ganda
    MATCHED = "MATCHED"
    NOT_MATCHED = "NOT_MATCHED"


class Person(Base):
    """Identitas orang. Sumber kebenaran identitas (dulu: `passengers`).

    Catatan: tabel ini TIDAK punya kolom tanggal lahir, tempat lahir, jenis
    kelamin, maupun alamat. Field-field itu tetap diekstrak OCR, tapi hanya
    tersimpan sebagai JSON di detection_event.ocr_result — tidak bisa di-query
    sebagai kolom.
    """

    __tablename__ = "person"

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    id_card_type: Mapped[str | None] = mapped_column(String, nullable=True)
    citizen_id: Mapped[str | None] = mapped_column(String, nullable=True)  # NIK, UNIQUE
    status: Mapped[str] = mapped_column(String, nullable=False, default=PersonStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    embeddings: Mapped[list["EmbeddingMetadata"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Person {self.full_name} nik={self.citizen_id}>"


class EmbeddingMetadata(Base):
    """Embedding wajah 512-d di pgvector.

    Tabel ini tidak punya kolom status (ACTIVE/REPLACED) maupun quality_score.
    Konsekuensinya: SEMUA embedding milik seseorang menjadi kandidat pencarian,
    bukan hanya yang terbaru. Itu justru menguntungkan verifikasi (beberapa pose
    tersimpan), tapi berarti embedding buruk tidak bisa "dinonaktifkan" — karena
    itu quality check di sisi registrasi harus tetap ketat.
    """

    __tablename__ = "embedding_metadata"

    embedding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person.person_id", ondelete="CASCADE"),
        nullable=False,
    )
    vector: Mapped[list[float]] = mapped_column(
        Vector(settings.face_embedding_dim), nullable=False
    )
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    person: Mapped["Person"] = relationship(back_populates="embeddings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EmbeddingMetadata {self.embedding_id} person={self.person_id}>"


class DetectionEvent(Base):
    """Jejak setiap kejadian: upload KTP, registrasi wajah, verifikasi.

    Inilah satu-satunya tabel yang punya `raw_image_key` (object key MinIO) —
    di sinilah path file di MinIO dicatat.

    PENTING: kolom di sini HARUS persis mengikuti tabel `detection_event` di
    database. Tabel itu TIDAK punya `ocr_result` maupun `gate_id`, jadi keduanya
    tidak dipetakan — memetakannya akan membuat setiap INSERT/SELECT event gagal
    dengan "column does not exist".
    """

    __tablename__ = "detection_event"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    camera_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.person_id"), nullable=True
    )
    embedding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Kolom nyata di DB. Menyimpan gabungan "JENIS:STATUS" (mis. "KTP:PENDING",
    # "ENROLL:ACCEPTED", "VERIFY:NOT_MATCHED"). Tabel tidak lagi punya kolom
    # event_type/verification_status terpisah, jadi keduanya disatukan di sini.
    # Terpanjang: "VERIFY:NOT_MATCHED" = 18 char, muat di VARCHAR(20).
    detection_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_image_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    # --- event_type & verification_status: bukan kolom, tapi properti yang
    # membaca/menulis paruh dari detection_status. Ini menjaga seluruh kode
    # aplikasi (yang memakai kedua nama itu) tetap bekerja tanpa perubahan. ---

    @staticmethod
    def _compose(event_type: str | None, status: str | None) -> str | None:
        if not event_type and not status:
            return None
        return f"{event_type or ''}:{status or ''}"

    @property
    def event_type(self) -> str | None:
        if not self.detection_status:
            return None
        return self.detection_status.split(":", 1)[0] or None

    @event_type.setter
    def event_type(self, value: str | None) -> None:
        self.detection_status = self._compose(value, self.verification_status)

    @property
    def verification_status(self) -> str | None:
        if not self.detection_status or ":" not in self.detection_status:
            return None
        return self.detection_status.split(":", 1)[1] or None

    @verification_status.setter
    def verification_status(self, value: str | None) -> None:
        self.detection_status = self._compose(self.event_type, value)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DetectionEvent {self.event_type} {self.verification_status}>"
