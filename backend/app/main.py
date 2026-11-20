from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import connect, close
from app.services.activity_cache import activity_cache
from app.routers import chat, commerce, health, knowledge, tickets


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    if not settings.wekoya_api_key:
        print("WARNING: WEKOYA_API_KEY is not configured; chat requests will be safely disabled.")
    else:
        from app.services.bridge_client import BridgeClient
        supported, warning = await BridgeClient().check_catalogue()
        if not supported:
            print(f"WARNING: {warning}")
    yield
    await activity_cache.close()
    await close()


app = FastAPI(title="Support Copilot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/customers")
async def customers():
    from app.db import get_db
    db = get_db()
    return await db.customers.find({}, {"_id": 0}).to_list(length=20)


@app.get("/api/customers/{customer_id}/orders")
async def customer_orders(customer_id: str):
    from app.db import get_db
    db = get_db()
    return await db.orders.find({"customer_id": customer_id}, {"_id": 0}).to_list(length=50)


app.include_router(health.router)
app.include_router(knowledge.router)
app.include_router(chat.router)
app.include_router(tickets.router)
app.include_router(commerce.router)
