import json
from typing import Any

from pydantic import ValidationError

from app.db import get_db
from app.models import ChatRequest, ChatResponse, Citation, OrderStatusArgs
from app.prompts import SYSTEM_INSTRUCTION, ORDER_TOOL
from app.services.bridge_client import BridgeClient, BridgeResponseError
from app.services.order_service import get_order_status
from app.services.retrieval_service import MongoTextRetrieval


class ChatService:
    def __init__(self, bridge: BridgeClient | None = None, retrieval: MongoTextRetrieval | None = None):
        self.bridge = bridge or BridgeClient()
        self.retrieval = retrieval or MongoTextRetrieval()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        db = get_db()
        customer = await db.customers.find_one({"customer_id": request.customer_id})
        if not customer:
            raise BridgeResponseError("The selected demo customer is invalid.", 400)

        import uuid
        session_id = request.session_id
        session_id = session_id or str(uuid.uuid4())

        citations, article_context = await self.retrieval.articles_for_context(request.message)
        context = article_context or "(No relevant support articles were retrieved.)"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": f"Retrieved support context (use only this context):\n<retrieved_articles>\n{context}\n</retrieved_articles>\n\nCustomer question: {request.message}"},
        ]

        if not self.bridge.catalogue_checked:
            supported, warning = await self.bridge.check_catalogue()
            if not supported:
                answer = f"I cannot safely answer that yet because of a configuration issue: {warning} You can escalate this conversation to a human agent."
                await persist_messages(db, session_id, request.customer_id, request.message, answer)
                return ChatResponse(session_id=session_id, answer=answer, citations=citations)

        tool_used = None
        usage = {}
        for request_number in range(5):
            response = await self.bridge.create(
                messages=messages,
                tools=[ORDER_TOOL],
                customer_id=request.customer_id,
            )
            choice = response.choices[0]
            assistant_message = choice.message
            usage = dict(getattr(response, "usage", None).model_dump()) if getattr(response, "usage", None) else {}
            tool_calls = getattr(assistant_message, "tool_calls", None)
            if not tool_calls:
                answer = assistant_message.content or "I do not have enough verified information to answer. I recommend escalation to a human agent."
                await persist_messages(db, session_id, request.customer_id, request.message, answer)
                return ChatResponse(session_id=session_id, answer=answer, citations=citations, tool_used=tool_used, usage=usage)

            tool_call = tool_calls[0]
            if getattr(tool_call, "function", None) is None or tool_call.function.name != "get_order_status":
                continue

            try:
                arguments = json.loads(tool_call.function.arguments)
                parsed = OrderStatusArgs.model_validate(arguments)
            except (json.JSONDecodeError, ValidationError):
                tool_result = {"found": False, "message": "The order ID could not be validated."}
            else:
                tool_result = await get_order_status(request.customer_id, parsed)
                tool_used = "get_order_status"

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
        await persist_messages(db, session_id, request.customer_id, request.message, answer)
        return ChatResponse(session_id=session_id, answer=answer, citations=citations, tool_used=tool_used, usage=usage)


async def persist_messages(db, session_id: str, customer_id: str, user_message: str, assistant_message: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    await db.chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {"customer_id": customer_id},
            "$push": {"messages": {"$each": [
                {"role": "user", "content": user_message, "created_at": now},
                {"role": "assistant", "content": assistant_message, "created_at": now},
            ]}},
        },
        upsert=True,
    )
