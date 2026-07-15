from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Skema database dikelola DI LUAR aplikasi ini (PostgreSQL GCP).
# Tidak ada create_all / Alembic yang dijalankan terhadap engine ini.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # koneksi mati dideteksi sebelum dipakai
    pool_size=5,
    max_overflow=10,
    echo=settings.debug,
    connect_args={"connect_timeout": settings.postgres_connect_timeout},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: satu session per request, selalu ditutup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
