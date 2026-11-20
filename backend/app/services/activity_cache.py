import json
import asyncio
from datetime import datetime, timezone
from typing import Any

import aiomcache

from app.config import settings


class ActivityCache:
    def __init__(self) -> None:
        self.client: aiomcache.Client | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def _client(self) -> aiomcache.Client:
        loop = asyncio.get_running_loop()
        if self.client is None or self.loop is not loop:
            self.client = aiomcache.Client(settings.memcached_host, settings.memcached_port)
            self.loop = loop
        return self.client

    async def record(self, user_id: str, session_id: str, message_count: int, preview: str) -> None:
        activity: dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "message_count": message_count,
            "last_message_preview": preview[:160],
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            encoded = json.dumps(activity, separators=(",", ":")).encode()
            ttl = settings.memcached_activity_ttl_seconds
            client = self._client()
            await client.set(f"support:user:{user_id}".encode(), encoded, exptime=ttl)
            await client.set(f"support:session:{session_id}".encode(), encoded, exptime=ttl)
        except (aiomcache.ClientException, OSError):
            return

    async def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
            self.loop = None


activity_cache = ActivityCache()
