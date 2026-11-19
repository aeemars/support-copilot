from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings


class Mongo:
    client: AsyncIOMotorClient | None = None


mongo = Mongo()


async def connect() -> AsyncIOMotorDatabase:
    mongo.client = AsyncIOMotorClient(settings.mongodb_uri)
    db = mongo.client[settings.mongodb_db]
    await ensure_indexes(db)
    return db


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.orders.create_index("order_id", unique=True)
    await db.tickets.create_index("ticket_id", unique=True)
    await db.knowledge_articles.create_index(
        [("title", "text"), ("body", "text"), ("tags", "text")],
        name="knowledge_text",
    )


async def close() -> None:
    if mongo.client:
        mongo.client.close()


def get_db() -> AsyncIOMotorDatabase:
    if mongo.client is None:
        raise RuntimeError("Database is not connected")
    return mongo.client[settings.mongodb_db]
