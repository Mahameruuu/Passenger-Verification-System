from fastapi import APIRouter

from app.core.config import settings
from app.schemas.config import AppConfigResponse, CameraConfig, FaceConfig

router = APIRouter(tags=["config"])


@router.get(
    "/config",
    response_model=AppConfigResponse,
    summary="Konfigurasi untuk client",
    description=(
        "Meneruskan sebagian isi `.env` ke browser — terutama pilihan kamera.\n\n"
        "Kamera di UI berjalan di browser (`getUserMedia`), yang tidak bisa "
        "membaca `.env` di server. Endpoint ini jembatannya: ubah "
        "`CAMERA_INDEX` di `.env` (0 = kamera laptop, 1 = webcam eksternal), "
        "restart server, dan UI otomatis memakai kamera tersebut.\n\n"
        "Hanya berisi nilai yang aman diketahui client — tidak ada kredensial."
    ),
)
def get_config() -> AppConfigResponse:
    return AppConfigResponse(
        app_name=settings.app_name,
        app_version=settings.app_version,
        camera=CameraConfig(
            index=settings.camera_index,
            width=settings.camera_width,
            height=settings.camera_height,
        ),
        face=FaceConfig(
            match_threshold=settings.face_match_threshold,
            min_quality_score=settings.face_min_quality_score,
        ),
    )
