import pymongo
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings


class Mongo:
    client: AsyncMongoClient | None = None


mongo = Mongo()


async def connect() -> AsyncDatabase:
    mongo.client = AsyncMongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    db = mongo.client[settings.mongodb_db]
    await db.command("ping")
    await ensure_indexes(db)
    return db


async def ensure_indexes(db: AsyncDatabase) -> None:
    await db.orders.create_index("order_id", unique=True)
    await db.tickets.create_index("ticket_id", unique=True)
    await db.knowledge_articles.create_index(
        [("title", pymongo.TEXT), ("body", pymongo.TEXT), ("tags", pymongo.TEXT)],
        name="knowledge_text",
    )


async def close() -> None:
    if mongo.client:
        await mongo.client.close()


def get_db() -> AsyncDatabase:
    if mongo.client is None:
        raise RuntimeError("Database is not connected")
    return mongo.client[settings.mongodb_db]
