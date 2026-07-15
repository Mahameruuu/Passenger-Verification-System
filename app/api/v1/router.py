from fastapi import APIRouter

from app.api.v1.endpoints import config, faces, ktp

api_router = APIRouter()
api_router.include_router(config.router)
api_router.include_router(ktp.router)
api_router.include_router(faces.router)

# Catatan: /health sengaja TIDAK di sini. Health check dipasang di root
# (GET /health) supaya tidak ikut berubah saat versi API naik.
