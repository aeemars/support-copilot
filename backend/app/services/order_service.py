from app.db import get_db
from app.models import OrderStatusArgs


async def get_order_status(customer_id: str, args: OrderStatusArgs) -> dict:
    db = get_db()
    order = await db.orders.find_one(
        {"order_id": args.order_id, "customer_id": customer_id},
        {"_id": 0},
    )
    if not order:
        return {"found": False, "message": "No matching order was found for the authenticated customer."}
    return {
        "found": True,
        "order_id": order["order_id"],
        "status": order["status"],
        "items": order["items"],
        "tracking_number": order.get("tracking_number"),
        "courier": order.get("courier"),
        "estimated_delivery": order.get("estimated_delivery"),
    }
