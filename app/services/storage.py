"""Object storage MinIO — menggantikan folder lokal.

File KTP dan wajah tidak lagi ditulis ke disk. PostgreSQL hanya menyimpan
`object key`-nya (detection_event.raw_image_key).
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidFileTypeError,
    StorageError,
)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
CHUNK_SIZE = 64 * 1024

# Ekstensi & Content-Type dari client TIDAK dipercaya — isi file yang dibaca.
JPEG_SIGNATURE = b"\xff\xd8\xff"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class StoredObject:
    """File yang sudah tersimpan di MinIO. `key` inilah yang masuk ke database."""

    key: str
    size: int
    content_type: str


def _sniff_content_type(header: bytes) -> str:
    if header.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    if header.startswith(PNG_SIGNATURE):
        return "image/png"
    raise InvalidFileTypeError(
        "File harus berupa gambar JPG atau PNG. "
        "Isi file tidak dikenali sebagai JPEG maupun PNG."
    )


class StorageService:
    """Klien MinIO. Bucket dibuat sekali bila belum ada."""

    _lock = threading.Lock()
    _bucket_ready = False

    def __init__(self, bucket: str | None = None) -> None:
        self.bucket = bucket or settings.minio_bucket
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from minio import Minio
            except ImportError as exc:  # pragma: no cover
                raise StorageError(
                    "Paket `minio` belum terpasang. Jalankan: pip install minio"
                ) from exc

            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            self._ensure_bucket(self._client)
        return self._client

    def _ensure_bucket(self, client) -> None:
        if StorageService._bucket_ready:
            return
        with StorageService._lock:
            if StorageService._bucket_ready:
                return
            try:
                if not client.bucket_exists(self.bucket):
                    client.make_bucket(self.bucket)
                StorageService._bucket_ready = True
            except Exception as exc:  # noqa: BLE001 — minio melempar S3Error/urllib3
                raise StorageError(
                    f"Tidak bisa menghubungi MinIO di {settings.minio_endpoint}: {exc}"
                ) from exc

    # ------------------------------------------------------------------ tulis

    @staticmethod
    def build_key(prefix: str, extension: str = ".jpg") -> str:
        """Object key: <prefix>/<tahun>/<bulan>/<uuid><ext>.

        Nama file digenerate sendiri — nama dari client tidak pernah dipakai,
        karena bisa berisi "../" atau menimpa objek lain.
        """
        now = datetime.now(timezone.utc)
        return f"{prefix}/{now:%Y}/{now:%m}/{uuid4().hex}{extension}"

    def put_bytes(
        self, data: bytes, key: str, content_type: str = "image/jpeg"
    ) -> StoredObject:
        client = self._get_client()
        try:
            client.put_object(
                self.bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Gagal menyimpan objek ke MinIO: {exc}") from exc

        return StoredObject(key=key, size=len(data), content_type=content_type)

    async def read_upload(self, file: UploadFile) -> tuple[bytes, str]:
        """Validasi upload lalu kembalikan (bytes, content_type).

        Belum menyentuh MinIO: gambar perlu di-decode & diperiksa lebih dulu.
        """
        extension = Path(file.filename or "").suffix.lower()
        if extension and extension not in ALLOWED_EXTENSIONS:
            raise InvalidFileTypeError(
                f"Ekstensi '{extension}' tidak diizinkan. Gunakan JPG atau PNG."
            )

        await file.seek(0)
        buffer = bytearray()
        while chunk := await file.read(CHUNK_SIZE):
            buffer.extend(chunk)
            # Batas ditegakkan saat streaming, bukan dari Content-Length —
            # header itu berasal dari client dan bisa berbohong.
            if len(buffer) > MAX_FILE_SIZE:
                raise FileTooLargeError(
                    f"Ukuran file melebihi batas {MAX_FILE_SIZE // (1024 * 1024)} MB."
                )

        if not buffer:
            raise EmptyFileError("File kosong.")

        content_type = _sniff_content_type(bytes(buffer[: len(PNG_SIGNATURE)]))
        return bytes(buffer), content_type

    async def save_upload(self, file: UploadFile, prefix: str) -> StoredObject:
        """Validasi lalu unggah apa adanya ke MinIO."""
        data, content_type = await self.read_upload(file)
        extension = ".jpg" if content_type == "image/jpeg" else ".png"
        return self.put_bytes(data, self.build_key(prefix, extension), content_type)

    # ------------------------------------------------------------------- baca

    def presigned_url(self, key: str) -> str:
        """URL sementara untuk menampilkan objek di UI.

        Dipakai supaya browser tidak perlu kredensial MinIO, dan supaya link
        yang bocor kedaluwarsa dengan sendirinya.
        """
        client = self._get_client()
        try:
            return client.presigned_get_object(
                self.bucket,
                key,
                expires=timedelta(seconds=settings.minio_presign_expiry_seconds),
            )
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Gagal membuat presigned URL: {exc}") from exc

    def get_bytes(self, key: str) -> bytes:
        client = self._get_client()
        response = None
        try:
            response = client.get_object(self.bucket, key)
            return response.read()
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Gagal membaca objek dari MinIO: {exc}") from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def delete(self, key: str) -> None:
        """Hapus objek. Aman dipanggil untuk objek yang sudah tidak ada."""
        try:
            self._get_client().remove_object(self.bucket, key)
        except Exception:  # noqa: BLE001
            # Kegagalan pembersihan tidak boleh menutupi error aslinya.
            pass
