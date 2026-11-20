from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models import ChatRequest, OrderStatusArgs
from app.services import chat_service
from app.services.bridge_client import BridgeResponseError, map_status_error
from app.services.order_service import get_order_status
from app.services.chat_service import ChatService, local_commerce_answer
from app.services.bridge_client import BridgeResponseError


class FakeCollection:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    async def find_one(self, query, *args, **kwargs):
        if self.name == "customers":
            return self.db.customer
        if self.name == "orders":
            if self.db.order and self.db.order["customer_id"] == query["customer_id"] and self.db.order["order_id"] == query["order_id"]:
                return self.db.order
            return None
        return None

    async def update_one(self, *args, **kwargs):
        return None

    async def insert_one(self, document):
        self.db.inserted = document


class FakeDB:
    def __init__(self, order=None, customer=True):
        self.order = order
        self.customer = {"customer_id": "cust-ada"} if customer else None
        self.customers = FakeCollection(self, "customers")
        self.orders = FakeCollection(self, "orders")
        self.chat_sessions = FakeCollection(self, "chat_sessions")
        self.tickets = FakeCollection(self, "tickets")

    async def find_one(self, query, *args, **kwargs):
        if "customer_id" in query and "order_id" in query:
            if self.order and self.order["customer_id"] == query["customer_id"] and self.order["order_id"] == query["order_id"]:
                return self.order
            return None
        if "customer_id" in query:
            return self.customer
        return None

    async def update_one(self, *args, **kwargs):
        return None

    async def insert_one(self, document):
        self.inserted = document


@pytest.mark.asyncio
async def test_order_lookup_prevents_cross_customer_access(monkeypatch):
    db = FakeDB({
        "order_id": "KORA-1001",
        "customer_id": "cust-ada",
        "status": "Out for delivery",
        "items": [],
    })
    monkeypatch.setattr("app.services.order_service.get_db", lambda: db)
    result = await get_order_status("cust-tunde", OrderStatusArgs(order_id="KORA-1001"))
    assert result["found"] is False


@pytest.mark.asyncio
async def test_order_questions_use_local_mongodb_records(monkeypatch):
    monkeypatch.setattr(
        "app.services.chat_service.user_orders",
        AsyncMock(return_value=[{"order_id": "KORA-ABC123", "fulfillment_status": "processing"}]),
    )
    answer = await local_commerce_answer("user-ada", "Do I have any placed order?")
    assert "KORA-ABC123" in answer
    assert "processing" in answer


@pytest.mark.asyncio
async def test_unsupported_question_has_safe_escalation(monkeypatch):
    db = FakeDB(customer=True)
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)

    bridge = SimpleNamespace(
        catalogue_checked=True,
        tools_supported=False,
        create=AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="I do not have enough verified information. Please escalate to a human agent.", tool_calls=None))],
            usage=None,
        )),
    )
    retrieval = SimpleNamespace(articles_for_context=AsyncMock(return_value=([], "")))
    response = await ChatService(bridge=bridge, retrieval=retrieval).chat(
        ChatRequest(user_id="user-ada", customer_id="cust-ada", message="What is my bank account balance?")
    )
    assert "human agent" in response.answer.lower()
    assert response.citations == []


@pytest.mark.asyncio
async def test_configuration_failure_returns_no_internal_details(monkeypatch):
    db = FakeDB(customer=True)
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)

    bridge = SimpleNamespace(
        catalogue_checked=False,
        tools_supported=False,
        check_catalogue=AsyncMock(return_value=(False, "gemini-2.5-flash-lite does not advertise tools")),
        create=AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="I do not have enough verified information. Please escalate to a human agent.", tool_calls=None))],
            usage=None,
        )),
    )
    retrieval = SimpleNamespace(articles_for_context=AsyncMock(return_value=([], "")))
    response = await ChatService(bridge=bridge, retrieval=retrieval).chat(
        ChatRequest(user_id="user-ada", customer_id="cust-ada", message="What is your return policy?")
    )

    assert "human agent" in response.answer.lower()
    assert "gemini" not in response.answer.lower()
    assert "configuration" not in response.answer.lower()
    assert response.citations == []
    retrieval.articles_for_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_cannot_be_reused_by_another_user(monkeypatch):
    db = FakeDB(customer=True)
    db.chat_sessions.find_one = AsyncMock(return_value={"user_id": "user-other"})
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)

    with pytest.raises(BridgeResponseError) as error:
        await ChatService().chat(ChatRequest(
            user_id="user-ada",
            session_id="session-owned-by-other",
            message="Hello",
        ))

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_citations_are_limited_to_retrieved_articles(monkeypatch):
    citation = chat_service.Citation(slug="returns-policy", title="Returns Policy", excerpt="Return within seven days.")
    db = FakeDB(customer=True)
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)
    bridge = SimpleNamespace(
        catalogue_checked=True,
        tools_supported=False,
        create=AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Answered from retrieved support.", tool_calls=None))],
            usage=None,
        )),
    )
    retrieval = SimpleNamespace(articles_for_context=AsyncMock(return_value=( [citation], "<article>Return policy</article>" )))
    response = await ChatService(bridge=bridge, retrieval=retrieval).chat(
        ChatRequest(user_id="user-ada", customer_id="cust-ada", message="What is the return policy?")
    )
    assert len(response.citations) == 1
    assert response.citations[0].slug == "returns-policy"


@pytest.mark.asyncio
async def test_ticket_creation_works(monkeypatch):
    db = FakeDB(customer=True)
    monkeypatch.setattr("app.routers.tickets.get_db", lambda: db)
    from app.routers.tickets import create_ticket
    from app.models import TicketRequest
    response = await create_ticket(TicketRequest(
        user_id="user-ada",
        customer_id="cust-ada",
        subject="Damaged item",
        description="My item arrived damaged.",
    ))
    assert response.status == "open"
    assert response.ticket_id.startswith("KORA-T-")


@pytest.mark.parametrize("status,expected", [
    (401, 502),
    (403, 502),
    (402, 402),
    (429, 429),
    (503, 502),
])
def test_wekoya_errors_are_safe(status, expected):
    error = map_status_error(status)
    assert error.status_code == expected
    assert "escalat" in str(error).lower() or "try" in str(error).lower()


@pytest.mark.asyncio
async def test_tool_loop_limit(monkeypatch):
    db = FakeDB(customer=True)
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)
    monkeypatch.setattr("app.services.order_service.get_db", lambda: db)
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="get_order_status", arguments='{"order_id":"KORA-1001"}'),
    )
    bridge = SimpleNamespace(
        catalogue_checked=True,
        tools_supported=True,
        create=AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))],
            usage=None,
        )),
    )
    retrieval = SimpleNamespace(articles_for_context=AsyncMock(return_value=([], "")))
    response = await ChatService(bridge=bridge, retrieval=retrieval).chat(
        ChatRequest(user_id="user-ada", customer_id="cust-ada", message="Where is KORA-1001?")
    )
    assert bridge.create.await_count == 5
    assert "could not complete" in response.answer.lower()
