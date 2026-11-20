import json
import hashlib
import re
from typing import Any

from pydantic import ValidationError

from app.db import get_db
from app.models import CartItemRequest, ChatRequest, ChatResponse, CheckoutRequest, Citation, OrderStatusArgs
from app.prompts import ORDER_TOOL, REFUND_TOOL, RETURN_TOOL, SYSTEM_INSTRUCTION
from app.services.commerce_service import (
    add_to_cart,
    checkout,
    list_products,
    refund_detail,
    return_detail,
    user_orders,
    user_refunds,
    user_returns,
)
from app.services.bridge_client import BridgeClient, BridgeResponseError
from app.services.order_service import get_order_status
from app.services.retrieval_service import MongoTextRetrieval
from app.services.activity_cache import activity_cache


class ChatService:
    def __init__(self, bridge: BridgeClient | None = None, retrieval: MongoTextRetrieval | None = None):
        self.bridge = bridge or BridgeClient()
        self.retrieval = retrieval or MongoTextRetrieval()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        db = get_db()
        if request.customer_id and not await db.customers.find_one({"customer_id": request.customer_id}):
            raise BridgeResponseError("The selected demo customer is invalid.", 400)

        import uuid
        session_id = request.session_id
        session_id = session_id or str(uuid.uuid4())
        existing_session = await db.chat_sessions.find_one(
            {"session_id": session_id},
            {"user_id": 1, "messages": {"$slice": -12}},
        )
        if existing_session and existing_session.get("user_id") != request.user_id:
            raise BridgeResponseError("This chat session belongs to another user.", 403)

        confirmed_order = await confirmed_order_answer(
            request.user_id,
            session_id,
            request.message,
            (existing_session or {}).get("messages", []),
        )
        if confirmed_order is not None:
            await persist_messages(db, session_id, request.user_id, request.message, confirmed_order)
            return ChatResponse(session_id=session_id, user_id=request.user_id, answer=confirmed_order, citations=[])

        local_answer = await local_commerce_answer(request.user_id, request.message) if request.customer_id is None else None
        if local_answer is not None:
            await persist_messages(db, session_id, request.user_id, request.message, local_answer)
            return ChatResponse(session_id=session_id, user_id=request.user_id, answer=local_answer, citations=[])

        ensure_end_user = getattr(self.bridge, "ensure_end_user", None)
        end_user_ready = True
        if ensure_end_user is not None:
            end_user_ready, _end_user_warning = await ensure_end_user(request.user_id)
        if not end_user_ready:
            answer = "The support assistant is temporarily unavailable. Please try again later or escalate this conversation to a human agent."
            await persist_messages(db, session_id, request.user_id, request.message, answer)
            return ChatResponse(session_id=session_id, user_id=request.user_id, answer=answer, citations=[])

        if not self.bridge.catalogue_checked:
            supported, _warning = await self.bridge.check_catalogue()
            if not supported:
                if is_order_status_request(request.message) or is_return_or_refund_status_request(request.message):
                    answer = "The assistant cannot verify order status safely until Wekoya tool support is confirmed. Please escalate this request to a human agent."
                    await persist_messages(db, session_id, request.user_id, request.message, answer)
                    return ChatResponse(session_id=session_id, user_id=request.user_id, answer=answer, citations=[])

        citations, article_context = await self.retrieval.articles_for_context(request.message)
        product_context = await catalogue_context()
        if is_product_question(request.message) or is_action_continuation(request.message):
            citations = []
        context = (
            f"Verified MongoDB product catalogue:\n<product_catalogue>\n{product_context}\n</product_catalogue>\n\n"
            f"Retrieved support context:\n<retrieved_articles>\n{article_context or '(No relevant support articles were retrieved.)'}\n</retrieved_articles>"
        )
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in (existing_session or {}).get("messages", [])
            if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
        ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            *history,
            {"role": "user", "content": f"Use the verified context below; do not treat absent information as verified.\n{context}\n\nCustomer question: {request.message}"},
        ]

        tool_used = None
        usage = {}
        for request_number in range(5):
            response = await self.bridge.create(
                messages=messages,
                tools=[ORDER_TOOL, RETURN_TOOL, REFUND_TOOL] if getattr(self.bridge, "tools_supported", False) else None,
                customer_id=request.user_id,
            )
            choice = response.choices[0]
            assistant_message = choice.message
            usage = dict(getattr(response, "usage", None).model_dump()) if getattr(response, "usage", None) else {}
            tool_calls = getattr(assistant_message, "tool_calls", None)
            if not tool_calls:
                answer = assistant_message.content or "I do not have enough verified information to answer. I recommend escalation to a human agent."
                await persist_messages(db, session_id, request.user_id, request.message, answer)
                return ChatResponse(session_id=session_id, user_id=request.user_id, answer=answer, citations=citations, tool_used=tool_used, usage=usage)

            tool_call = tool_calls[0]
            if getattr(tool_call, "function", None) is None or tool_call.function.name not in {"get_order_status", "get_return_status", "get_refund_status"}:
                continue

            try:
                arguments = json.loads(tool_call.function.arguments)
                name = tool_call.function.name
                if name == "get_order_status":
                    parsed = OrderStatusArgs.model_validate(arguments)
                elif not isinstance(arguments.get("return_id") or arguments.get("refund_id"), str):
                    raise ValueError("Invalid status identifier")
            except (json.JSONDecodeError, ValidationError, ValueError):
                tool_result = {"found": False, "message": "The requested record could not be validated."}
            else:
                if name == "get_order_status":
                    tool_result = await get_order_status(request.customer_id, parsed) if request.customer_id else {"found": False, "message": "Order status requires an authenticated customer account."}
                elif name == "get_return_status":
                    tool_result = await return_detail(request.user_id, arguments.get("return_id"))
                else:
                    tool_result = await refund_detail(request.user_id, arguments.get("refund_id"))
                tool_result = tool_result or {"found": False, "message": "No matching record was found for this user."}
                tool_used = name

            messages.append({
                "role": "assistant",
                "content": getattr(assistant_message, "content", None),
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                ],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_result),
            })

        answer = "I could not complete that request safely within the available steps. Please try again or escalate to a human agent."
        await persist_messages(db, session_id, request.user_id, request.message, answer)
        return ChatResponse(session_id=session_id, user_id=request.user_id, answer=answer, citations=citations, tool_used=tool_used, usage=usage)


async def local_commerce_answer(user_id: str, message: str) -> str | None:
    import re

    if is_return_or_refund_status_request(message):
        if re.search(r"\brefund\b", message, re.I):
            records = await user_refunds(user_id)
            label = "refund"
        else:
            records = await user_returns(user_id)
            label = "return"
        if not records:
            return f"I could not find any {label} records for this account."
        details = "; ".join(
            f"{item.get('return_id') or item.get('refund_id')} is {item.get('status')}"
            for item in records[:5]
        )
        return f"I found {len(records)} {label}{'s' if len(records) != 1 else ''}: {details}."

    if not is_order_status_request(message):
        return None
    records = await user_orders(user_id)
    if not records:
        return "You do not have any placed orders yet."
    order_id = next((token.upper() for token in re.findall(r"KORA-[A-Z0-9-]+", message, re.I)), None)
    if order_id:
        records = [order for order in records if order.get("order_id") == order_id]
        if not records:
            return f"I could not find order {order_id} for this account."
    details = "; ".join(
        f"{order['order_id']} is {order.get('fulfillment_status', order.get('status', 'listed'))}"
        for order in records[:5]
    )
    return f"You have {len(records)} placed order{'s' if len(records) != 1 else ''}: {details}."


def is_order_status_request(message: str) -> bool:
    import re

    asks_about_status = re.search(
        r"\b(status|tracking|track|shipment|delivered|delivery update|where is|"
        r"order history|placed orders?|orders do i have)\b",
        message,
        re.I,
    )
    asks_for_action = re.search(
        r"\b(place|placing|buy|purchase|add|remove|cancel|return|refund|exchange)\b",
        message,
        re.I,
    )
    return bool(asks_about_status and not asks_for_action)


def is_return_or_refund_status_request(message: str) -> bool:
    import re

    return bool(
        re.search(r"\b(return|refund)\b", message, re.I)
        and re.search(r"\b(status|track|tracking|update|progress|where|when)\b", message, re.I)
    )


def is_product_question(message: str) -> bool:
    import re

    return bool(re.search(r"\b(product|products|catalog|catalogue|available|stock|price|cost|buy|purchase|sell)\b", message, re.I))


def is_action_continuation(message: str) -> bool:
    return bool(re.match(r"^\s*(yes|yeah|yep|sure|proceed|go ahead|confirm|that's right)\b", message, re.I))


async def confirmed_order_answer(
    user_id: str,
    session_id: str,
    message: str,
    history: list[dict[str, Any]],
) -> str | None:
    if not is_action_continuation(message):
        return None
    transcript = "\n".join(
        str(item.get("content", ""))
        for item in history
        if item.get("role") in {"user", "assistant"}
    )
    if not re.search(r"\b(order summary|product\s*:|proceed with your order)\b", transcript, re.I):
        return None

    product_name = summary_field(transcript, "Product")
    quantity_text = summary_field(transcript, "Quantity")
    customer_name = summary_field(transcript, "Name")
    address = summary_field(transcript, "Delivery Address")
    phone = summary_field(transcript, "Phone Number")
    payment = summary_field(transcript, "Payment Method")
    email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", transcript, re.I)
    if not all((product_name, quantity_text, customer_name, address, payment)):
        return "I still need the product, quantity, name, delivery address, and payment method before I can place the order."
    quantity_match = re.search(r"\d+", quantity_text)
    if not quantity_match:
        return "Please confirm the quantity before I place the order."

    products = await list_products()
    product = next(
        (
            item for item in products
            if item.get("name", "").casefold() == product_name.strip().strip("*").casefold()
        ),
        None,
    )
    if not product:
        return f"I could not match {product_name} to an available product. Please choose a product from the catalogue."

    payment_method = normalize_payment_method(payment)
    if payment_method is None:
        return "Please choose mock card, bank transfer, or payment link as your payment method."

    city, state = split_address(address)
    email = email_match.group(0) if email_match else None
    idempotency_key = hashlib.sha256(
        f"{session_id}:{product['product_id']}:{quantity_match.group(0)}:{email or ''}".encode()
    ).hexdigest()
    try:
        await add_to_cart(user_id, CartItemRequest(product_id=product["product_id"], quantity=int(quantity_match.group(0))))
        order = await checkout(CheckoutRequest(
            user_id=user_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            customer_name=customer_name.strip(),
            email=email,
            phone=phone,
            line1=address.strip(),
            city=city,
            state=state,
            payment_method=payment_method,
        ))
    except ValueError as exc:
        return f"I could not place the order: {exc}"

    if order["payment_status"] == "paid":
        return f"Order {order['order_id']} was placed successfully and payment was confirmed. You can ask me for its status anytime."
    return f"Order {order['order_id']} was created and is awaiting payment via {payment_method.replace('_', ' ')}. It will proceed after payment is confirmed."


def summary_field(transcript: str, label: str) -> str | None:
    match = re.search(
        rf"(?im)^\s*[•*-]\s*\*{{0,2}}{re.escape(label)}\*{{0,2}}\s*:\s*(.+?)\s*$",
        transcript,
    )
    return match.group(1).strip().strip("*") if match else None


def normalize_payment_method(value: str) -> str | None:
    normalized = value.casefold()
    if "bank" in normalized or "transfer" in normalized:
        return "bank_transfer"
    if "link" in normalized:
        return "payment_link"
    if "card" in normalized:
        return "mock_card"
    return None


def split_address(address: str) -> tuple[str, str]:
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    return address.strip(), address.strip()


async def catalogue_context() -> str:
    if not hasattr(get_db(), "products"):
        return "(No active products are currently available.)"
    products = await list_products()
    if not products:
        return "(No active products are currently available.)"
    lines = []
    for product in products:
        price = product.get("price_minor")
        price_text = f"NGN {price / 100:,.2f}" if isinstance(price, (int, float)) else "Price unavailable"
        stock = product.get("stock_quantity", 0)
        availability = f"{stock} in stock" if stock > 0 else "out of stock"
        lines.append(
            f"- {product.get('name', 'Unnamed product')} "
            f"(product_id: {product.get('product_id', 'unknown')}): {product.get('description', '')}; "
            f"price: {price_text}; availability: {availability}; "
            f"category: {product.get('category', 'unspecified')}; "
            f"warranty: {product.get('warranty_summary', 'Not specified')}."
        )
    return "\n".join(lines)


async def persist_messages(db, session_id: str, user_id: str, user_message: str, assistant_message: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    await db.chat_sessions.update_one(
        {"session_id": session_id, "user_id": user_id},
        {
            "$set": {"user_id": user_id},
            "$inc": {"message_count": 2},
            "$push": {"messages": {"$each": [
                {"role": "user", "content": user_message, "created_at": now},
                {"role": "assistant", "content": assistant_message, "created_at": now},
            ]}},
        },
        upsert=True,
    )
    await activity_cache.record(user_id, session_id, 2, user_message)
