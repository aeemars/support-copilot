from fastapi import APIRouter, Query

from app.services.retrieval_service import MongoTextRetrieval

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=500)):
    return {"articles": await MongoTextRetrieval().search(q)}
