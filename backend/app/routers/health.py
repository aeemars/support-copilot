from fastapi import APIRouter

from app.config import settings
from app.db import mongo

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "api_configured": bool(settings.wekoya_api_key),
        "database": "connected" if mongo.client is not None else "disconnected",
        "model": settings.wekoya_model,
    }
