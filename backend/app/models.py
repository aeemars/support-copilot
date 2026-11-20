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
    tool_used: Literal["get_order_status", "get_return_status", "get_refund_status"] | None = None
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


class CartItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=20)


class CheckoutRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=200)
    customer_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=80)
    state: str = Field(min_length=1, max_length=80)
    payment_method: Literal["mock_card", "bank_transfer", "payment_link"] = "mock_card"


class ReturnRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    items: list[dict] = Field(min_length=1)
    reason: Literal["unwanted", "damaged", "wrong_item", "faulty", "other"]
    reason_details: str | None = Field(default=None, max_length=1000)


class StatusLookupArgs(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    order_id: str | None = None
    return_id: str | None = None
    refund_id: str | None = None
