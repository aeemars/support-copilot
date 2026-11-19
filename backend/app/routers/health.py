from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "api_configured": bool(settings.wekoya_api_key),
        "database": "connected",
        "model": settings.wekoya_model,
    }
