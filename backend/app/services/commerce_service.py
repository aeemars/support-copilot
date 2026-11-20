from datetime import datetime, timezone
from uuid import uuid4

from app.db import get_db
from app.models import CartItemRequest, CheckoutRequest, ReturnRequest


def now():
    return datetime.now(timezone.utc)


async def list_products():
    return await get_db().products.find({"status": {"$ne": "archived"}}, {"_id": 0}).to_list(length=100)


async def product_detail(product_id: str):
    return await get_db().products.find_one({"product_id": product_id, "status": {"$ne": "archived"}}, {"_id": 0})


async def get_cart(user_id: str):
    cart = await get_db().carts.find_one({"user_id": user_id}, {"_id": 0})
    return cart or {"cart_id": f"CART-{uuid4().hex[:10].upper()}", "user_id": user_id, "items": [], "currency": "NGN"}


async def add_to_cart(user_id: str, item: CartItemRequest):
    db = get_db()
    product = await db.products.find_one({"product_id": item.product_id, "status": "active"}, {"_id": 0})
    if not product:
        raise ValueError("Product is unavailable.")
    cart = await get_cart(user_id)
    current = next((entry for entry in cart["items"] if entry["product_id"] == item.product_id), None)
    quantity = (current["quantity"] if current else 0) + item.quantity
    if quantity > product["stock_quantity"]:
        raise ValueError("Requested quantity exceeds available stock.")
    items = [entry for entry in cart["items"] if entry["product_id"] != item.product_id]
    items.append({"product_id": item.product_id, "quantity": quantity, "unit_price_minor": product["price_minor"]})
    await db.carts.update_one(
        {"user_id": user_id},
        {"$set": {"cart_id": cart["cart_id"], "user_id": user_id, "items": items, "currency": "NGN", "updated_at": now()}},
        upsert=True,
    )
    return await get_cart(user_id)


async def remove_from_cart(user_id: str, product_id: str):
    db = get_db()
    await db.carts.update_one({"user_id": user_id}, {"$pull": {"items": {"product_id": product_id}}, "$set": {"updated_at": now()}})
    return await get_cart(user_id)


async def checkout(request: CheckoutRequest) -> dict:
    db = get_db()
    cart = await db.carts.find_one({"user_id": request.user_id})
    if not cart or not cart.get("items"):
        raise ValueError("Your cart is empty.")
    order_items = []
    subtotal = 0
    for item in cart["items"]:
        product = await db.products.find_one({"product_id": item["product_id"], "status": "active"})
        if not product or product["stock_quantity"] < item["quantity"]:
            raise ValueError("A product in your cart is no longer available in the requested quantity.")
        line_total = product["price_minor"] * item["quantity"]
        subtotal += line_total
        order_items.append({
            "product_id": product["product_id"],
            "name_snapshot": product["name"],
            "quantity": item["quantity"],
            "unit_price_minor": product["price_minor"],
            "line_total_minor": line_total,
        })
    order_id = f"KORA-{uuid4().hex[:8].upper()}"
    order = {
        "order_id": order_id,
        "user_id": request.user_id,
        "customer_id": None,
        "customer_snapshot": {"name": request.customer_name, "email": request.email, "phone": request.phone},
        "shipping_address": {"line1": request.line1, "line2": request.line2, "city": request.city, "state": request.state, "country": "NG"},
        "items": order_items,
        "subtotal_minor": subtotal,
        "delivery_fee_minor": 250000,
        "total_minor": subtotal + 250000,
        "currency": "NGN",
        "payment_status": "paid",
        "payment_method": request.payment_method,
        "payment_reference": f"MOCK-{uuid4().hex[:10].upper()}",
        "fulfillment_status": "processing",
        "courier": None,
        "tracking_number": None,
        "estimated_delivery": "2-3 business days",
        "created_at": now(),
        "updated_at": now(),
    }
    for item in order_items:
        result = await db.products.update_one(
            {"product_id": item["product_id"], "stock_quantity": {"$gte": item["quantity"]}},
            {"$inc": {"stock_quantity": -item["quantity"]}},
        )
        if result.modified_count != 1:
            raise ValueError("Stock changed while placing the order. Please review your cart and try again.")
    await db.orders.insert_one(order)
    await db.order_events.insert_one({"event_id": f"EVT-{uuid4().hex}", "order_id": order_id, "user_id": request.user_id, "type": "created", "message": "Order placed successfully.", "created_at": now()})
    await db.carts.delete_one({"user_id": request.user_id})
    order.pop("_id", None)
    return order


async def user_orders(user_id: str):
    return await get_db().orders.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).to_list(length=100)


async def order_detail(user_id: str, order_id: str):
    order = await get_db().orders.find_one({"user_id": user_id, "order_id": order_id}, {"_id": 0})
    if not order:
        return None
    order["events"] = await get_db().order_events.find({"user_id": user_id, "order_id": order_id}, {"_id": 0}).sort("created_at", 1).to_list(length=100)
    return order


async def request_return(order_id: str, request: ReturnRequest):
    db = get_db()
    order = await db.orders.find_one({"order_id": order_id, "user_id": request.user_id})
    if not order or order["fulfillment_status"] != "delivered":
        raise ValueError("Only delivered orders owned by you can be returned.")
    return_id = f"KORA-RET-{uuid4().hex[:8].upper()}"
    record = {
        "return_id": return_id, "order_id": order_id, "user_id": request.user_id,
        "items": request.items, "reason": request.reason, "reason_details": request.reason_details,
        "status": "requested", "created_at": now(), "updated_at": now(),
    }
    refund_amount = 0
    order_lines = {item["product_id"]: item for item in order.get("items", [])}
    for requested in request.items:
        line = order_lines.get(requested.get("product_id"))
        quantity = int(requested.get("quantity", 0))
        if not line or quantity < 1 or quantity > line["quantity"]:
            raise ValueError("Return quantity must match items purchased in this order.")
        refund_amount += line["unit_price_minor"] * quantity
    refund = {
        "refund_id": f"KORA-REF-{uuid4().hex[:8].upper()}",
        "order_id": order_id,
        "return_id": return_id,
        "user_id": request.user_id,
        "amount_minor": refund_amount,
        "currency": order.get("currency", "NGN"),
        "status": "requested",
        "payment_reference": order.get("payment_reference"),
        "created_at": now(),
        "updated_at": now(),
    }
    await db.returns.insert_one(record)
    await db.refunds.insert_one(refund)
    record["refund_id"] = refund["refund_id"]
    record.pop("_id", None)
    return record


async def user_returns(user_id: str):
    return await get_db().returns.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).to_list(length=100)


async def user_refunds(user_id: str):
    return await get_db().refunds.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).to_list(length=100)


async def return_detail(user_id: str, return_id: str):
    return await get_db().returns.find_one({"user_id": user_id, "return_id": return_id}, {"_id": 0})


async def refund_detail(user_id: str, refund_id: str):
    return await get_db().refunds.find_one({"user_id": user_id, "refund_id": refund_id}, {"_id": 0})
