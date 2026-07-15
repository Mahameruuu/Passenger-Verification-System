from pydantic import BaseModel, Field


class CameraConfig(BaseModel):
    """Pilihan kamera yang dibaca dari .env, diteruskan ke browser."""

    index: int = Field(
        description="Urutan perangkat video. 0 = kamera laptop, 1 = webcam eksternal."
    )
    width: int
    height: int


class FaceConfig(BaseModel):
    match_threshold: float
    min_quality_score: float


class AppConfigResponse(BaseModel):
    """Konfigurasi yang boleh diketahui client. Tidak memuat rahasia apa pun."""

    app_name: str
    app_version: str
    camera: CameraConfig
    face: FaceConfig
