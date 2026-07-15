"""Live Camera → registrasi wajah.

Membuka webcam, menampilkan preview dengan kotak wajah, dan mengirim frame yang
dipilih ke POST /api/v1/faces/register.

Akses kamera SENGAJA tidak diletakkan di dalam server API: server bisa berjalan
di mesin lain, dan `cv2.VideoCapture` di sisi server hanya bisa melihat kamera
milik server itu sendiri. Kamera adalah urusan client.

Kamera dipilih dari .env (CAMERA_INDEX, CAMERA_WIDTH, CAMERA_HEIGHT).
--camera hanya untuk menimpanya sesaat tanpa menyentuh .env.

Pemakaian:
    python tools/capture_face.py --passenger-id <uuid>
    python tools/capture_face.py --passenger-id <uuid> --camera 1
    python tools/capture_face.py --list-cameras

Tombol:
    SPASI / ENTER  kirim frame saat ini
    Q / ESC        keluar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402

DEFAULT_API = "http://127.0.0.1:8000"


def list_cameras(limit: int = 6) -> int:
    """Cari index kamera yang bisa dibuka — untuk mengisi CAMERA_INDEX di .env."""
    print("Memindai perangkat kamera...")
    found = 0
    for index in range(limit):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok:
                h, w = frame.shape[:2]
                print(f"  index {index}: TERSEDIA ({w}x{h})")
                found += 1
        cap.release()
    if not found:
        print("  tidak ada kamera terdeteksi.")
        return 1
    print(f"\nSetel CAMERA_INDEX=<index> di .env untuk memilih salah satunya.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Registrasi wajah dari webcam")
    parser.add_argument("--passenger-id", help="UUID penumpang")
    parser.add_argument("--api", default=DEFAULT_API, help=f"Base URL (default {DEFAULT_API})")
    parser.add_argument(
        "--camera", type=int, default=None,
        help="Index kamera; menimpa CAMERA_INDEX dari .env",
    )
    parser.add_argument(
        "--list-cameras", action="store_true", help="Tampilkan kamera yang tersedia"
    )
    args = parser.parse_args()

    if args.list_cameras:
        return list_cameras()

    if not args.passenger_id:
        parser.error("--passenger-id wajib diisi (atau pakai --list-cameras)")

    camera_index = args.camera if args.camera is not None else settings.camera_index

    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(
            f"Kamera index {camera_index} tidak bisa dibuka. "
            "Jalankan `python tools/capture_face.py --list-cameras` untuk melihat "
            "index yang tersedia.",
            file=sys.stderr,
        )
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.camera_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.camera_height)
    print(f"Kamera index {camera_index} aktif.")

    # Deteksi ringan hanya untuk memandu pengguna di preview. Deteksi yang
    # sesungguhnya (InsightFace) tetap dilakukan di server.
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    print("SPASI/ENTER = kirim frame   |   Q/ESC = keluar")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Gagal membaca frame dari kamera.", file=sys.stderr)
                return 1

            preview = frame.copy()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for x, y, w, h in cascade.detectMultiScale(gray, 1.2, 5):
                cv2.rectangle(preview, (x, y), (x + w, y + h), (0, 255, 0), 2)

            cv2.putText(
                preview, "SPASI = daftar wajah, Q = keluar", (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
            )
            cv2.imshow("PIVS - Registrasi Wajah", preview)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return 0
            if key not in (32, 13):
                continue

            ok, buffer = cv2.imencode(".jpg", frame)
            if not ok:
                print("Gagal meng-encode frame.", file=sys.stderr)
                continue

            print("Mengirim frame...")
            try:
                response = requests.post(
                    f"{args.api}/api/v1/faces/register",
                    data={"passenger_id": args.passenger_id},
                    files={"file": ("capture.jpg", buffer.tobytes(), "image/jpeg")},
                    timeout=120,
                )
            except requests.RequestException as exc:
                print(f"Gagal menghubungi API: {exc}", file=sys.stderr)
                continue

            if response.status_code == 201:
                body = response.json()
                status = body["registration_status"]
                quality = body["quality"]
                print(f"  status  : {status}")
                print(f"  kualitas: {quality['score']:.3f}")
                for reason in quality["reasons"]:
                    print(f"  ! {reason}")
                if status == "ACTIVE":
                    print(f"  embedding: {body['registration']['embedding_path']}")
                    print("Wajah berhasil didaftarkan.")
                    return 0
                print("Wajah ditolak — perbaiki dan coba lagi.")
            else:
                print(f"  HTTP {response.status_code}: {response.text}")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
