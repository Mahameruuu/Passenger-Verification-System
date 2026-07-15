"""initial schema: passengers, ktp_documents, face_registrations, boarding_logs, audit_logs

Revision ID: 0001
Revises:
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


gender_enum = postgresql.ENUM(
    "LAKI_LAKI", "PEREMPUAN", name="gender", create_type=False
)
registration_status_enum = postgresql.ENUM(
    "DRAFT",
    "KTP_VERIFIED",
    "FACE_REGISTERED",
    "ACTIVE",
    "REJECTED",
    name="registration_status",
    create_type=False,
)
ocr_status_enum = postgresql.ENUM(
    "PENDING",
    "PROCESSING",
    "SUCCESS",
    "PARTIAL",
    "FAILED",
    name="ocr_status",
    create_type=False,
)
face_registration_status_enum = postgresql.ENUM(
    "PENDING",
    "ACTIVE",
    "REPLACED",
    "REJECTED",
    name="face_registration_status",
    create_type=False,
)
boarding_type_enum = postgresql.ENUM(
    "BOARDING", "DISEMBARKING", name="boarding_type", create_type=False
)
boarding_result_enum = postgresql.ENUM(
    "MATCHED",
    "NOT_MATCHED",
    "NO_FACE_DETECTED",
    "ERROR",
    name="boarding_result",
    create_type=False,
)

ALL_ENUMS = (
    gender_enum,
    registration_status_enum,
    ocr_status_enum,
    face_registration_status_enum,
    boarding_type_enum,
    boarding_result_enum,
)


def upgrade() -> None:
    bind = op.get_bind()

    # gen_random_uuid() bawaan PostgreSQL 13+; pgcrypto untuk versi lebih lama.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    for enum in ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    # ---------------------------------------------------------------- passengers
    op.create_table(
        "passengers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("nik", sa.String(length=16), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("gender", gender_enum, nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "registration_status",
            registration_status_enum,
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_passengers"),
        sa.UniqueConstraint("nik", name="uq_passengers_nik"),
        # Nama pendek: naming convention di Base.metadata menambahkan prefix
        # "ck_<table>_" secara otomatis.
        sa.CheckConstraint("nik ~ '^[0-9]{16}$'", name="nik_format"),
        sa.CheckConstraint(
            "char_length(full_name) > 0", name="full_name_not_blank"
        ),
    )
    op.create_index("ix_passengers_nik", "passengers", ["nik"], unique=True)
    op.create_index(
        "ix_passengers_registration_status", "passengers", ["registration_status"]
    )

    # ------------------------------------------------------------ ktp_documents
    op.create_table(
        "ktp_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("passenger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("image_path", sa.String(length=512), nullable=False),
        sa.Column("ocr_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "ocr_status", ocr_status_enum, server_default="PENDING", nullable=False
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ktp_documents"),
        sa.ForeignKeyConstraint(
            ["passenger_id"],
            ["passengers.id"],
            name="fk_ktp_documents_passenger_id_passengers",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_ktp_documents_passenger_id", "ktp_documents", ["passenger_id"])
    op.create_index("ix_ktp_documents_ocr_status", "ktp_documents", ["ocr_status"])

    # ------------------------------------------------------- face_registrations
    op.create_table(
        "face_registrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("passenger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("face_image_path", sa.String(length=512), nullable=False),
        sa.Column("embedding_path", sa.String(length=512), nullable=True),
        sa.Column("quality_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "verification_score", sa.Numeric(precision=5, scale=4), nullable=True
        ),
        sa.Column(
            "registration_status",
            face_registration_status_enum,
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_face_registrations"),
        sa.ForeignKeyConstraint(
            ["passenger_id"],
            ["passengers.id"],
            name="fk_face_registrations_passenger_id_passengers",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "quality_score IS NULL OR quality_score BETWEEN 0 AND 1",
            name="quality_score_range",
        ),
        sa.CheckConstraint(
            "verification_score IS NULL OR verification_score BETWEEN 0 AND 1",
            name="verification_score_range",
        ),
    )
    op.create_index(
        "ix_face_registrations_passenger_id", "face_registrations", ["passenger_id"]
    )
    op.create_index(
        "ix_face_registrations_registration_status",
        "face_registrations",
        ["registration_status"],
    )
    op.create_index(
        "uq_face_registrations_passenger_active",
        "face_registrations",
        ["passenger_id"],
        unique=True,
        postgresql_where=sa.text("registration_status = 'ACTIVE'"),
    )

    # ------------------------------------------------------------ boarding_logs
    op.create_table(
        "boarding_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("passenger_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("camera_name", sa.String(length=100), nullable=False),
        sa.Column("boarding_type", boarding_type_enum, nullable=False),
        sa.Column(
            "result", boarding_result_enum, server_default="MATCHED", nullable=False
        ),
        sa.Column("match_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("captured_image_path", sa.String(length=512), nullable=True),
        sa.Column(
            "boarding_time",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_boarding_logs"),
        sa.ForeignKeyConstraint(
            ["passenger_id"],
            ["passengers.id"],
            name="fk_boarding_logs_passenger_id_passengers",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "match_score IS NULL OR match_score BETWEEN 0 AND 1",
            name="match_score_range",
        ),
        sa.CheckConstraint(
            "result <> 'MATCHED' OR passenger_id IS NOT NULL",
            name="matched_requires_passenger",
        ),
    )
    op.create_index("ix_boarding_logs_passenger_id", "boarding_logs", ["passenger_id"])
    op.create_index(
        "ix_boarding_logs_boarding_time", "boarding_logs", ["boarding_time"]
    )
    op.create_index(
        "ix_boarding_logs_passenger_id_boarding_time",
        "boarding_logs",
        ["passenger_id", sa.text("boarding_time DESC")],
    )

    # --------------------------------------------------------------- audit_logs
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("passenger_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(
            ["passenger_id"],
            ["passengers.id"],
            name="fk_audit_logs_passenger_id_passengers",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_audit_logs_passenger_id", "audit_logs", ["passenger_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index(
        "ix_audit_logs_entity_type_entity_id",
        "audit_logs",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("audit_logs")
    op.drop_table("boarding_logs")
    op.drop_table("face_registrations")
    op.drop_table("ktp_documents")
    op.drop_table("passengers")

    for enum in ALL_ENUMS:
        enum.drop(bind, checkfirst=True)
