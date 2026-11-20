import json
import hashlib
import re
from typing import Any

from pydantic import ValidationError

from app.db import get_db
from app.models import CartItemRequest, ChatRequest, ChatResponse, CheckoutRequest, Citation, OrderStatusArgs, ReturnRequest
from app.prompts import (
    ALL_TOOLS,
    ORDER_TOOL,
    RETURN_TOOL,
    REFUND_TOOL,
    PLACE_ORDER_TOOL,
    CANCEL_ORDER_TOOL,
    REQUEST_RETURN_TOOL,
    REQUEST_REFUND_TOOL,
    SYSTEM_INSTRUCTION,
)
from app.services.commerce_service import (
    add_to_cart,
    cancel_order,
    checkout,
    create_refund_request,
    list_products,
    order_detail,
    refund_detail,
    request_return,
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

        history_messages = (existing_session or {}).get("messages", [])

        # 1. Order confirmation path (works for all users upon confirmation)
        confirmed_order = await confirmed_order_answer(
            request.user_id,
            session_id,
            request.message,
            history_messages,
        )
        if confirmed_order is not None:
            await persist_messages(db, session_id, request.user_id, request.message, confirmed_order)
            return ChatResponse(session_id=session_id, user_id=request.user_id, answer=confirmed_order, citations=[])

        # Local action and status handlers for direct browser users
        if request.customer_id is None:
            # 2. Return action path
            return_action = await return_action_answer(
                request.user_id,
                session_id,
                request.message,
                history_messages,
            )
            if return_action is not None:
                await persist_messages(db, session_id, request.user_id, request.message, return_action)
                return ChatResponse(session_id=session_id, user_id=request.user_id, answer=return_action, citations=[])

            # 3. Cancellation action path
            cancellation_action = await cancellation_action_answer(
                request.user_id,
                session_id,
                request.message,
                history_messages,
            )
            if cancellation_action is not None:
                await persist_messages(db, session_id, request.user_id, request.message, cancellation_action)
                return ChatResponse(session_id=session_id, user_id=request.user_id, answer=cancellation_action, citations=[])

            # 4. Refund action path
            refund_action = await refund_action_answer(
                request.user_id,
                session_id,
                request.message,
                history_messages,
            )
            if refund_action is not None:
                await persist_messages(db, session_id, request.user_id, request.message, refund_action)
                return ChatResponse(session_id=session_id, user_id=request.user_id, answer=refund_action, citations=[])

            # 5. Local commerce status query (order/return/refund status lookups)
            local_answer = await local_commerce_answer(request.user_id, request.message)
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
            for item in history_messages
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
            tools_to_pass = ALL_TOOLS if getattr(self.bridge, "tools_supported", False) else None
            response = await self.bridge.create(
                messages=messages,
                tools=tools_to_pass,
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
            if getattr(tool_call, "function", None) is None:
                continue

            name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
            except (json.JSONDecodeError, ValueError):
                arguments = {}

            tool_result = {"found": False, "message": "The requested action could not be completed."}
            try:
                if name == "get_order_status":
                    parsed = OrderStatusArgs.model_validate(arguments)
                    tool_result = await get_order_status(request.customer_id, parsed) if request.customer_id else {"found": False, "message": "Order status requires an authenticated customer account."}
                    tool_used = name
                elif name == "get_return_status":
                    tool_result = await return_detail(request.user_id, str(arguments.get("return_id", ""))) or {"found": False, "message": "No matching return record found."}
                    tool_used = name
                elif name == "get_refund_status":
                    tool_result = await refund_detail(request.user_id, str(arguments.get("refund_id", ""))) or {"found": False, "message": "No matching refund record found."}
                    tool_used = name
                elif name == "place_order":
                    prod_name = arguments.get("product_name", "")
                    products = await list_products()
                    prod = next((p for p in products if p.get("name", "").casefold() == prod_name.strip().casefold()), None)
                    if not prod:
                        tool_result = {"success": False, "message": f"Product '{prod_name}' is unavailable."}
                    else:
                        qty = int(arguments.get("quantity", 1))
                        await add_to_cart(request.user_id, CartItemRequest(product_id=prod["product_id"], quantity=qty))
                        addr = str(arguments.get("delivery_address", ""))
                        city, state = split_address(addr)
                        pm = normalize_payment_method(arguments.get("payment_method", "mock_card")) or "mock_card"
                        idemp = hashlib.sha256(f"{session_id}:{prod['product_id']}:{qty}".encode()).hexdigest()
                        ord_res = await checkout(CheckoutRequest(
                            user_id=request.user_id,
                            session_id=session_id,
                            idempotency_key=idemp,
                            customer_name=str(arguments.get("customer_name", "Customer")),
                            email=arguments.get("email"),
                            phone=arguments.get("phone"),
                            line1=addr,
                            city=city,
                            state=state,
                            payment_method=pm,
                        ))
                        tool_result = {
                            "success": True,
                            "order_id": ord_res["order_id"],
                            "total_minor": ord_res["total_minor"],
                            "payment_status": ord_res["payment_status"],
                            "fulfillment_status": ord_res["fulfillment_status"],
                        }
                    tool_used = name
                elif name == "cancel_order":
                    res = await cancel_order(request.user_id, str(arguments.get("order_id", "")), reason=arguments.get("reason", "Cancelled by customer"))
                    tool_result = {"success": True, "order_id": res["order_id"], "message": res["message"]}
                    tool_used = name
                elif name == "request_return":
                    res = await request_return(str(arguments.get("order_id", "")), ReturnRequest(
                        user_id=request.user_id,
                        items=[{"product_id": "item", "quantity": 1}],
                        reason=arguments.get("reason", "unwanted"),
                        reason_details=arguments.get("reason_details"),
                    ))
                    tool_result = {"success": True, "return_id": res["return_id"], "refund_id": res.get("refund_id")}
                    tool_used = name
                elif name == "request_refund":
                    res = await create_refund_request(
                        request.user_id,
                        order_id=arguments.get("order_id"),
                        return_id=arguments.get("return_id"),
                        reason=arguments.get("reason", ""),
                    )
                    tool_result = {"success": True, "refund_id": res.get("refund_id"), "status": res.get("status")}
                    tool_used = name
            except Exception as exc:
                tool_result = {"success": False, "message": str(exc)}

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
    return bool(
        re.search(r"\b(return|refund)\b", message, re.I)
        and re.search(r"\b(status|track|tracking|update|progress|where|when)\b", message, re.I)
    )


def is_product_question(message: str) -> bool:
    return bool(re.search(r"\b(product|products|catalog|catalogue|available|stock|price|cost|buy|purchase|sell)\b", message, re.I))


def is_action_continuation(message: str) -> bool:
    return bool(
        re.search(
            r"\b(confirm|proceed|place order|confirm order|go ahead|yes|yeah|yep|sure|that's right|finalize|submit|buy now)\b",
            message,
            re.I,
        )
    )


def extract_order_info(transcript: str, products: list[dict]) -> dict[str, Any]:
    info: dict[str, Any] = {}

    # 1. Product matching
    prod_bullet = re.search(
        r"(?im)^\s*[•*-]?\s*\*{0,2}(?:Product|Item)(?:\s+Name)?\*{0,2}\s*:\s*(.+?)\s*$",
        transcript,
    )
    if prod_bullet:
        bullet_name = prod_bullet.group(1).strip().strip("* ").casefold()
        info["product"] = next((p for p in products if p.get("name", "").casefold() == bullet_name), None)

    if not info.get("product"):
        for product in products:
            p_name = product.get("name", "")
            if p_name and re.search(rf"\b{re.escape(p_name)}\b", transcript, re.I):
                info["product"] = product
                break

    # 2. Quantity
    qty_bullet = re.search(
        r"(?im)^\s*[•*-]?\s*\*{0,2}(?:Quantity|Qty)\*{0,2}\s*:\s*(\d+)",
        transcript,
    )
    if qty_bullet:
        info["quantity"] = int(qty_bullet.group(1))
    elif info.get("product"):
        p_name = info["product"]["name"]
        qty_match = (
            re.search(rf"(?i)\bordering\s+(\d+)\s+{re.escape(p_name)}", transcript)
            or re.search(rf"(?i)\b(\d+)\s+(?:piece|pieces|unit|units|x\s+)?{re.escape(p_name)}", transcript)
            or re.search(r"(?i)\bordering\s+(\d+)\b", transcript)
        )
        info["quantity"] = int(qty_match.group(1)) if qty_match else 1
    else:
        info["quantity"] = 1

    # 3. Customer name
    name_match = re.search(
        r"(?im)^\s*[•*-]?\s*\*{0,2}(?:Customer\s+)?(?:Full\s+)?Name\*{0,2}\s*:\s*([^\n•]+)",
        transcript,
    )
    if name_match:
        info["customer_name"] = name_match.group(1).strip().strip("* ")

    # 4. Email
    email_match = re.search(
        r"(?im)^\s*[•*-]?\s*\*{0,2}Email(?:\s+Address)?\*{0,2}\s*:\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
        transcript,
    ) or re.search(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        transcript,
    )
    if email_match:
        info["email"] = email_match.group(1 if email_match.lastindex else 0).strip()

    # 5. Phone
    phone_match = re.search(
        r"(?im)^\s*[•*-]?\s*\*{0,2}(?:Phone(?:\s*Number)?|Mobile)\*{0,2}\s*:\s*([^\n•]+)",
        transcript,
    ) or re.search(
        r"\b(?:\+?234|0)[789][01]\d{8}\b",
        transcript,
    )
    if phone_match:
        info["phone"] = phone_match.group(1 if phone_match.lastindex else 0).strip().strip("* ")

    # 6. Delivery address
    addr_match = re.search(
        r"(?im)^\s*[•*-]?\s*\*{0,2}(?:Delivery\s+|Shipping\s+)?Address\*{0,2}\s*:\s*([^\n•]+)",
        transcript,
    )
    if addr_match:
        info["address"] = addr_match.group(1).strip().strip("* ")

    # 7. Payment method
    payment_match = re.search(
        r"(?im)^\s*[•*-]?\s*\*{0,2}Payment(?:\s*Method)?\*{0,2}\s*:\s*([^\n•]+)",
        transcript,
    )
    if payment_match:
        info["payment_method"] = normalize_payment_method(payment_match.group(1))
    elif "bank transfer" in transcript.lower() or "bank" in transcript.lower():
        info["payment_method"] = "bank_transfer"
    elif "link" in transcript.lower():
        info["payment_method"] = "payment_link"
    else:
        info["payment_method"] = "mock_card"

    return info


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
    has_order_intent = bool(
        re.search(r"\b(order summary|ordering|confirm order|finalize your purchase|proceed with your order)\b", transcript, re.I)
        or re.search(r"\b(confirm order)\b", message, re.I)
    )
    if not has_order_intent:
        return None

    products = await list_products()
    info = extract_order_info(transcript, products)

    product = info.get("product")
    quantity = info.get("quantity", 1)
    customer_name = info.get("customer_name")
    address = info.get("address")
    phone = info.get("phone")
    email = info.get("email")
    payment_method = info.get("payment_method") or "mock_card"

    missing = []
    if not product:
        missing.append("product")
    if not customer_name:
        missing.append("full name")
    if not address:
        missing.append("delivery address")
    if missing:
        return f"I still need your {', '.join(missing)} before I can place the order."

    city, state = split_address(address)
    idempotency_key = hashlib.sha256(
        f"{session_id}:{product['product_id']}:{quantity}:{email or phone or customer_name}".encode()
    ).hexdigest()

    try:
        await add_to_cart(user_id, CartItemRequest(product_id=product["product_id"], quantity=quantity))
        order = await checkout(CheckoutRequest(
            user_id=user_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            customer_name=customer_name,
            email=email,
            phone=phone,
            line1=address,
            city=city,
            state=state,
            payment_method=payment_method,
        ))
    except ValueError as exc:
        return f"I could not place the order: {exc}"

    if order["payment_status"] == "paid":
        return f"Order {order['order_id']} was placed successfully and payment was confirmed. You can ask me for its status anytime."

    if payment_method == "bank_transfer":
        total_ngn = f"NGN {order.get('total_minor', 0) / 100:,.2f}"
        return (
            f"Order {order['order_id']} was created and is awaiting payment via bank transfer. "
            f"Please transfer {total_ngn} to:\n"
            f"• **Bank:** Kora Demo Bank\n"
            f"• **Account Name:** Kora Electronics Retail Ltd\n"
            f"• **Account Number:** 0123456789\n"
            f"• **Reference:** {order['order_id']}\n\n"
            f"Your order will be processed as soon as payment is confirmed."
        )

    return f"Order {order['order_id']} was created and is awaiting payment via {payment_method.replace('_', ' ')}. It will proceed after payment is confirmed."


async def return_action_answer(
    user_id: str,
    session_id: str,
    message: str,
    history: list[dict[str, Any]],
) -> str | None:
    if re.search(r"\b(policy|policies|terms|condition|hours|time|faq)\b", message, re.I):
        return None
    is_return_mention = bool(re.search(r"\b(i want to return|please return|return this|return order|returning order|can i return|return my)\b", message, re.I))
    is_status_only = is_return_or_refund_status_request(message)
    is_confirm = is_action_continuation(message)
    transcript = "\n".join(str(item.get("content", "")) for item in history if item.get("role") in {"user", "assistant"})
    transcript_has_return = bool(re.search(r"\b(return|returning)\b", transcript, re.I))

    if not (is_return_mention and not is_status_only) and not (is_confirm and transcript_has_return):
        return None

    order_id = next(
        (token.upper() for token in re.findall(r"KORA-[A-Z0-9-]+", message + " " + transcript, re.I)),
        None,
    )
    db = get_db()
    if not order_id:
        orders = await user_orders(user_id)
        delivered_orders = [o for o in orders if (o.get("fulfillment_status") or o.get("status", "")).lower() == "delivered"]
        if len(delivered_orders) == 1:
            order_id = delivered_orders[0]["order_id"]
        elif delivered_orders:
            order_list = ", ".join(o["order_id"] for o in delivered_orders[:3])
            return f"You have eligible delivered orders ({order_list}). Which order would you like to return, and what is the reason?"
        else:
            return "You do not have any delivered orders eligible for return at this time."

    reason = "unwanted"
    for candidate in ["damaged", "wrong_item", "faulty", "unwanted", "other"]:
        if candidate.replace("_", " ") in message.lower() or candidate in message.lower():
            reason = candidate
            break
    if "damage" in message.lower():
        reason = "damaged"

    try:
        order = await db.orders.find_one({"order_id": order_id})
        items = []
        if order and order.get("items"):
            for it in order["items"]:
                items.append({
                    "product_id": it.get("product_id") or it.get("name"),
                    "name": it.get("name") or it.get("name_snapshot", "Item"),
                    "quantity": it.get("quantity", 1),
                })
        else:
            items.append({"product_id": "default", "quantity": 1})

        ret = await request_return(order_id, ReturnRequest(
            user_id=user_id,
            items=items,
            reason=reason,
            reason_details=message,
        ))
        return (
            f"Return request {ret['return_id']} was submitted for order {order_id} (Reason: {reason.replace('_', ' ')}). "
            f"A refund request ({ret.get('refund_id')}) has been initiated and will be processed to your original payment method once the returned item is inspected."
        )
    except ValueError as exc:
        return f"Return could not be processed: {exc}"


async def cancellation_action_answer(
    user_id: str,
    session_id: str,
    message: str,
    history: list[dict[str, Any]],
) -> str | None:
    if re.search(r"\b(policy|policies|terms|condition|faq)\b", message, re.I):
        return None
    is_cancel = bool(re.search(r"\b(cancel|cancellation)\b", message, re.I))
    is_confirm = is_action_continuation(message)
    transcript = "\n".join(str(item.get("content", "")) for item in history if item.get("role") in {"user", "assistant"})
    transcript_has_cancel = bool(re.search(r"\b(cancel|cancellation)\b", transcript, re.I))

    if not is_cancel and not (is_confirm and transcript_has_cancel):
        return None

    order_id = next(
        (token.upper() for token in re.findall(r"KORA-[A-Z0-9-]+", message + " " + transcript, re.I)),
        None,
    )
    if not order_id:
        orders = await user_orders(user_id)
        cancellable = [o for o in orders if (o.get("fulfillment_status") or o.get("status", "")).lower() in {"pending", "processing", "awaiting_payment"}]
        if len(cancellable) == 1:
            order_id = cancellable[0]["order_id"]
        elif cancellable:
            order_list = ", ".join(o["order_id"] for o in cancellable[:3])
            return f"You have orders that can be cancelled ({order_list}). Please confirm which order you would like to cancel."
        else:
            return "I could not find any active, unshipped orders that can be cancelled."

    try:
        res = await cancel_order(user_id, order_id, reason=message)
        return res["message"]
    except ValueError as exc:
        return f"Order cancellation could not be completed: {exc}"


async def refund_action_answer(
    user_id: str,
    session_id: str,
    message: str,
    history: list[dict[str, Any]],
) -> str | None:
    if not re.search(r"\brefund\b", message, re.I):
        return None
    if is_return_or_refund_status_request(message):
        return None

    transcript = "\n".join(str(item.get("content", "")) for item in history if item.get("role") in {"user", "assistant"})
    order_id = next(
        (token.upper() for token in re.findall(r"KORA-[A-Z0-9-]+", message + " " + transcript, re.I)),
        None,
    )
    return_id = next(
        (token.upper() for token in re.findall(r"KORA-RET-[A-Z0-9-]+", message + " " + transcript, re.I)),
        None,
    )
    db = get_db()
    if return_id:
        refund = await db.refunds.find_one({"return_id": return_id}, {"_id": 0})
        if refund:
            return f"Refund {refund['refund_id']} for return {return_id} is currently {refund.get('status', 'requested')}. Approved refunds take 5-10 business days for cards or 2-3 business days for bank transfers."
    if order_id:
        order = await db.orders.find_one({"order_id": order_id})
        if order:
            st = (order.get("fulfillment_status") or order.get("status", "")).lower()
            if st == "delivered":
                return f"Order {order_id} has been delivered. Per Kora Electronics policy, please submit a return request for the item first so we can arrange inspection and process your refund."
            elif st in {"processing", "pending", "awaiting_payment"}:
                try:
                    res = await cancel_order(user_id, order_id, reason="Customer requested refund before dispatch")
                    return res["message"]
                except ValueError as exc:
                    return str(exc)
    return None


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
