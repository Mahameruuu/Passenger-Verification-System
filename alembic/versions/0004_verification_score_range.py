"""face_registrations.verification_score: izinkan rentang -1..1

Skor verifikasi adalah cosine similarity antar embedding, yang secara
matematis berada di rentang -1..1. Batas lama (0..1) akan menolak skor
negatif — padahal skor negatif justru informasi yang valid: wajahnya
jelas bukan orang yang sama.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nama pendek: naming convention di Base.metadata menambahkan prefix
    # "ck_<table>_" secara otomatis, baik saat membuat maupun menghapus.
    op.drop_constraint(
        "verification_score_range", "face_registrations", type_="check"
    )
    op.create_check_constraint(
        "verification_score_range",
        "face_registrations",
        "verification_score IS NULL OR verification_score BETWEEN -1 AND 1",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE face_registrations SET verification_score = 0 "
        "WHERE verification_score < 0"
    )
    # Nama pendek: naming convention di Base.metadata menambahkan prefix
    # "ck_<table>_" secara otomatis, baik saat membuat maupun menghapus.
    op.drop_constraint(
        "verification_score_range", "face_registrations", type_="check"
    )
    op.create_check_constraint(
        "verification_score_range",
        "face_registrations",
        "verification_score IS NULL OR verification_score BETWEEN 0 AND 1",
    )
