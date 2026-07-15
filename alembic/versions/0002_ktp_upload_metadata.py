"""ktp upload: passenger_id nullable + metadata file

Dokumen KTP di-upload SEBELUM penumpang ada, karena NIK baru diketahui
setelah OCR berjalan. Karena itu passenger_id harus nullable; penautan ke
passengers dilakukan pada tahap OCR.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "ktp_documents",
        "passenger_id",
        existing_type=sa.UUID(),
        nullable=True,
    )
    op.add_column(
        "ktp_documents",
        sa.Column("original_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "ktp_documents",
        sa.Column("content_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "ktp_documents",
        sa.Column("file_size", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ktp_documents", "file_size")
    op.drop_column("ktp_documents", "content_type")
    op.drop_column("ktp_documents", "original_filename")
    # Baris tanpa pemilik harus dibersihkan dulu sebelum kolom dikembalikan NOT NULL.
    op.execute("DELETE FROM ktp_documents WHERE passenger_id IS NULL")
    op.alter_column(
        "ktp_documents",
        "passenger_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
