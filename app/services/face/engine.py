"""Pipeline wajah: RetinaFace ResNet50 (deteksi + 5 landmark) → alignment →
FaceNet InceptionResnetV1 vggface2 (embedding 512-d).

Desain abstraksi (agar portabel CPU → GPU → Axelera Metis):

    FaceEngine (ABC)
      ├─ detect_face(image)        -> primitive: bergantung backend (ONNX/Axelera)
      ├─ generate_embedding(crop)  -> primitive: bergantung backend
      ├─ align_face(image, lmk)    -> geometri murni (cv2/numpy), SAMA di semua backend
      └─ detect(image)             -> orkestrator: detect_face → align → embed → pose

    CPUFaceEngine   : RetinaFace via onnxruntime + FaceNet via facenet-pytorch
    MetisFaceEngine : (menyusul) model .onnx yang SAMA, inference via Axelera Runtime

Yang berpindah saat ganti backend HANYA dua primitive di atas. Orkestrasi,
alignment, estimasi pose, dan seluruh business logic di service TIDAK berubah —
itulah syarat "pindah ke Metis tanpa mengubah kode".

Catatan pose: RetinaFace hanya memberi 5 landmark (bukan pose 3D seperti dulu
dari InsightFace). Pose (pitch/yaw/roll) karena itu DIESTIMASI dari 5 landmark
via solvePnP, supaya validasi kualitas "wajah menoleh/menunduk" tetap berlaku.
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from app.core.config import settings
from app.core.exceptions import FaceEngineError

# Alignment ArcFace menghasilkan 112x112. FaceNet dilatih pada 160x160, jadi
# crop di-align pada kelipatan 112 lalu diskalakan — bukan di-crop ulang, supaya
# posisi mata/mulut tetap konsisten dengan template alignment.
ALIGNED_SIZE = 112
ALIGNED_SIZE_HQ = 224  # 112*2, sumber lebih tajam untuk diturunkan ke 160
FACENET_INPUT_SIZE = 160

# Template 5-titik ArcFace pada kanvas 112x112 (urutan: mata kiri, mata kanan,
# hidung, sudut mulut kiri, sudut mulut kanan). Alignment memetakan landmark
# wajah ke titik-titik baku ini. Nilai standar, sama seperti insightface.
_ARCFACE_TEMPLATE_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass
class DetectedFace:
    """Satu wajah: sudah di-align, sudah diekstrak embedding-nya.

    Bentuk ini SENGAJA dipertahankan persis seperti sebelumnya — quality check,
    penyimpanan crop, dan pemilihan wajah terbesar bergantung pada field-field
    ini. Mengubahnya akan merembet ke business logic.
    """

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


@dataclass
class RawDetection:
    """Keluaran mentah detektor sebelum alignment/embedding."""

    bbox: tuple[int, int, int, int]
    det_score: float
    landmarks: np.ndarray  # (5, 2) float32: mata kiri, mata kanan, hidung, mulut kiri, mulut kanan


# --------------------------------------------------------------------------- #
#  Estimasi pose dari 5 landmark (pengganti pose 3D InsightFace)               #
# --------------------------------------------------------------------------- #

# Model 3D generik untuk 5 landmark (satuan relatif). Cukup untuk MENAKSIR
# yaw/pitch/roll pada ambang validasi (~30°), bukan pengukuran presisi.
_POSE_MODEL_3D = np.array(
    [
        [-34.0, 32.0, 26.0],   # mata kiri
        [34.0, 32.0, 26.0],    # mata kanan
        [0.0, 0.0, 0.0],       # hidung
        [-26.0, -30.0, 22.0],  # sudut mulut kiri
        [26.0, -30.0, 22.0],   # sudut mulut kanan
    ],
    dtype=np.float64,
)


def estimate_pose(landmarks: np.ndarray, image_shape: tuple[int, ...]) -> tuple[float, float, float] | None:
    """Taksir (pitch, yaw, roll) derajat dari 5 landmark 2D via solvePnP.

    Mengembalikan None bila estimasi gagal — pemanggil (quality check) sudah
    menangani pose None dengan aman (melewati cek yaw/pitch).
    """
    try:
        h, w = image_shape[:2]
        focal = float(w)
        cam = np.array([[focal, 0, w / 2.0], [0, focal, h / 2.0], [0, 0, 1]], dtype=np.float64)
        pts2d = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
        # EPNP: robust untuk 5 titik tanpa tebakan awal. ITERATIVE melempar error
        # bila tak diberi initial guess pada konfigurasi titik tertentu.
        ok, rvec, _ = cv2.solvePnP(
            _POSE_MODEL_3D, pts2d, cam, np.zeros((4, 1)), flags=cv2.SOLVEPNP_EPNP
        )
        if not ok:
            return None
        rot, _ = cv2.Rodrigues(rvec)
        # RQDecomp3x3 mengembalikan sudut Euler (derajat): x=pitch, y=yaw, z=roll.
        angles = cv2.RQDecomp3x3(rot)[0]
        pitch, yaw, roll = float(angles[0]), float(angles[1]), float(angles[2])
        # Normalkan ke rentang (-90, 90] agar ambang derajat masuk akal.
        pitch = _wrap_angle(pitch)
        yaw = _wrap_angle(yaw)
        roll = _wrap_angle(roll)
        return (pitch, yaw, roll)
    except cv2.error:
        return None


def _wrap_angle(a: float) -> float:
    while a > 90.0:
        a -= 180.0
    while a < -90.0:
        a += 180.0
    return a


# --------------------------------------------------------------------------- #
#  FaceNet embedder (tidak berubah — embedding harus identik dengan sebelumnya)#
# --------------------------------------------------------------------------- #


class FaceNetEmbedder:
    """FaceNet InceptionResnetV1 (facenet-pytorch), keluaran 512-d.

    Model dimuat sekali (lazy, thread-safe). Embedding SENGAJA tidak diubah:
    embedding yang sudah tersimpan di pgvector harus tetap sebanding.
    """

    _lock = threading.Lock()

    def __init__(self) -> None:
        self._model: Any = None
        self._torch: Any = None
        self._device: Any = None

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


# --------------------------------------------------------------------------- #
#  Abstraksi FaceEngine                                                        #
# --------------------------------------------------------------------------- #


class FaceEngine(ABC):
    """Kontrak pipeline wajah, terlepas dari backend inference.

    Backend HANYA mengimplementasikan dua primitive: `detect_face` dan
    `generate_embedding`. `align_face` dan `detect` sudah disediakan di sini
    (geometri + orkestrasi yang identik di semua backend).
    """

    # --- primitive yang bergantung backend ---------------------------------

    @abstractmethod
    def detect_face(self, image: np.ndarray) -> list[RawDetection]:
        """Deteksi wajah: kembalikan bbox + skor + 5 landmark per wajah."""

    @abstractmethod
    def generate_embedding(self, aligned_bgr_160: np.ndarray) -> np.ndarray:
        """Crop wajah 160x160 BGR → embedding 512-d ternormalisasi."""

    # --- geometri: sama di semua backend (tidak bergantung framework) ------

    def align_face(self, image: np.ndarray, landmarks: np.ndarray, size: int = ALIGNED_SIZE) -> np.ndarray:
        """Align wajah ke template ArcFace 5-titik lalu crop ke `size`x`size`.

        Memakai similarity transform (rotasi+skala+translasi) dari 5 landmark ke
        template baku — persis peran norm_crop InsightFace, tapi tanpa InsightFace.
        """
        template = _ARCFACE_TEMPLATE_112 * (size / 112.0)
        src = np.asarray(landmarks, dtype=np.float32).reshape(5, 2)
        matrix = _similarity_transform(src, template)
        return cv2.warpAffine(image, matrix, (size, size), borderValue=0.0)

    # --- orkestrasi: pipeline utuh (dipakai service via engine.detect) -----

    def detect(self, image: np.ndarray) -> list[DetectedFace]:
        """Pipeline penuh: deteksi → align → embed → pose, per wajah.

        Inilah satu-satunya method yang dipanggil service. Bentuk keluarannya
        (list[DetectedFace]) TIDAK berubah dari implementasi lama.
        """
        results: list[DetectedFace] = []
        for raw in self.detect_face(image):
            aligned = self.align_face(image, raw.landmarks, ALIGNED_SIZE)

            # Untuk FaceNet: align pada resolusi lebih tinggi lalu turunkan ke
            # 160, bukan memperbesar crop 112 (yang menambah artefak).
            hq = self.align_face(image, raw.landmarks, ALIGNED_SIZE_HQ)
            facenet_input = cv2.resize(
                hq, (FACENET_INPUT_SIZE, FACENET_INPUT_SIZE), interpolation=cv2.INTER_AREA
            )
            embedding = self.generate_embedding(facenet_input)
            pose = estimate_pose(raw.landmarks, image.shape)

            results.append(
                DetectedFace(
                    bbox=raw.bbox,
                    det_score=raw.det_score,
                    landmarks=np.asarray(raw.landmarks, dtype=np.float32),
                    aligned=aligned,
                    embedding=embedding,
                    pose=pose,
                )
            )
        return results


def _similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Matriks affine 2x3 similarity (Umeyama) dari titik src → dst."""
    try:
        from skimage.transform import SimilarityTransform

        tform = SimilarityTransform()
        tform.estimate(src, dst)
        return tform.params[:2, :].astype(np.float32)
    except ImportError:
        # Fallback tanpa skimage: estimasi affine parsial dari OpenCV.
        matrix, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
        if matrix is None:
            raise FaceEngineError("Gagal menghitung alignment wajah dari landmark.")
        return matrix.astype(np.float32)


# --------------------------------------------------------------------------- #
#  RetinaFace ResNet50 (ONNX) — detektor untuk CPUFaceEngine                   #
# --------------------------------------------------------------------------- #

# Konfigurasi RetinaFace ResNet50 — SAMA PERSIS dengan operator resmi Axelera
# (retinaface.py: cfg dari model_info.extra_kwargs['RetinaFace']['cfg']). Nilai
# ini yang menentukan hasil decode; menyamakannya menjamin deteksi & landmark
# identik antara CPU (kode ini) dan Metis (Axelera runtime) pada model yang sama.
_RETINAFACE_CFG = {
    "min_sizes": [[16, 32], [64, 128], [256, 512]],
    "steps": [8, 16, 32],
    "variance": [0.1, 0.2],
    "clip": False,
}
_RF_MEAN = np.array([104.0, 117.0, 123.0], dtype=np.float32)  # BGR, mean RetinaFace baku

# Jumlah kolom per tensor keluaran ONNX (dipakai untuk mengenali loc/conf/landm),
# mengikuti DecodeRetinaface.exec_torch Axelera.
_NUM_LOC_COORDS = 4
_NUM_CONF_CLASSES = 2       # background + face
_NUM_LANDMARK_PAIRS = 5     # 5 landmark → 10 nilai


class RetinaFaceOnnxDetector:
    """RetinaFace ResNet50 dalam ONNX, dijalankan via onnxruntime.

    Model dimuat sekali (lazy, thread-safe). File .onnx yang sama nantinya
    dikompilasi untuk Axelera Metis — MetisFaceEngine cukup mengganti runtime,
    bukan modelnya.
    """

    _lock = threading.Lock()

    def __init__(self) -> None:
        self._session: Any = None
        self._input_name: str | None = None

    def _get_session(self) -> Any:
        if self._session is None:
            with self._lock:
                if self._session is None:
                    try:
                        import onnxruntime as ort
                    except ImportError as exc:  # pragma: no cover
                        raise FaceEngineError(
                            "onnxruntime belum terpasang. Jalankan: "
                            "pip install onnxruntime"
                        ) from exc

                    path = _ensure_model_file()
                    try:
                        # Pakai GPU bila tersedia (Laptop GPU), jika tidak CPU.
                        avail = ort.get_available_providers()
                        providers = [
                            p
                            for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                            if p in avail
                        ] or ["CPUExecutionProvider"]
                        session = ort.InferenceSession(path, providers=providers)
                    except Exception as exc:  # noqa: BLE001
                        raise FaceEngineError(
                            f"Gagal memuat RetinaFace ONNX ({path}): {exc}"
                        ) from exc
                    self._session = session
                    self._input_name = session.get_inputs()[0].name
        return self._session

    def detect(self, image: np.ndarray) -> list[RawDetection]:
        session = self._get_session()
        size = settings.retinaface_input_size

        # Preprocess LETTERBOX (jaga rasio aspek + padding), sesuai
        # ResizeMode.LETTERBOX_FIT di pipeline Axelera — bukan stretch ke persegi.
        canvas, ratio, pad_x, pad_y = _letterbox(image, size, _RF_MEAN)
        blob = ((canvas - _RF_MEAN).transpose(2, 0, 1))[None, ...]

        try:
            outputs = session.run(None, {self._input_name: blob})
        except Exception as exc:  # noqa: BLE001
            raise FaceEngineError(f"Inferensi RetinaFace gagal: {exc}") from exc

        # Kenali tensor by jumlah kolom (persis DecodeRetinaface.exec_torch).
        loc, conf, landm = _select_outputs(outputs)
        variances = _RETINAFACE_CFG["variance"]
        priors = generate_priors(_RETINAFACE_CFG, (size, size))

        boxes = decode_loc(loc[0], priors, variances)      # cx,cy,w,h ternormalisasi
        scores = conf[0, :, 1]                              # kolom 1 = wajah
        lands = decode_landm(landm[0], priors, variances)  # 10 nilai ternormalisasi

        # Filter by confidence (sama seperti _filter_samples Axelera).
        keep = scores > settings.retinaface_conf_threshold
        boxes, lands, scores = boxes[keep], lands[keep], scores[keep]
        if boxes.shape[0] == 0:
            return []

        # Normalized [0,1] → piksel pada kanvas letterbox (size x size).
        boxes[:, 0::2] *= size
        boxes[:, 1::2] *= size
        lands[:, 0::2] *= size
        lands[:, 1::2] *= size

        # Bentuk pusat (cx,cy,w,h) → sudut (x1,y1,x2,y2). Konversi ini di Axelera
        # dilakukan BBoxState; di sini eksplisit karena kita langsung memakainya.
        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2

        # Batalkan letterbox → koordinat gambar asli.
        xyxy[:, 0::2] = (xyxy[:, 0::2] - pad_x) / ratio
        xyxy[:, 1::2] = (xyxy[:, 1::2] - pad_y) / ratio
        lands[:, 0::2] = (lands[:, 0::2] - pad_x) / ratio
        lands[:, 1::2] = (lands[:, 1::2] - pad_y) / ratio

        order = _rf_nms(xyxy, scores, settings.retinaface_nms_threshold)

        detections: list[RawDetection] = []
        for i in order:
            x1, y1, x2, y2 = xyxy[i]
            detections.append(
                RawDetection(
                    bbox=(int(x1), int(y1), int(x2), int(y2)),
                    det_score=float(scores[i]),
                    landmarks=lands[i].reshape(5, 2).astype(np.float32),
                )
            )
        return detections


def _ensure_model_file() -> str:
    """Kembalikan path model ONNX; unduh bila belum ada dan URL tersedia."""
    path = settings.retinaface_model_path
    if os.path.isfile(path):
        return path

    url = settings.retinaface_model_url
    if url:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            import urllib.request

            urllib.request.urlretrieve(url, path)
        except Exception as exc:  # noqa: BLE001
            raise FaceEngineError(
                f"Gagal mengunduh RetinaFace ONNX dari {url}: {exc}"
            ) from exc
        _verify_md5(path, settings.retinaface_model_md5)
        return path

    raise FaceEngineError(
        f"Model RetinaFace ONNX tidak ditemukan di '{path}'. "
        "Sediakan file .onnx RetinaFace ResNet50 (mis. dari Axelera Model Zoo) "
        "di path tersebut, atau set RETINAFACE_MODEL_URL untuk unduh otomatis."
    )


def _verify_md5(path: str, expected: str) -> None:
    """Verifikasi integritas unduhan. Lewati bila md5 tidak dikonfigurasi."""
    if not expected:
        return
    import hashlib

    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected:
        os.remove(path)
        raise FaceEngineError(
            f"MD5 model RetinaFace tidak cocok (harap {expected}, dapat {actual}). "
            "File dihapus; unduh ulang."
        )


def _select_outputs(outputs: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Petakan 3 output ONNX ke (loc, conf, landm) berdasarkan jumlah kolomnya.

    Persis DecodeRetinaface.exec_torch Axelera: loc=4 kolom, conf=2, landm=10.
    Batch dim dipertahankan (diindeks [0] oleh pemanggil).
    """
    loc = conf = landm = None
    for out in outputs:
        arr = np.asarray(out, dtype=np.float32)
        last = arr.shape[-1]
        if last == _NUM_LOC_COORDS:
            loc = arr
        elif last == _NUM_CONF_CLASSES:
            conf = arr
        elif last == _NUM_LANDMARK_PAIRS * 2:
            landm = arr
    if loc is None or conf is None or landm is None:
        raise FaceEngineError(
            "Output RetinaFace ONNX tak dikenali (butuh tensor 4/2/10 kolom)."
        )
    return loc, conf, landm


def generate_priors(cfg: dict, image_size: tuple[int, int]) -> np.ndarray:
    """Priorbox RetinaFace. SALINAN PERSIS generate_priors() Axelera.

    image_size = (height, width). Keluaran anchor bentuk pusat ternormalisasi
    [cx, cy, s_kx, s_ky].
    """
    from itertools import product
    from math import ceil

    min_sizes = cfg["min_sizes"]
    steps = cfg["steps"]
    clip = cfg["clip"]
    feature_maps = [
        [ceil(image_size[0] / step), ceil(image_size[1] / step)] for step in steps
    ]

    anchors: list[list[float]] = []
    for k, f in enumerate(feature_maps):
        min_sizes_k = min_sizes[k]
        for i, j in product(range(f[0]), range(f[1])):
            for min_size in min_sizes_k:
                s_kx = min_size / image_size[1]
                s_ky = min_size / image_size[0]
                dense_cx = [x * steps[k] / image_size[1] for x in [j + 0.5]]
                dense_cy = [y * steps[k] / image_size[0] for y in [i + 0.5]]
                for cy, cx in product(dense_cy, dense_cx):
                    anchors.append([cx, cy, s_kx, s_ky])

    output = np.array(anchors, dtype=np.float32)
    if clip:
        output = np.clip(output, 0, 1)
    return output


def decode_loc(loc: np.ndarray, priors: np.ndarray, variances: list) -> np.ndarray:
    """Decode bounding box. SALINAN PERSIS decode_loc() Axelera.

    Keluaran bentuk pusat [cx, cy, w, h] ternormalisasi (BUKAN sudut) — konversi
    ke sudut dilakukan pemanggil, seperti BBoxState di pipeline Axelera.
    """
    if len(loc) == 0 or len(priors) == 0:
        return np.empty((0, _NUM_LOC_COORDS), dtype=np.float32)
    return np.concatenate(
        (
            priors[:, :2] + loc[:, :2] * variances[0] * priors[:, 2:],
            priors[:, 2:] * np.exp(loc[:, 2:] * variances[1]),
        ),
        axis=1,
    )


def decode_landm(pre: np.ndarray, priors: np.ndarray, variances: list) -> np.ndarray:
    """Decode 5 landmark. SALINAN PERSIS decode_landm() Axelera."""
    if len(pre) == 0 or len(priors) == 0:
        return np.empty((0, _NUM_LANDMARK_PAIRS * 2), dtype=np.float32)
    return np.concatenate(
        (
            priors[:, :2] + pre[:, :2] * variances[0] * priors[:, 2:],
            priors[:, :2] + pre[:, 2:4] * variances[0] * priors[:, 2:],
            priors[:, :2] + pre[:, 4:6] * variances[0] * priors[:, 2:],
            priors[:, :2] + pre[:, 6:8] * variances[0] * priors[:, 2:],
            priors[:, :2] + pre[:, 8:10] * variances[0] * priors[:, 2:],
        ),
        axis=1,
    )


def _letterbox(
    image: np.ndarray, size: int, pad_value: np.ndarray
) -> tuple[np.ndarray, float, int, int]:
    """Resize jaga rasio aspek + padding ke kanvas size x size (LETTERBOX_FIT).

    Kembalikan (kanvas float32, ratio, pad_x, pad_y) untuk membatalkan pemetaan
    saat mengembalikan koordinat ke gambar asli.
    """
    h, w = image.shape[:2]
    ratio = min(size / w, size / h)
    new_w, new_h = int(round(w * ratio)), int(round(h * ratio))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    canvas = np.zeros((size, size, 3), dtype=np.float32) + pad_value
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized.astype(np.float32)
    return canvas, ratio, pad_x, pad_y


def _rf_nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    """Non-max suppression standar. Kembalikan indeks yang dipertahankan."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= iou_thresh]
    return keep


# --------------------------------------------------------------------------- #
#  Implementasi backend                                                        #
# --------------------------------------------------------------------------- #


class CPUFaceEngine(FaceEngine):
    """FaceEngine untuk CPU/GPU laptop.

    - detect_face      : RetinaFace ResNet50 (ONNX via onnxruntime)
    - generate_embedding: FaceNet InceptionResnetV1 vggface2 (facenet-pytorch)

    Pemakaian GPU otomatis bila tersedia (onnxruntime CUDA untuk deteksi;
    FACENET_DEVICE=cuda untuk embedding).
    """

    def __init__(
        self,
        detector: RetinaFaceOnnxDetector | None = None,
        embedder: FaceNetEmbedder | None = None,
    ) -> None:
        self.detector = detector or RetinaFaceOnnxDetector()
        self.embedder = embedder or FaceNetEmbedder()

    def detect_face(self, image: np.ndarray) -> list[RawDetection]:
        return self.detector.detect(image)

    def generate_embedding(self, aligned_bgr_160: np.ndarray) -> np.ndarray:
        return self.embedder.embed(aligned_bgr_160)


# --------------------------------------------------------------------------- #
#  Factory: pilih backend lewat config (singleton per proses)                  #
# --------------------------------------------------------------------------- #

_engine_lock = threading.Lock()
_engine_instance: FaceEngine | None = None


def get_face_engine() -> FaceEngine:
    """Kembalikan FaceEngine sesuai settings.face_engine (dibuat sekali).

    Inilah satu-satunya titik yang menentukan backend. Pindah ke Metis =
    set FACE_ENGINE=metis; kode service tidak berubah sama sekali.
    """
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = _build_engine(settings.face_engine)
    return _engine_instance


def _build_engine(backend: str) -> FaceEngine:
    name = (backend or "cpu").strip().lower()
    if name in ("cpu", "gpu", "cuda"):
        return CPUFaceEngine()
    if name == "metis":
        # Placeholder yang jujur: MetisFaceEngine akan mengimplementasikan
        # detect_face/generate_embedding memakai Axelera Runtime atas model
        # .onnx yang SAMA. Sampai itu ada, jangan diam-diam fallback ke CPU.
        raise FaceEngineError(
            "FACE_ENGINE=metis belum diimplementasikan. Tambahkan MetisFaceEngine "
            "(subclass FaceEngine) lalu daftarkan di _build_engine()."
        )
    raise FaceEngineError(
        f"FACE_ENGINE tidak dikenali: '{backend}'. Pilihan: 'cpu' atau 'metis'."
    )
