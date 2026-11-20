from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    slug: str
    title: str
    excerpt: str


class ChatRequest(BaseModel):
    session_id: str | None = None
    user_id: str = Field(min_length=1, max_length=128)
    customer_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    session_id: str
    user_id: str
    answer: str
    citations: list[Citation] = []
    tool_used: Literal["get_order_status"] | None = None
    usage: dict = {}


class TicketRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    customer_id: str | None = None
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)


class TicketResponse(BaseModel):
    ticket_id: str
    user_id: str
    customer_id: str | None = None
    subject: str
    description: str
    status: Literal["open"] = "open"
    created_at: datetime


class OrderStatusArgs(BaseModel):
    order_id: str = Field(min_length=1, max_length=100)
