from fastapi import APIRouter, HTTPException

from app.models import ChatRequest
from app.services.bridge_client import BridgeResponseError
from app.services.chat_service import ChatService

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(request: ChatRequest):
    try:
        return await ChatService().chat(request)
    except BridgeResponseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
