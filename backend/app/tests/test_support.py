from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models import ChatRequest, OrderStatusArgs
from app.services import chat_service
from app.services.bridge_client import BridgeResponseError, map_status_error
from app.services.order_service import get_order_status
from app.services.chat_service import ChatService, confirmed_order_answer, local_commerce_answer
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
@pytest.mark.parametrize("message", [
    "Place an order for 2 Wireless Earbuds",
    "I want to place an order",
    "I want a refund for my damaged item",
    "Please return this item",
])
async def test_commerce_actions_are_not_misclassified_as_status(monkeypatch, message):
    answer = await local_commerce_answer("user-ada", message)
    assert answer is None


@pytest.mark.asyncio
async def test_confirmed_order_is_processed_with_payment_state(monkeypatch):
    monkeypatch.setattr("app.services.chat_service.list_products", AsyncMock(return_value=[{
        "product_id": "prod-earbuds",
        "name": "Wireless Earbuds",
    }]))
    add = AsyncMock()
    checkout = AsyncMock(return_value={
        "order_id": "KORA-NEW123",
        "payment_status": "paid",
    })
    monkeypatch.setattr("app.services.chat_service.add_to_cart", add)
    monkeypatch.setattr("app.services.chat_service.checkout", checkout)
    history = [{
        "role": "assistant",
        "content": (
            "Here is your order summary:\n"
            "• **Product:** Wireless Earbuds\n"
            "• **Quantity:** 1\n"
            "• **Name:** Ada Okafor\n"
            "• **Delivery Address:** 2 Maplewood Estate, Lagos\n"
            "• **Phone Number:** 08136705593\n"
            "• **Payment Method:** mock card\n"
            "• **Email:** ada@example.com"
        ),
    }]
    answer = await confirmed_order_answer("user-ada", "session-1", "confirm order", history)
    assert "KORA-NEW123" in answer
    add.assert_awaited_once()
    checkout.assert_awaited_once()
    assert checkout.call_args.args[0].payment_method == "mock_card"


@pytest.mark.asyncio
async def test_confirmed_bank_transfer_order_waits_for_payment(monkeypatch):
    monkeypatch.setattr("app.services.chat_service.list_products", AsyncMock(return_value=[{
        "product_id": "prod-earbuds",
        "name": "Wireless Earbuds",
    }]))
    monkeypatch.setattr("app.services.chat_service.add_to_cart", AsyncMock())
    monkeypatch.setattr("app.services.chat_service.checkout", AsyncMock(return_value={
        "order_id": "KORA-NEW124",
        "payment_status": "pending",
    }))
    answer = await confirmed_order_answer(
        "user-ada",
        "session-1",
        "proceed with the order",
        [{"role": "assistant", "content": (
            "Order summary:\n"
            "• Product: Wireless Earbuds\n"
            "• Quantity: 1\n"
            "• Name: Ada Okafor\n"
            "• Delivery Address: 2 Maplewood Estate, Lagos\n"
            "• Payment Method: bank transfer\n"
            "Email: ada@example.com"
        )}],
    )
    assert "awaiting payment" in answer


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
async def test_product_questions_use_verified_catalogue_context(monkeypatch):
    db = FakeDB(customer=True)
    db.products = object()
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)
    monkeypatch.setattr(
        "app.services.chat_service.list_products",
        AsyncMock(return_value=[{
            "product_id": "prod-earbuds",
            "name": "Wireless Earbuds",
            "description": "Bluetooth earbuds.",
            "category": "Audio",
            "price_minor": 4500000,
            "stock_quantity": 18,
            "warranty_summary": "One-year warranty.",
        }]),
    )
    bridge = SimpleNamespace(
        catalogue_checked=True,
        tools_supported=False,
        create=AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Wireless Earbuds are available for NGN 45,000.00.", tool_calls=None))],
            usage=None,
        )),
    )
    retrieval = SimpleNamespace(articles_for_context=AsyncMock(return_value=([
        chat_service.Citation(slug="payment-methods", title="Payment Methods", excerpt="Cards and transfers."),
    ], "<article>Payment methods</article>")))
    response = await ChatService(bridge=bridge, retrieval=retrieval).chat(
        ChatRequest(user_id="user-ada", customer_id="cust-ada", message="I need a list of available products")
    )
    sent_message = bridge.create.call_args.kwargs["messages"][1]["content"]
    assert "Wireless Earbuds" in sent_message
    assert "NGN 45,000.00" in sent_message
    assert response.citations == []


@pytest.mark.asyncio
async def test_chat_includes_bounded_session_history(monkeypatch):
    db = FakeDB(customer=True)
    db.chat_sessions.find_one = AsyncMock(return_value={
        "user_id": "user-ada",
        "messages": [
            {"role": "user", "content": "I want Wireless Earbuds"},
            {"role": "assistant", "content": "They are available for NGN 45,000."},
        ],
    })
    db.products = object()
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)
    monkeypatch.setattr("app.services.chat_service.list_products", AsyncMock(return_value=[]))
    bridge = SimpleNamespace(
        catalogue_checked=True,
        tools_supported=False,
        create=AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Please provide your delivery details.", tool_calls=None))],
            usage=None,
        )),
    )
    retrieval = SimpleNamespace(articles_for_context=AsyncMock(return_value=([], "")))
    await ChatService(bridge=bridge, retrieval=retrieval).chat(
        ChatRequest(user_id="user-ada", session_id="session-1", customer_id="cust-ada", message="yes")
    )
    messages = bridge.create.call_args.kwargs["messages"]
    assert messages[1]["content"] == "I want Wireless Earbuds"
    assert messages[2]["content"] == "They are available for NGN 45,000."


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


@pytest.mark.asyncio
async def test_screenshot_prose_order_confirmation(monkeypatch):
    monkeypatch.setattr("app.services.chat_service.list_products", AsyncMock(return_value=[{
        "product_id": "prod-earbuds",
        "name": "Wireless Earbuds",
    }]))
    add = AsyncMock()
    checkout = AsyncMock(return_value={
        "order_id": "KORA-AYO123",
        "total_minor": 4750000,
        "payment_status": "pending",
    })
    monkeypatch.setattr("app.services.chat_service.add_to_cart", add)
    monkeypatch.setattr("app.services.chat_service.checkout", checkout)

    history = [{
        "role": "assistant",
        "content": (
            "You are ordering 1 Wireless Earbuds at NGN 45,000.00.\n"
            "The total for your order will be NGN 45,000.00.\n"
            'Please type "confirm order" to finalize your purchase.\n'
            "• Name: Ayomide\n"
            "• Email: kumba@gmail.com\n"
            "• Phone: 08198760925\n"
            "• Delivery Address: 2 maplewood estate, lagos\n"
            "• Payment Method: Bank transfer"
        ),
    }]
    answer = await confirmed_order_answer("user-ayo", "session-1", "confirm order", history)
    assert answer is not None
    assert "KORA-AYO123" in answer
    assert "Bank transfer" in answer or "bank transfer" in answer
    assert "0123456789" in answer
    add.assert_awaited_once()
    checkout.assert_awaited_once()
    chk_args = checkout.call_args.args[0]
    assert chk_args.customer_name == "Ayomide"
    assert chk_args.email == "kumba@gmail.com"
    assert chk_args.phone == "08198760925"
    assert "maplewood" in chk_args.line1.lower()
    assert chk_args.payment_method == "bank_transfer"


@pytest.mark.asyncio
async def test_cancellation_action_cancels_order(monkeypatch):
    from app.services.chat_service import cancellation_action_answer
    cancel_mock = AsyncMock(return_value={
        "order_id": "KORA-1003",
        "status": "cancelled",
        "message": "Order KORA-1003 has been cancelled successfully.",
    })
    monkeypatch.setattr("app.services.chat_service.cancel_order", cancel_mock)

    answer = await cancellation_action_answer(
        "user-ada",
        "session-1",
        "Please cancel order KORA-1003",
        [],
    )
    assert answer is not None
    assert "KORA-1003" in answer
    assert "cancelled successfully" in answer
    cancel_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_return_action_submits_return(monkeypatch):
    from app.services.chat_service import return_action_answer
    ret_mock = AsyncMock(return_value={
        "return_id": "KORA-RET-12345678",
        "refund_id": "KORA-REF-87654321",
    })
    monkeypatch.setattr("app.services.chat_service.request_return", ret_mock)

    db = FakeDB()
    db.orders.find_one = AsyncMock(return_value={
        "order_id": "KORA-1002",
        "fulfillment_status": "delivered",
        "items": [{"product_id": "prod-charger", "quantity": 1, "unit_price_minor": 1800000}],
    })
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)

    answer = await return_action_answer(
        "user-ada",
        "session-1",
        "I want to return order KORA-1002 because it is damaged",
        [],
    )
    assert answer is not None
    assert "KORA-RET-12345678" in answer
    assert "KORA-REF-87654321" in answer
    ret_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_calling_supports_mutations(monkeypatch):
    db = FakeDB(customer=True)
    monkeypatch.setattr("app.services.chat_service.get_db", lambda: db)
    tool_call = SimpleNamespace(
        id="call-cancel",
        function=SimpleNamespace(name="cancel_order", arguments='{"order_id":"KORA-1003","reason":"Changed mind"}'),
    )
    bridge = SimpleNamespace(
        catalogue_checked=True,
        tools_supported=True,
        create=AsyncMock(side_effect=[
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))], usage=None),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Your order KORA-1003 has been cancelled.", tool_calls=None))], usage=None),
        ]),
    )
    retrieval = SimpleNamespace(articles_for_context=AsyncMock(return_value=([], "")))
    cancel_mock = AsyncMock(return_value={
        "order_id": "KORA-1003",
        "status": "cancelled",
        "message": "Order KORA-1003 cancelled.",
    })
    monkeypatch.setattr("app.services.chat_service.cancel_order", cancel_mock)

    response = await ChatService(bridge=bridge, retrieval=retrieval).chat(
        ChatRequest(user_id="user-ada", customer_id="cust-ada", message="Please cancel KORA-1003")
    )
    assert response.tool_used == "cancel_order"
    assert "cancelled" in response.answer.lower()

