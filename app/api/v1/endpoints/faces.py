import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    DuplicateFaceError,
    EmptyFileError,
    FaceEngineError,
    FaceMismatchError,
    FaceNotRegisteredError,
    FileTooLargeError,
    InvalidFileTypeError,
    InvalidImageError,
    MultipleFacesError,
    NoFaceDetectedError,
    PassengerNotFoundError,
    StorageError,
)
from app.db.session import get_db
from app.schemas.face import (
    EmbeddingResponse,
    FaceMatchResponse,
    FaceRegistrationResult,
    QualityReportResponse,
)
from app.schemas.ktp import ErrorResponse
from app.services.face_service import FaceService
from app.services.match_service import MatchResult, MatchService

router = APIRouter(prefix="/faces", tags=["faces"])


def get_face_service(db: Session = Depends(get_db)) -> FaceService:
    return FaceService(db)


def get_match_service(db: Session = Depends(get_db)) -> MatchService:
    return MatchService(db)


def _to_match_response(result: MatchResult) -> FaceMatchResponse:
    return FaceMatchResponse(
        matched=result.matched,
        similarity=round(result.similarity, 4),
        threshold=result.threshold,
        faces_detected=result.faces_detected,
        probe_det_score=round(result.probe_det_score, 4),
        runner_up_similarity=(
            round(result.runner_up_similarity, 4)
            if result.runner_up_similarity is not None
            else None
        ),
        person=result.person,
        selfie_url=result.selfie_url,
    )


@router.post(
    "/register",
    response_model=FaceRegistrationResult,
    status_code=status.HTTP_201_CREATED,
    summary="Registrasi wajah (FaceNet → pgvector)",
    description=(
        "Menerima satu frame kamera, lalu menjalankan:\n\n"
        "**Detection (RetinaFace ResNet50) → Alignment → Quality Check → Crop → "
        "Embedding (FaceNet InceptionResnetV1, 512-d)**\n\n"
        "- Crop wajah 112×112 diunggah ke **MinIO** (`faces/<tahun>/<bulan>/`)\n"
        "- Embedding disimpan ke **`embedding_metadata.vector`** (pgvector)\n"
        "- Event dicatat di `detection_event`\n\n"
        "`registration_status`: `ACCEPTED` atau `REJECTED` (embedding tidak dibuat).\n\n"
        "**`409`** bila wajah sudah terdaftar sebagai orang **lain** — dicek lewat "
        "similarity search pgvector. Satu orang tidak boleh punya dua identitas."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "File kosong / bukan JPG/PNG"},
        404: {"model": ErrorResponse, "description": "Orang tidak ditemukan"},
        409: {"model": ErrorResponse, "description": "Wajah milik orang lain, atau tidak cocok dengan wajah orang ini yang sudah terdaftar"},
        413: {"model": ErrorResponse, "description": "Ukuran file melebihi batas"},
        422: {"model": ErrorResponse, "description": "Nol atau >1 wajah, gambar rusak"},
    },
)
async def register_face(
    passenger_id: uuid.UUID = Form(
        ..., description="person_id dari hasil OCR KTP"
    ),
    file: UploadFile = File(..., description="Frame wajah dari kamera (JPG/PNG)"),
    service: FaceService = Depends(get_face_service),
) -> FaceRegistrationResult:
    try:
        result = await service.register(passenger_id, file)
    except (InvalidFileTypeError, EmptyFileError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except PassengerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (DuplicateFaceError, FaceMismatchError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (NoFaceDetectedError, MultipleFacesError, InvalidImageError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except (FaceEngineError, StorageError) as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return FaceRegistrationResult(
        registration_status="ACCEPTED" if result.accepted else "REJECTED",
        quality=QualityReportResponse(**result.quality.to_json()),
        face_url=result.face_url,
        embedding=(
            EmbeddingResponse.model_validate(result.embedding)
            if result.embedding
            else None
        ),
        person=result.person,
    )


@router.post(
    "/verify",
    response_model=FaceMatchResponse,
    summary="Verifikasi 1:1 (pgvector)",
    description=(
        "**Selfie → embedding FaceNet → similarity search pgvector → threshold.**\n\n"
        "Mencocokkan selfie dengan embedding milik satu orang. Setiap percobaan "
        "dicatat ke `detection_event` — termasuk yang gagal."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Orang tidak ditemukan"},
        409: {"model": ErrorResponse, "description": "Belum ada wajah terdaftar"},
        422: {"model": ErrorResponse, "description": "Tidak ada wajah pada selfie"},
    },
)
async def verify_face(
    passenger_id: uuid.UUID = Form(..., description="person_id yang akan diverifikasi"),
    file: UploadFile = File(..., description="Selfie (JPG/PNG)"),
    service: MatchService = Depends(get_match_service),
) -> FaceMatchResponse:
    try:
        result = await service.verify(passenger_id, file)
    except (InvalidFileTypeError, EmptyFileError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except PassengerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except FaceNotRegisteredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (NoFaceDetectedError, InvalidImageError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except (FaceEngineError, StorageError) as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return _to_match_response(result)


@router.post(
    "/identify",
    response_model=FaceMatchResponse,
    summary="Identifikasi 1:N (similarity search pgvector)",
    description=(
        "Mengambil embedding wajah live, lalu mencari identitas paling mirip "
        "lewat **index HNSW pgvector** (`vector_cosine_ops`, operator `<=>`).\n\n"
        "Hanya embedding dengan `model_version` yang sama yang dibandingkan — "
        "embedding dari model berbeda tidak sebanding.\n\n"
        "`runner_up_similarity` adalah skor kandidat terbaik dari **orang lain**; "
        "bila rapat dengan `similarity`, sistem sedang ragu."
    ),
    responses={
        409: {"model": ErrorResponse, "description": "Belum ada wajah terdaftar"},
        422: {"model": ErrorResponse, "description": "Tidak ada wajah pada selfie"},
    },
)
async def identify_face(
    file: UploadFile = File(..., description="Selfie (JPG/PNG)"),
    service: MatchService = Depends(get_match_service),
) -> FaceMatchResponse:
    try:
        result = await service.identify(file)
    except (InvalidFileTypeError, EmptyFileError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except FaceNotRegisteredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (NoFaceDetectedError, InvalidImageError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except (FaceEngineError, StorageError) as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return _to_match_response(result)


@router.get(
    "/passenger/{passenger_id}",
    response_model=list[EmbeddingResponse],
    summary="Riwayat embedding wajah seseorang",
    description=(
        "Semua embedding milik orang ini di `embedding_metadata`, terbaru dulu. "
        "Vektornya sendiri tidak dikirim."
    ),
    responses={404: {"model": ErrorResponse, "description": "Orang tidak ditemukan"}},
)
def list_faces(
    passenger_id: uuid.UUID,
    service: FaceService = Depends(get_face_service),
) -> list[EmbeddingResponse]:
    try:
        rows = service.list_for_person(passenger_id)
    except PassengerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return [EmbeddingResponse.model_validate(r) for r in rows]


@router.get(
    "/model",
    summary="Model wajah yang sedang dipakai",
    description=(
        "Berguna untuk memastikan embedding lama & baru berasal dari model yang "
        "sama. Embedding dari model berbeda TIDAK sebanding."
    ),
)
def face_model_info() -> dict:
    return {
        "engine_backend": settings.face_engine,
        "detector": "RetinaFace ResNet50 (ONNX)",
        "detector_model_path": settings.retinaface_model_path,
        "embedding_model": "FaceNet InceptionResnetV1",
        "pretrained": settings.facenet_pretrained,
        "model_version": settings.face_model_version,
        "embedding_dim": settings.face_embedding_dim,
        "device": settings.facenet_device,
        "match_threshold": settings.face_match_threshold,
        "duplicate_threshold": settings.face_duplicate_threshold,
    }
