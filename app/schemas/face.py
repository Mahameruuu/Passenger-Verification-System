import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ocr import PersonResponse


class EmbeddingResponse(BaseModel):
    """Satu baris `embedding_metadata`. Vektornya sendiri tidak dikirim."""

    model_config = ConfigDict(from_attributes=True)

    embedding_id: uuid.UUID
    person_id: uuid.UUID
    model_version: str | None = Field(
        default=None,
        description=(
            "Embedding dari model berbeda TIDAK sebanding. Pencarian hanya "
            "membandingkan embedding dengan model_version yang sama."
        ),
    )
    created_at: datetime


class QualityReportResponse(BaseModel):
    passed: bool
    score: float = Field(description="Skor kualitas gabungan (0–1)")
    metrics: dict[str, float]
    reasons: list[str] = Field(default_factory=list)


class FaceRegistrationResult(BaseModel):
    registration_status: str = Field(
        description=(
            "ACCEPTED — embedding tersimpan ke pgvector. "
            "REJECTED — kualitas di bawah ambang; embedding TIDAK dibuat."
        )
    )
    quality: QualityReportResponse
    face_url: str | None = Field(
        default=None, description="Presigned URL crop wajah 112x112 di MinIO."
    )
    embedding: EmbeddingResponse | None = None
    person: PersonResponse


class FaceMatchResponse(BaseModel):
    matched: bool
    similarity: float = Field(description="Cosine similarity (pgvector), rentang -1..1.")
    threshold: float
    faces_detected: int
    probe_det_score: float
    runner_up_similarity: float | None = Field(
        default=None,
        description=(
            "Hanya untuk 1:N. Skor kandidat terbaik dari ORANG LAIN. Bila rapat "
            "dengan `similarity`, sistem sedang ragu — layak diperiksa manusia."
        ),
    )
    person: PersonResponse | None = None
    selfie_url: str | None = None
