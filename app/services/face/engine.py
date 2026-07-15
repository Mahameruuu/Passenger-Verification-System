"""Engine wajah: InsightFace (deteksi + alignment + pose) → FaceNet (embedding).

Pembagian tugas ini disengaja:

- **Deteksi & alignment** tetap InsightFace (SCRFD + landmark 3D). Detektornya
  akurat dan landmark 3D-nya adalah satu-satunya sumber `yaw`/`pitch` untuk
  quality check. MTCNN bawaan facenet-pytorch tidak menyediakan pose, sehingga
  memakainya akan menghilangkan penolakan wajah menoleh/menunduk.
- **Embedding** memakai FaceNet InceptionResnetV1 (512-d), sesuai target.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np

from app.core.config import settings
from app.core.exceptions import FaceEngineError

# Alignment ArcFace menghasilkan 112x112. FaceNet dilatih pada 160x160, jadi
# crop di-align pada kelipatan 112 lalu diskalakan — bukan di-crop ulang, supaya
# posisi mata/mulut tetap konsisten dengan template alignment.
ALIGNED_SIZE = 112
ALIGNED_SIZE_HQ = 224  # 112*2, sumber yang lebih tajam untuk diturunkan ke 160
FACENET_INPUT_SIZE = 160


@dataclass
class DetectedFace:
    """Satu wajah: sudah di-align, sudah diekstrak embedding-nya."""

    bbox: tuple[int, int, int, int]
    det_score: float
    landmarks: np.ndarray
    aligned: np.ndarray  # crop 112x112 BGR (untuk disimpan & quality check)
    embedding: np.ndarray  # 512-d FaceNet, L2-normalized
    pose: tuple[float, float, float] | None = None  # pitch, yaw, roll (derajat)

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]


class FaceEngine(Protocol):
    def detect(self, image: np.ndarray) -> list[DetectedFace]: ...


class FaceNetEmbedder:
    """FaceNet InceptionResnetV1 (facenet-pytorch), keluaran 512-d.

    Model dimuat sekali (lazy, thread-safe).
    """

    _lock = threading.Lock()

    def __init__(self) -> None:
        self._model: Any = None
        self._torch: Any = None

    def _get_model(self) -> tuple[Any, Any]:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        import torch
                        from facenet_pytorch import InceptionResnetV1
                    except ImportError as exc:  # pragma: no cover
                        raise FaceEngineError(
                            "FaceNet belum terpasang. Jalankan: "
                            "pip install torch facenet-pytorch"
                        ) from exc

                    try:
                        model = InceptionResnetV1(
                            pretrained=settings.facenet_pretrained
                        ).eval()
                        device = torch.device(settings.facenet_device)
                        model = model.to(device)
                    except Exception as exc:  # noqa: BLE001
                        raise FaceEngineError(
                            f"Gagal memuat FaceNet ({settings.facenet_pretrained}): {exc}"
                        ) from exc

                    self._torch = torch
                    self._device = device
                    self._model = model
        return self._model, self._torch

    def embed(self, aligned_bgr_160: np.ndarray) -> np.ndarray:
        """Crop wajah 160x160 BGR → embedding 512-d yang sudah L2-normalized."""
        model, torch = self._get_model()

        # FaceNet mengharapkan RGB dengan standardisasi (x - 127.5) / 128.
        rgb = cv2.cvtColor(aligned_bgr_160, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - 127.5) / 128.0
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(self._device)

        with torch.no_grad():
            output = model(tensor)

        embedding = output.detach().cpu().numpy().ravel().astype(np.float32)

        # FaceNet sudah mengeluarkan vektor ternormalisasi, tapi normalisasi
        # ulang tetap dilakukan: cosine similarity hanya sahih untuk vektor
        # ternormalisasi, dan diam-diam salah bila tidak.
        norm = float(np.linalg.norm(embedding))
        if norm == 0.0:
            raise FaceEngineError("FaceNet menghasilkan embedding nol.")
        return embedding / norm


class InsightFaceDetector:
    """Deteksi + alignment + pose. TIDAK dipakai untuk embedding."""

    _lock = threading.Lock()

    def __init__(self) -> None:
        self._app: Any = None

    def _get_app(self) -> Any:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    try:
                        from insightface.app import FaceAnalysis
                    except ImportError as exc:  # pragma: no cover
                        raise FaceEngineError(
                            "InsightFace belum terpasang. Jalankan: "
                            "pip install insightface onnxruntime"
                        ) from exc
                    try:
                        app = FaceAnalysis(
                            name=settings.face_detector_model,
                            providers=["CPUExecutionProvider"],
                            # Model pengenalan tidak dimuat: embedding-nya dari
                            # FaceNet. Ini menghemat ~170 MB memori.
                            allowed_modules=["detection", "landmark_3d_68"],
                        )
                        app.prepare(
                            ctx_id=0,
                            det_size=(settings.face_det_size, settings.face_det_size),
                        )
                    except Exception as exc:  # noqa: BLE001
                        raise FaceEngineError(
                            f"Gagal memuat detektor InsightFace: {exc}"
                        ) from exc
                    self._app = app
        return self._app

    def detect_raw(self, image: np.ndarray) -> list[Any]:
        app = self._get_app()
        try:
            return app.get(image)
        except Exception as exc:  # noqa: BLE001
            raise FaceEngineError(f"Deteksi wajah gagal: {exc}") from exc


class HybridFaceEngine:
    """Implementasi FaceEngine: InsightFace mendeteksi, FaceNet meng-embed."""

    def __init__(
        self,
        detector: InsightFaceDetector | None = None,
        embedder: FaceNetEmbedder | None = None,
    ) -> None:
        self.detector = detector or InsightFaceDetector()
        self.embedder = embedder or FaceNetEmbedder()

    def detect(self, image: np.ndarray) -> list[DetectedFace]:
        from insightface.utils.face_align import norm_crop

        results: list[DetectedFace] = []
        for face in self.detector.detect_raw(image):
            # Alignment: wajah diputar/diskalakan berdasarkan 5 landmark supaya
            # mata & mulut berada pada posisi baku. Tanpa ini, embedding wajah
            # miring tidak sebanding dengan wajah tegak.
            aligned = norm_crop(image, landmark=face.kps, image_size=ALIGNED_SIZE)

            # Untuk FaceNet: align pada resolusi lebih tinggi lalu turunkan ke
            # 160, bukan memperbesar crop 112 (yang menambah artefak).
            hq = norm_crop(image, landmark=face.kps, image_size=ALIGNED_SIZE_HQ)
            facenet_input = cv2.resize(
                hq, (FACENET_INPUT_SIZE, FACENET_INPUT_SIZE), interpolation=cv2.INTER_AREA
            )
            embedding = self.embedder.embed(facenet_input)

            x1, y1, x2, y2 = (int(v) for v in face.bbox)
            pose = getattr(face, "pose", None)

            results.append(
                DetectedFace(
                    bbox=(x1, y1, x2, y2),
                    det_score=float(face.det_score),
                    landmarks=np.asarray(face.kps, dtype=np.float32),
                    aligned=aligned,
                    embedding=embedding,
                    pose=tuple(float(v) for v in pose) if pose is not None else None,
                )
            )

        return results
