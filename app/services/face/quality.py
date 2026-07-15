from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.config import settings
from app.services.face.engine import DetectedFace


@dataclass
class QualityReport:
    """Hasil pemeriksaan kualitas wajah.

    `score` (0–1) disimpan ke face_registrations.quality_score; `passed`
    menentukan apakah wajah boleh dijadikan acuan verifikasi.
    """

    passed: bool
    score: float
    metrics: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)  # alasan penolakan

    def to_json(self) -> dict:
        return {
            "passed": self.passed,
            "score": round(self.score, 4),
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "reasons": self.reasons,
        }


def _normalize(value: float, low: float, high: float) -> float:
    """Petakan nilai ke 0–1 secara linier, dipotong di kedua ujung."""
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def assess(face: DetectedFace, image: np.ndarray) -> QualityReport:
    """Periksa apakah wajah layak dipakai sebagai acuan registrasi.

    Foto acuan yang buruk (buram, gelap, menoleh, terlalu kecil) akan merusak
    SEMUA verifikasi penumpang ini di kemudian hari — lebih baik ditolak
    sekarang dan penumpang diminta foto ulang.
    """
    gray = cv2.cvtColor(face.aligned, cv2.COLOR_BGR2GRAY)

    # Ketajaman: varians Laplacian. Foto buram menghasilkan varians rendah.
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    face_size = float(min(face.width, face.height))

    # Rasio wajah terhadap frame: wajah yang terlalu jauh dari kamera
    # kehilangan detail meski resolusi gambarnya besar.
    frame_h, frame_w = image.shape[:2]
    face_ratio = float(face_size / max(min(frame_h, frame_w), 1))

    yaw = abs(face.pose[1]) if face.pose is not None else 0.0
    pitch = abs(face.pose[0]) if face.pose is not None else 0.0

    metrics = {
        "det_score": face.det_score,
        "sharpness": sharpness,
        "brightness": brightness,
        "face_size_px": face_size,
        "face_ratio": face_ratio,
        "yaw": yaw,
        "pitch": pitch,
    }

    reasons: list[str] = []
    if face.det_score < settings.face_min_det_score:
        reasons.append(
            f"Keyakinan deteksi wajah rendah ({face.det_score:.2f} < "
            f"{settings.face_min_det_score})."
        )
    if face_size < settings.face_min_size_px:
        reasons.append(
            f"Wajah terlalu kecil ({face_size:.0f} px < "
            f"{settings.face_min_size_px} px). Dekatkan wajah ke kamera."
        )
    if sharpness < settings.face_min_sharpness:
        reasons.append(
            f"Foto terlalu buram (ketajaman {sharpness:.0f} < "
            f"{settings.face_min_sharpness}). Pastikan kamera fokus."
        )
    if brightness < settings.face_min_brightness:
        reasons.append(
            f"Foto terlalu gelap (kecerahan {brightness:.0f} < "
            f"{settings.face_min_brightness})."
        )
    if brightness > settings.face_max_brightness:
        reasons.append(
            f"Foto terlalu terang / silau (kecerahan {brightness:.0f} > "
            f"{settings.face_max_brightness})."
        )
    if face.pose is not None and yaw > settings.face_max_yaw:
        reasons.append(
            f"Wajah terlalu menoleh (yaw {yaw:.0f}° > {settings.face_max_yaw}°). "
            "Hadap lurus ke kamera."
        )
    if face.pose is not None and pitch > settings.face_max_pitch:
        reasons.append(
            f"Wajah terlalu menunduk/mendongak (pitch {pitch:.0f}° > "
            f"{settings.face_max_pitch}°)."
        )

    # Skor gabungan. Bobot mencerminkan seberapa besar tiap faktor merusak
    # kecocokan embedding: ketajaman & ukuran paling menentukan.
    score = (
        0.30 * _normalize(sharpness, settings.face_min_sharpness, 400.0)
        + 0.25 * _normalize(face_size, settings.face_min_size_px, 300.0)
        + 0.20 * _normalize(face.det_score, settings.face_min_det_score, 1.0)
        + 0.15 * (1.0 - _normalize(yaw, 0.0, 45.0))
        + 0.10 * (1.0 - abs(brightness - 128.0) / 128.0)
    )
    score = float(np.clip(score, 0.0, 1.0))

    passed = not reasons and score >= settings.face_min_quality_score
    if not reasons and not passed:
        reasons.append(
            f"Skor kualitas keseluruhan terlalu rendah ({score:.2f} < "
            f"{settings.face_min_quality_score})."
        )

    return QualityReport(passed=passed, score=score, metrics=metrics, reasons=reasons)
