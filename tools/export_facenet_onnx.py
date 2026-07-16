"""Export FaceNet InceptionResnetV1 (vggface2) ke ONNX untuk kompilasi Axelera Metis.

ADITIF: skrip ini TIDAK mengubah pipeline CPU. Ia hanya menghasilkan file .onnx
dari model FaceNet yang sama persis dengan yang dipakai runtime, lalu memverifikasi
bahwa ONNX-nya faithful terhadap PyTorch dan mencetak spec sheet preprocessing.

Serahkan hasilnya (file .onnx + spec sheet) ke tim Metis sebagai bahan tes compile
Voyager SDK. Ingat: ONNX BELUM bisa langsung jalan di Metis — masih perlu
dikompilasi (quantize + calibration) lebih dulu.

Jalankan dari root project:
    ./venv/Scripts/python.exe tools/export_facenet_onnx.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Konsol Windows (cp1252) tak bisa mencetak sebagian karakter — paksa UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Supaya `import app.core.config` bekerja saat skrip dijalankan dari root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402

INPUT_SIZE = 160
EMBED_DIM = 512
OPSET = 13
OUT_PATH = ROOT / "models" / "facenet_inceptionresnetv1_vggface2.onnx"


def main() -> None:
    try:
        import torch
        from facenet_pytorch import InceptionResnetV1
    except ImportError:
        raise SystemExit(
            "Butuh torch + facenet-pytorch. Jalankan: pip install torch facenet-pytorch"
        )

    print(f"[1/4] Memuat FaceNet InceptionResnetV1 (pretrained={settings.facenet_pretrained})...")
    model = InceptionResnetV1(pretrained=settings.facenet_pretrained).eval()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Input model = tensor yang SUDAH dinormalisasi (bukan gambar mentah). Nilai
    # acak cukup untuk membangun graf ONNX; angka aslinya tidak penting di sini.
    dummy = torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE)

    print(f"[2/4] Export ke ONNX (opset {OPSET}) -> {OUT_PATH.relative_to(ROOT)}...")
    torch.onnx.export(
        model,
        dummy,
        str(OUT_PATH),
        input_names=["input"],
        output_names=["embedding"],
        opset_version=OPSET,
        do_constant_folding=True,
        # Batch tetap 1, seperti model RetinaFace Axelera ([1,3,840,840]).
        dynamic_axes=None,
    )

    print("[3/4] Verifikasi ONNX vs PyTorch (harus ~identik)...")
    import onnxruntime as ort

    with torch.no_grad():
        torch_out = model(dummy).numpy().ravel()
    sess = ort.InferenceSession(str(OUT_PATH), providers=["CPUExecutionProvider"])
    onnx_out = sess.run(None, {"input": dummy.numpy()})[0].ravel()

    cos = float(
        np.dot(torch_out, onnx_out)
        / (np.linalg.norm(torch_out) * np.linalg.norm(onnx_out))
    )
    max_diff = float(np.max(np.abs(torch_out - onnx_out)))
    print(f"      cosine(torch, onnx) = {cos:.6f} | max|diff| = {max_diff:.2e}")
    if cos < 0.9999:
        print("      PERINGATAN: hasil ONNX menyimpang dari PyTorch — periksa opset/versi.")
    else:
        print("      OK: ONNX faithful terhadap PyTorch.")

    print("[4/4] SPEC SHEET (serahkan ke tim Metis) >>>")
    print(_spec_sheet())


def _spec_sheet() -> str:
    return f"""
================ FaceNet ONNX — Spesifikasi untuk Axelera Metis ================
File            : {OUT_PATH.relative_to(ROOT)}
Arsitektur      : InceptionResnetV1 (facenet-pytorch), pretrained={settings.facenet_pretrained}
model_version   : {settings.face_model_version}

INPUT tensor
  name          : input
  shape         : [1, 3, {INPUT_SIZE}, {INPUT_SIZE}]   (NCHW, batch tetap 1)
  dtype         : float32
  PREPROCESS (lakukan SEBELUM model — taruh di pipeline YAML Metis):
    1) crop wajah hasil alignment 160x160 (align_face jalan di host)
    2) BGR -> RGB
    3) normalisasi (x - 127.5) / 128.0        # BUKAN /255, tanpa mean/std ImageNet

OUTPUT tensor
  name          : embedding
  shape         : [1, {EMBED_DIM}]
  CATATAN       : output SUDAH L2-normalized (F.normalize ikut ter-export).
                  Bila operator normalize bermasalah saat compile Metis, export
                  ulang tanpa normalize lalu lakukan L2-normalize di host.

KONSISTENSI (WAJIB diperhatikan)
  - Embedding di pgvector saat ini dibuat FaceNet CPU float32.
  - Bila FaceNet ini dikuantisasi (INT8) di Metis, embedding akan bergeser:
      * ukur ulang threshold (sekarang {settings.face_match_threshold})
      * pakai model_version BERBEDA untuk embedding Metis (jangan dicampur)
      * register & verify harus memakai backend FaceNet yang sama
  - Alignment (similarity transform 5-landmark) & pose (solvePnP) TETAP di host.
===============================================================================
"""


if __name__ == "__main__":
    main()
