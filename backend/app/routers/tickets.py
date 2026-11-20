from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.db import get_db
from app.models import TicketRequest, TicketResponse

router = APIRouter(prefix="/api", tags=["tickets"])


@router.post("/tickets", response_model=TicketResponse)
async def create_ticket(request: TicketRequest):
    db = get_db()
    if request.customer_id and not await db.customers.find_one({"customer_id": request.customer_id}):
        raise HTTPException(status_code=400, detail="The selected demo customer is invalid.")
    ticket = {
        "ticket_id": f"KORA-T-{uuid4().hex[:8].upper()}",
        "user_id": request.user_id,
        "customer_id": request.customer_id,
        "subject": request.subject,
        "description": request.description,
        "status": "open",
        "created_at": datetime.now(timezone.utc),
    }
    await db.tickets.insert_one(ticket)
    from app.services.activity_cache import activity_cache
    await activity_cache.record(request.user_id, f"ticket:{ticket['ticket_id']}", 1, request.subject)
    return TicketResponse(**ticket)
