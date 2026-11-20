from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import connect, close
from app.services.activity_cache import activity_cache
from app.routers import chat, commerce, health, knowledge, tickets


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await connect()
    try:
        from app.seed import seed_if_empty
        await seed_if_empty(db)
    except Exception as exc:
        print(f"Notice: automatic seeding check skipped: {exc}")

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
allow_origins = ["*"] if settings.cors_origins.strip() == "*" else [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
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

# Mount static frontend build for production deployments if present
from pathlib import Path
from fastapi.staticfiles import StaticFiles

frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

