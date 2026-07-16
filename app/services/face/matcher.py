from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Candidate:
    """Satu embedding acuan yang sudah terdaftar."""

    passenger_id: str
    registration_id: str
    embedding: np.ndarray


@dataclass(frozen=True)
class MatchCandidate:
    passenger_id: str
    registration_id: str
    similarity: float


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity dua embedding, rentang -1..1.

    Embedding FaceNet sudah L2-normalized, tapi normalisasi ulang tetap
    dilakukan: vektor bisa saja berasal dari versi lain yang tidak menormalkan,
    dan dot product dari vektor tak-ternormalisasi BUKAN cosine similarity.
    """
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()

    if a.shape != b.shape:
        raise ValueError(
            f"Dimensi embedding tidak sama: {a.shape} vs {b.shape}. "
            "Kemungkinan model wajah berganti sejak registrasi."
        )

    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    similarity = float(np.dot(a, b) / (norm_a * norm_b))
    # Galat pembulatan float bisa menghasilkan 1.0000001.
    return float(np.clip(similarity, -1.0, 1.0))


def rank(probe: np.ndarray, candidates: list[Candidate]) -> list[MatchCandidate]:
    """Urutkan kandidat berdasarkan kemiripan dengan wajah probe, tertinggi dulu."""
    scored = [
        MatchCandidate(
            passenger_id=c.passenger_id,
            registration_id=c.registration_id,
            similarity=cosine_similarity(probe, c.embedding),
        )
        for c in candidates
    ]
    return sorted(scored, key=lambda m: m.similarity, reverse=True)
