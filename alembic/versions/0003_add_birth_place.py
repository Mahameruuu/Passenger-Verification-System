"""passengers: tambah birth_place (TTL = Tempat/Tanggal Lahir)

OCR mengekstrak TTL sebagai satu field ("JAKARTA, 17-08-1985"). Tanggalnya
masuk ke birth_date, tapi tempatnya belum punya kolom.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "passengers",
        sa.Column("birth_place", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("passengers", "birth_place")
