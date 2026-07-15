import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidFileTypeError,
    InvalidImageError,
    KTPDocumentNotFoundError,
    OCREngineError,
    StorageError,
)
from app.db.session import get_db
from app.schemas.ktp import ErrorResponse, KTPDocumentResponse
from app.schemas.ocr import ConfirmKTPRequest, OCRResultResponse, ParsedKTP
from app.services.ktp_service import KTPService
from app.services.ocr.engine import get_shared_engine
from app.services.ocr.ktp_parser import KTPFields
from app.services.ocr_service import OCRService
from app.services.storage import MAX_FILE_SIZE

router = APIRouter(prefix="/ktp", tags=["ktp"])


def get_ktp_service(db: Session = Depends(get_db)) -> KTPService:
    return KTPService(db)


def get_ocr_service(db: Session = Depends(get_db)) -> OCRService:
    # Suntikkan engine bersama (singleton) supaya model OCR tidak dimuat ulang
    # tiap request — inilah perbaikan utama untuk OCR yang tadinya ~1 menit.
    return OCRService(db, engine=get_shared_engine())


def _to_document(service: KTPService, event) -> KTPDocumentResponse:
    doc = KTPDocumentResponse.model_validate(event)
    doc.image_url = service.image_url(event)
    return doc


def _ocr_response(ktp: KTPService, result) -> OCRResultResponse:
    return OCRResultResponse(
        ocr_status=result.status,
        parsed=ParsedKTP(
            nik=result.fields.nik,
            full_name=result.fields.full_name,
            birth_place=result.fields.birth_place,
            birth_date=result.fields.birth_date,
            gender=result.fields.gender,
            address=result.fields.address,
        ),
        confidence=round(result.fields.confidence, 4),
        warnings=result.fields.warnings,
        person_created=result.person_created,
        person=result.person,
        document=_to_document(ktp, result.event),
    )


@router.post(
    "/upload",
    response_model=KTPDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload foto KTP",
    description=(
        "Meng-upload foto KTP ke **MinIO** (`ktp/<tahun>/<bulan>/<uuid>.jpg`), "
        "lalu mencatat object key-nya ke `detection_event.raw_image_key`.\n\n"
        f"- Format: **JPG/PNG** (diperiksa dari isi file, bukan ekstensi)\n"
        f"- Maksimal: **{MAX_FILE_SIZE // (1024 * 1024)} MB**\n\n"
        "File **tidak** lagi disimpan di folder lokal. **OCR belum dijalankan** — "
        "event dicatat dengan `verification_status = PENDING`."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "File kosong atau bukan JPG/PNG"},
        413: {"model": ErrorResponse, "description": "Ukuran file melebihi batas"},
    },
)
async def upload_ktp(
    file: UploadFile = File(..., description="Foto KTP (JPG/PNG, maks 5 MB)"),
    service: KTPService = Depends(get_ktp_service),
) -> KTPDocumentResponse:
    try:
        event = await service.upload(file)
    except (InvalidFileTypeError, EmptyFileError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return _to_document(service, event)


@router.post(
    "/{document_id}/ocr",
    response_model=OCRResultResponse,
    summary="Jalankan OCR pada dokumen KTP",
    description=(
        "Mengambil gambar dari MinIO, membacanya dengan PaddleOCR, mengekstrak "
        "**NIK, Nama, TTL, Jenis Kelamin, Alamat**, lalu:\n\n"
        "- menyimpan identitas ke tabel **`person`** (`full_name`, `citizen_id`)\n"
        "- menyimpan seluruh hasil OCR ke `detection_event.ocr_result`\n\n"
        "**Catatan:** tabel `person` tidak punya kolom TTL/jenis kelamin/alamat, "
        "sehingga field itu hanya tersimpan sebagai JSON dan tidak bisa di-query "
        "sebagai kolom.\n\n"
        "Periksa `ocr_status`: `SUCCESS` / `PARTIAL` (person tidak dibuat) / `FAILED`."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Dokumen tidak ditemukan"},
        422: {"model": ErrorResponse, "description": "Gambar gagal dibaca"},
    },
)
def run_ocr(
    document_id: uuid.UUID,
    service: OCRService = Depends(get_ocr_service),
    ktp: KTPService = Depends(get_ktp_service),
) -> OCRResultResponse:
    try:
        result = service.process(document_id)
    except KTPDocumentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (OCREngineError, InvalidImageError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return _ocr_response(ktp, result)


@router.post(
    "/{document_id}/confirm",
    response_model=OCRResultResponse,
    summary="Simpan identitas dari hasil OCR yang sudah dikonfirmasi",
    description=(
        "Dipanggil saat petugas menekan **Simpan** setelah meninjau (dan bila "
        "perlu mengoreksi) hasil OCR. Baru pada tahap inilah baris **`person`** "
        "dibuat.\n\n"
        "- **NIK** wajib 16 digit; **Nama** wajib diisi.\n"
        "- Bila NIK sudah terdaftar, dokumen ditautkan ke person yang ada "
        "tanpa menimpa identitas."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Dokumen tidak ditemukan"},
        422: {"model": ErrorResponse, "description": "Data tidak valid"},
    },
)
def confirm_ktp(
    document_id: uuid.UUID,
    payload: ConfirmKTPRequest,
    service: OCRService = Depends(get_ocr_service),
    ktp: KTPService = Depends(get_ktp_service),
) -> OCRResultResponse:
    fields = KTPFields(
        nik=payload.nik,
        full_name=payload.full_name,
        birth_place=payload.birth_place,
        birth_date=payload.birth_date,
        gender=payload.gender,
        address=payload.address,
        confidence=1.0,  # data dikonfirmasi manusia — bukan lagi taksiran OCR
    )
    try:
        result = service.confirm(document_id, fields)
    except KTPDocumentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (OCREngineError, InvalidImageError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return _ocr_response(ktp, result)


@router.get(
    "/{document_id}",
    response_model=KTPDocumentResponse,
    summary="Ambil metadata dokumen KTP",
    responses={404: {"model": ErrorResponse, "description": "Dokumen tidak ditemukan"}},
)
def get_ktp_document(
    document_id: uuid.UUID,
    service: KTPService = Depends(get_ktp_service),
) -> KTPDocumentResponse:
    try:
        event = service.get(document_id)
    except KTPDocumentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return _to_document(service, event)
