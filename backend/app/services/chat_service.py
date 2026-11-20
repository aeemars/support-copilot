import json
from typing import Any

from pydantic import ValidationError

from app.db import get_db
from app.models import ChatRequest, ChatResponse, Citation, OrderStatusArgs
from app.prompts import ORDER_TOOL, REFUND_TOOL, RETURN_TOOL, SYSTEM_INSTRUCTION
from app.services.commerce_service import list_products, refund_detail, return_detail, user_orders, user_refunds, user_returns
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
        existing_session = await db.chat_sessions.find_one({"session_id": session_id}, {"user_id": 1})
        if existing_session and existing_session.get("user_id") != request.user_id:
            raise BridgeResponseError("This chat session belongs to another user.", 403)

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
                import re
                if re.search(r"\b(order|tracking|shipment|where is|status)\b", request.message, re.I):
                    answer = "The assistant cannot verify order status safely until Wekoya tool support is confirmed. Please escalate this request to a human agent."
                    await persist_messages(db, session_id, request.user_id, request.message, answer)
                    return ChatResponse(session_id=session_id, user_id=request.user_id, answer=answer, citations=[])

        citations, article_context = await self.retrieval.articles_for_context(request.message)
        product_context = await catalogue_context()
        if is_product_question(request.message):
            citations = []
        context = (
            f"Verified MongoDB product catalogue:\n<product_catalogue>\n{product_context}\n</product_catalogue>\n\n"
            f"Retrieved support context:\n<retrieved_articles>\n{article_context or '(No relevant support articles were retrieved.)'}\n</retrieved_articles>"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
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

    if re.search(r"\b(return|refund)\b", message, re.I):
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

    if not re.search(r"\b(order|tracking|shipment|placed order|where is)\b", message, re.I):
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


def is_product_question(message: str) -> bool:
    import re

    return bool(re.search(r"\b(product|products|catalog|catalogue|available|stock|price|cost|buy|purchase|sell)\b", message, re.I))


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
