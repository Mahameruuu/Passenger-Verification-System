import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.models import Base  # noqa: F401  (mendaftarkan semua model ke metadata)

# ---------------------------------------------------------------------------
# ALEMBIC DIMATIKAN.
#
# Skema database sekarang dikelola DI LUAR aplikasi ini (PostgreSQL GCP,
# `cvasdp`). Migration di folder ini membuat skema LAMA (passengers,
# ktp_documents, face_registrations, ...) yang sudah tidak dipakai.
#
# Menjalankan `alembic upgrade head` terhadap database GCP akan membuat
# tabel-tabel asing di sana. Karena itu dihentikan di sini, bukan sekadar
# ditulis di dokumentasi — dokumentasi tidak menghentikan siapa pun.
#
# Kalau memang butuh menjalankannya terhadap database lokal:
#     set PIVS_ALLOW_ALEMBIC=1
# ---------------------------------------------------------------------------
if os.environ.get("PIVS_ALLOW_ALEMBIC") != "1":
    sys.exit(
        "Alembic dinonaktifkan: skema dikelola di luar aplikasi (PostgreSQL GCP).\n"
        "Migration di folder ini akan membuat tabel skema LAMA di database tujuan.\n"
        "Bila Anda yakin, set PIVS_ALLOW_ALEMBIC=1 terlebih dahulu."
    )

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
