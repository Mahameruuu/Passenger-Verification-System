from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Mengecek aplikasi dan koneksi PostgreSQL. "
        "Mengembalikan 503 bila database tidak dapat dihubungi, "
        "supaya bisa dipakai sebagai readiness probe."
    ),
    responses={503: {"model": HealthResponse, "description": "Database tidak sehat"}},
)
def health_check(response: Response, db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        database = "connected"
        database_error = None
    except SQLAlchemyError as exc:
        database = "disconnected"
        database_error = str(exc.__cause__ or exc).strip().splitlines()[0]
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if database == "connected" else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        database=database,
        database_error=database_error,
    )
