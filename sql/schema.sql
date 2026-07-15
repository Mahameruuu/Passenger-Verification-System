-- ============================================================================
-- PIVS — Passenger Identity Verification System
-- Skema PostgreSQL (referensi / setara dengan Alembic revision 0001)
-- Untuk deployment gunakan Alembic. File ini untuk review & dokumentasi.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ------------------------------------------------------------------- ENUM ---
CREATE TYPE gender AS ENUM ('LAKI_LAKI', 'PEREMPUAN');

CREATE TYPE registration_status AS ENUM (
    'DRAFT',            -- baris dibuat, KTP belum diproses
    'KTP_VERIFIED',     -- OCR sukses, identitas terisi
    'FACE_REGISTERED',  -- wajah terdaftar
    'ACTIVE',           -- siap dipakai verifikasi boarding
    'REJECTED'
);

CREATE TYPE ocr_status AS ENUM (
    'PENDING', 'PROCESSING', 'SUCCESS', 'PARTIAL', 'FAILED'
);

CREATE TYPE face_registration_status AS ENUM (
    'PENDING', 'ACTIVE', 'REPLACED', 'REJECTED'
);

CREATE TYPE boarding_type AS ENUM ('BOARDING', 'DISEMBARKING');

CREATE TYPE boarding_result AS ENUM (
    'MATCHED', 'NOT_MATCHED', 'NO_FACE_DETECTED', 'ERROR'
);

-- ------------------------------------------------------------- passengers ---
CREATE TABLE passengers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nik                 VARCHAR(16)  NOT NULL,
    full_name           VARCHAR(255) NOT NULL,
    birth_date          DATE,
    gender              gender,
    address             TEXT,
    registration_status registration_status NOT NULL DEFAULT 'DRAFT',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT uq_passengers_nik UNIQUE (nik),
    CONSTRAINT ck_passengers_nik_format CHECK (nik ~ '^[0-9]{16}$'),
    CONSTRAINT ck_passengers_full_name_not_blank CHECK (char_length(full_name) > 0)
);

CREATE UNIQUE INDEX ix_passengers_nik ON passengers (nik);
CREATE INDEX ix_passengers_registration_status ON passengers (registration_status);

-- ---------------------------------------------------------- ktp_documents ---
CREATE TABLE ktp_documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    passenger_id UUID NOT NULL,
    image_path   VARCHAR(512) NOT NULL,   -- storage/ktp/2026/07/<nik>.jpg
    ocr_json     JSONB,                   -- hasil mentah PaddleOCR
    ocr_status   ocr_status NOT NULL DEFAULT 'PENDING',
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_ktp_documents_passenger_id_passengers
        FOREIGN KEY (passenger_id) REFERENCES passengers (id) ON DELETE CASCADE
);

CREATE INDEX ix_ktp_documents_passenger_id ON ktp_documents (passenger_id);
CREATE INDEX ix_ktp_documents_ocr_status   ON ktp_documents (ocr_status);

-- ----------------------------------------------------- face_registrations ---
CREATE TABLE face_registrations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    passenger_id        UUID NOT NULL,
    face_image_path     VARCHAR(512) NOT NULL,  -- storage/faces/...
    embedding_path      VARCHAR(512),           -- placeholder: .npy, nanti pgvector
    quality_score       NUMERIC(5, 4),
    verification_score  NUMERIC(5, 4),
    registration_status face_registration_status NOT NULL DEFAULT 'PENDING',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_face_registrations_passenger_id_passengers
        FOREIGN KEY (passenger_id) REFERENCES passengers (id) ON DELETE CASCADE,
    CONSTRAINT ck_face_registrations_quality_score_range
        CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 1),
    CONSTRAINT ck_face_registrations_verification_score_range
        CHECK (verification_score IS NULL OR verification_score BETWEEN 0 AND 1)
);

CREATE INDEX ix_face_registrations_passenger_id
    ON face_registrations (passenger_id);
CREATE INDEX ix_face_registrations_registration_status
    ON face_registrations (registration_status);

-- Hanya boleh ada satu wajah ACTIVE per penumpang.
CREATE UNIQUE INDEX uq_face_registrations_passenger_active
    ON face_registrations (passenger_id)
    WHERE registration_status = 'ACTIVE';

-- ---------------------------------------------------------- boarding_logs ---
CREATE TABLE boarding_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    passenger_id        UUID,             -- NULL = wajah tidak dikenali
    camera_name         VARCHAR(100) NOT NULL,
    boarding_type       boarding_type NOT NULL,
    result              boarding_result NOT NULL DEFAULT 'MATCHED',
    match_score         NUMERIC(5, 4),
    captured_image_path VARCHAR(512),     -- storage/boarding/...
    boarding_time       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- RESTRICT: riwayat boarding tidak boleh hilang karena penghapusan penumpang.
    CONSTRAINT fk_boarding_logs_passenger_id_passengers
        FOREIGN KEY (passenger_id) REFERENCES passengers (id) ON DELETE RESTRICT,
    CONSTRAINT ck_boarding_logs_match_score_range
        CHECK (match_score IS NULL OR match_score BETWEEN 0 AND 1),
    CONSTRAINT ck_boarding_logs_matched_requires_passenger
        CHECK (result <> 'MATCHED' OR passenger_id IS NOT NULL)
);

CREATE INDEX ix_boarding_logs_passenger_id  ON boarding_logs (passenger_id);
CREATE INDEX ix_boarding_logs_boarding_time ON boarding_logs (boarding_time);
CREATE INDEX ix_boarding_logs_passenger_id_boarding_time
    ON boarding_logs (passenger_id, boarding_time DESC);

-- ------------------------------------------------------------- audit_logs ---
CREATE TABLE audit_logs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    passenger_id UUID,
    actor        VARCHAR(100) NOT NULL,  -- petugas / sistem
    action       VARCHAR(100) NOT NULL,  -- KTP_UPLOADED, OCR_COMPLETED, ...
    entity_type  VARCHAR(50)  NOT NULL,  -- passengers, ktp_documents, ...
    entity_id    UUID,
    payload      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_audit_logs_passenger_id_passengers
        FOREIGN KEY (passenger_id) REFERENCES passengers (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_logs_passenger_id ON audit_logs (passenger_id);
CREATE INDEX ix_audit_logs_created_at   ON audit_logs (created_at);
CREATE INDEX ix_audit_logs_entity_type_entity_id
    ON audit_logs (entity_type, entity_id);
