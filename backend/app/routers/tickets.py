from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.db import get_db
from app.models import TicketRequest, TicketResponse

router = APIRouter(prefix="/api", tags=["tickets"])


@router.post("/tickets", response_model=TicketResponse)
async def create_ticket(request: TicketRequest):
    db = get_db()
    customer = await db.customers.find_one({"customer_id": request.customer_id})
    if not customer:
        raise HTTPException(status_code=400, detail="The selected demo customer is invalid.")
    ticket = {
        "ticket_id": f"KORA-T-{uuid4().hex[:8].upper()}",
        "customer_id": request.customer_id,
        "subject": request.subject,
        "description": request.description,
        "status": "open",
        "created_at": datetime.now(timezone.utc),
    }
    await db.tickets.insert_one(ticket)
    return TicketResponse(**ticket)
