from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="ok = semua dependensi sehat; degraded = ada yang bermasalah"
    )
    app: str
    version: str
    database: Literal["connected", "disconnected"]
    database_error: str | None = Field(
        default=None, description="Alasan kegagalan koneksi, hanya terisi bila gagal"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "ok",
                    "app": "Passenger Identity Verification System",
                    "version": "0.1.0",
                    "database": "connected",
                    "database_error": None,
                }
            ]
        }
    }
