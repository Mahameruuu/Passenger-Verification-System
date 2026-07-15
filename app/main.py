import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.endpoints import health
from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cek koneksi sekali saat start supaya salah kredensial ketahuan langsung,
    # bukan baru terlihat saat request pertama. Gagal koneksi TIDAK menghentikan
    # aplikasi — /health yang bertugas melaporkannya.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            has_vector = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname='vector'")
            ).scalar()
        print(
            f"[startup] PostgreSQL terhubung: {settings.postgres_db} "
            f"@ {settings.postgres_host}:{settings.postgres_port}"
        )
        if not has_vector:
            # Tanpa pgvector, similarity search tidak bisa jalan sama sekali.
            print("[startup] PERINGATAN — extension `vector` TIDAK aktif di database.")
    except SQLAlchemyError as exc:
        print(f"[startup] PERINGATAN — PostgreSQL tidak terhubung: {exc}")

    # Panaskan model OCR di latar belakang supaya request OCR PERTAMA pun cepat
    # (biaya load model dibayar sekarang, bukan oleh pengguna). Dijalankan di
    # thread terpisah agar startup & /health tidak ikut terblokir.
    def _warmup_ocr() -> None:
        try:
            from app.services.ocr.engine import get_shared_engine

            get_shared_engine().warmup()
            print("[startup] Model OCR siap (warm-up selesai).")
        except Exception as exc:  # noqa: BLE001 — warm-up tidak boleh menjatuhkan app
            print(f"[startup] PERINGATAN — warm-up OCR gagal: {exc}")

    threading.Thread(target=_warmup_ocr, name="ocr-warmup", daemon=True).start()

    yield

    engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Sistem registrasi dan verifikasi identitas penumpang kapal "
        "menggunakan KTP dan Face Recognition.\n\n"
        "**Tahap saat ini:** pondasi backend. Baru tersedia health check."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(api_router, prefix=settings.api_v1_prefix)

# --------------------------------------------------------------------- UI
# Halaman demo statis (HTML/CSS/JS) untuk melihat hasil CV secara visual.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=WEB_DIR, html=True), name="ui")

# Folder /storage lokal SUDAH DIHAPUS: file kini di MinIO. UI menampilkan gambar
# lewat presigned URL yang dikembalikan API (image_url / face_url / selfie_url),
# jadi browser tidak perlu kredensial MinIO dan link yang bocor kedaluwarsa
# dengan sendirinya.


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")
