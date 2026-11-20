import json
from datetime import datetime, timezone
from typing import Any

import aiomcache

from app.config import settings


class ActivityCache:
    def __init__(self) -> None:
        self.client = aiomcache.Client(settings.memcached_host, settings.memcached_port)

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
            await self.client.set(f"support:user:{user_id}".encode(), encoded, exptime=ttl)
            await self.client.set(f"support:session:{session_id}".encode(), encoded, exptime=ttl)
        except (aiomcache.ClientException, OSError):
            return

    async def close(self) -> None:
        self.client.close()


activity_cache = ActivityCache()
