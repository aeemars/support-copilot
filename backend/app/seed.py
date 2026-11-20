import asyncio
from datetime import datetime, timedelta, timezone

from app.db import connect, ensure_indexes, close


CUSTOMERS = [
    {"customer_id": "cust-ada", "name": "Ada Okafor", "email": "ada@example.com"},
    {"customer_id": "cust-tunde", "name": "Tunde Balogun", "email": "tunde@example.com"},
    {"customer_id": "cust-zainab", "name": "Zainab Bello", "email": "zainab@example.com"},
]

PRODUCTS = [
    {"product_id": "prod-earbuds", "slug": "wireless-earbuds", "name": "Wireless Earbuds", "description": "Compact Bluetooth earbuds with a charging case.", "category": "Audio", "image_url": None, "price_minor": 4500000, "currency": "NGN", "stock_quantity": 18, "status": "active", "warranty_summary": "One-year manufacturer warranty.", "return_eligible": True, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
    {"product_id": "prod-charger", "slug": "usb-c-fast-charger", "name": "USB-C Fast Charger", "description": "Compact 30W charger for compatible phones and tablets.", "category": "Power", "image_url": None, "price_minor": 1800000, "currency": "NGN", "stock_quantity": 30, "status": "active", "warranty_summary": "Six-month manufacturer warranty.", "return_eligible": True, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
    {"product_id": "prod-speaker", "slug": "portable-bluetooth-speaker", "name": "Portable Bluetooth Speaker", "description": "Water-resistant speaker for everyday listening.", "category": "Audio", "image_url": None, "price_minor": 6250000, "currency": "NGN", "stock_quantity": 12, "status": "active", "warranty_summary": "One-year manufacturer warranty.", "return_eligible": True, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
    {"product_id": "prod-keyboard", "slug": "mechanical-keyboard", "name": "Mechanical Keyboard", "description": "Full-size mechanical keyboard with tactile switches.", "category": "Computing", "image_url": None, "price_minor": 7200000, "currency": "NGN", "stock_quantity": 10, "status": "active", "warranty_summary": "One-year manufacturer warranty.", "return_eligible": True, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
    {"product_id": "prod-stand", "slug": "adjustable-phone-stand", "name": "Adjustable Phone Stand", "description": "Foldable aluminium stand for desks and travel.", "category": "Accessories", "image_url": None, "price_minor": 950000, "currency": "NGN", "stock_quantity": 25, "status": "active", "warranty_summary": "Thirty-day replacement cover.", "return_eligible": True, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
    {"product_id": "prod-powerbank", "slug": "20000mah-power-bank", "name": "20,000mAh Power Bank", "description": "High-capacity USB-C power bank with dual outputs.", "category": "Power", "image_url": None, "price_minor": 5500000, "currency": "NGN", "stock_quantity": 14, "status": "active", "warranty_summary": "One-year manufacturer warranty.", "return_eligible": True, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
]


ARTICLES = [
    {"slug": "lagos-same-day-delivery", "title": "Lagos Same-Day Delivery", "body": "Same-day delivery is available for eligible paid orders placed before 12:00 noon within Lagos mainland and island zones. Availability is confirmed at checkout; orders after the cutoff or for ineligible areas move to the next delivery day.", "tags": ["lagos", "delivery", "same-day"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "abuja-delivery", "title": "Abuja Delivery", "body": "We deliver to Abuja and the surrounding Federal Capital Territory. Abuja orders normally use courier delivery rather than Lagos same-day delivery. The exact delivery estimate appears when the order is confirmed.", "tags": ["abuja", "delivery", "coverage"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "delivery-coverage", "title": "Delivery Coverage", "body": "Kora Electronics currently offers Lagos same-day delivery for eligible areas and nationwide courier delivery to Nigerian cities served by our courier partners. Remote locations may require additional courier time.", "tags": ["delivery", "coverage", "nigeria"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "returns-policy", "title": "Returns Policy", "body": "Unused items in original packaging may be returned within seven calendar days of delivery. The item must include accessories and proof of purchase. Opened software, consumables, and personalised items are excluded unless faulty.", "tags": ["returns", "refund", "policy"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "refunds", "title": "Refunds and Processing", "body": "Approved refunds are returned to the original payment method after the item passes inspection. Card refunds can take 5 to 10 business days to appear after approval; bank and transfer processing may vary.", "tags": ["refund", "payment", "processing"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "warranty", "title": "Warranty Coverage", "body": "Most Kora Electronics devices include a manufacturer warranty that varies by product and brand. Keep the receipt and serial number. Warranty support covers manufacturing defects, not accidental damage or misuse.", "tags": ["warranty", "repair", "device"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "payment-methods", "title": "Payment Methods", "body": "Kora Electronics accepts debit cards, major bank transfers, and approved payment links. Cash on delivery is not generally available for online orders. Payment confirmation is required before dispatch.", "tags": ["payment", "card", "transfer"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "damaged-item", "title": "Damaged Item Procedure", "body": "If an item arrives damaged, do not discard its packaging. Photograph the item and packaging and contact us within 48 hours of delivery. Include the order ID and a brief description so a support agent can arrange a replacement or refund.", "tags": ["damaged", "arrival", "returns", "support"], "updated_at": datetime.now(timezone.utc)},
    {"slug": "operating-hours", "title": "Operating Hours", "body": "Kora Electronics customer support operates Monday to Saturday from 8:30 AM to 6:00 PM West Africa Time. Messages sent outside those hours are queued for the next operating day.", "tags": ["hours", "support", "contact"], "updated_at": datetime.now(timezone.utc)},
]


ORDERS = [
    {"order_id": "KORA-1001", "customer_id": "cust-ada", "customer_name": "Ada Okafor", "status": "Out for delivery", "items": [{"name": "Wireless earbuds", "quantity": 1}], "tracking_number": "KO-DEMO-1001", "courier": "Kora Express", "estimated_delivery": "Today, by 6:00 PM", "created_at": datetime.now(timezone.utc)},
    {"order_id": "KORA-1002", "customer_id": "cust-ada", "customer_name": "Ada Okafor", "status": "Delivered", "items": [{"name": "USB-C charger", "quantity": 2}], "tracking_number": "KO-DEMO-1002", "courier": "Kora Express", "estimated_delivery": "Completed", "created_at": datetime.now(timezone.utc) - timedelta(days=5)},
    {"order_id": "KORA-1003", "customer_id": "cust-tunde", "customer_name": "Tunde Balogun", "status": "Processing", "items": [{"name": "Portable speaker", "quantity": 1}], "tracking_number": None, "courier": "Pending assignment", "estimated_delivery": "2-3 business days", "created_at": datetime.now(timezone.utc)},
    {"order_id": "KORA-1004", "customer_id": "cust-tunde", "customer_name": "Tunde Balogun", "status": "Shipped", "items": [{"name": "Mechanical keyboard", "quantity": 1}], "tracking_number": "KO-DEMO-1004", "courier": "Swift Cargo", "estimated_delivery": "In 2 business days", "created_at": datetime.now(timezone.utc) - timedelta(days=1)},
    {"order_id": "KORA-1005", "customer_id": "cust-zainab", "customer_name": "Zainab Bello", "status": "Delivered", "items": [{"name": "Phone stand", "quantity": 1}], "tracking_number": "KO-DEMO-1005", "courier": "Swift Cargo", "estimated_delivery": "Completed", "created_at": datetime.now(timezone.utc) - timedelta(days=8)},
]


async def main() -> None:
    db = await connect()
    await db.customers.delete_many({})
    await db.knowledge_articles.delete_many({})
    await db.products.delete_many({})
    await db.orders.delete_many({})
    await db.customers.insert_many(CUSTOMERS)
    await db.knowledge_articles.insert_many(ARTICLES)
    await db.products.insert_many(PRODUCTS)
    await db.orders.insert_many(ORDERS)
    print("Seeded demo customers, knowledge articles, and orders.")
    await close()


if __name__ == "__main__":
    asyncio.run(main())
