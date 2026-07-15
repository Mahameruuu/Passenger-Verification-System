import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.gcp import EmbeddingMetadata, Person


@dataclass(frozen=True)
class SearchHit:
    person_id: uuid.UUID
    embedding_id: uuid.UUID
    full_name: str
    citizen_id: str | None
    similarity: float  # 1 - cosine_distance, rentang -1..1


class EmbeddingRepository:
    """Akses tabel `embedding_metadata` + similarity search pgvector."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        person_id: uuid.UUID,
        vector: np.ndarray,
        model_version: str | None = None,
    ) -> EmbeddingMetadata:
        embedding = EmbeddingMetadata(
            person_id=person_id,
            vector=np.asarray(vector, dtype=np.float32).ravel().tolist(),
            model_version=model_version or settings.face_model_version,
        )
        self.db.add(embedding)
        self.db.flush()
        return embedding

    def count(self) -> int:
        return self.db.scalar(
            select(EmbeddingMetadata.embedding_id).limit(1)
        ) is not None

    def search(
        self,
        probe: np.ndarray,
        *,
        top_k: int | None = None,
        exclude_person_id: uuid.UUID | None = None,
    ) -> list[SearchHit]:
        """Cari embedding termirip lewat index HNSW pgvector.

        Operator `<=>` (cosine distance) dipakai karena index di database dibuat
        dengan `vector_cosine_ops`. Memakai operator lain (`<->`, `<#>`) membuat
        index diabaikan DAN mengubah arti skornya.

        Hanya embedding dengan `model_version` yang sama yang dibandingkan:
        embedding dari model berbeda tidak sebanding, dan mencampurnya
        menghasilkan skor yang tampak wajar tapi salah.
        """
        query_vector = np.asarray(probe, dtype=np.float32).ravel().tolist()
        distance = EmbeddingMetadata.vector.cosine_distance(query_vector)

        stmt = (
            select(
                EmbeddingMetadata.person_id,
                EmbeddingMetadata.embedding_id,
                Person.full_name,
                Person.citizen_id,
                distance.label("distance"),
            )
            .join(Person, Person.person_id == EmbeddingMetadata.person_id)
            .where(EmbeddingMetadata.model_version == settings.face_model_version)
            .order_by(distance)
            .limit(top_k or settings.face_search_top_k)
        )
        if exclude_person_id is not None:
            stmt = stmt.where(EmbeddingMetadata.person_id != exclude_person_id)

        from app.core.security import decrypt_nik
        return [
            SearchHit(
                person_id=row.person_id,
                embedding_id=row.embedding_id,
                full_name=row.full_name,
                citizen_id=decrypt_nik(row.citizen_id),
                similarity=1.0 - float(row.distance),
            )
            for row in self.db.execute(stmt)
        ]

    def list_by_person(self, person_id: uuid.UUID) -> list[EmbeddingMetadata]:
        stmt = (
            select(EmbeddingMetadata)
            .where(EmbeddingMetadata.person_id == person_id)
            .order_by(EmbeddingMetadata.created_at.desc())
        )
        return list(self.db.scalars(stmt))
