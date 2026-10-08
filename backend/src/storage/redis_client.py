import json
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis

from src.config import settings

_client: aioredis.Redis | None = None


def get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
            # Without timeouts a hung Redis stalls every request that
            # touches the cache instead of failing fast.
            socket_connect_timeout=2,
            socket_timeout=5,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


async def set_last_telemetry(satellite_id: str, data: dict[str, Any]) -> None:
    r = get_client()
    await r.set(f"telemetry:last:{satellite_id}", json.dumps(data), ex=3600)


async def get_last_telemetry(satellite_id: str) -> dict[str, Any] | None:
    r = get_client()
    raw = await r.get(f"telemetry:last:{satellite_id}")
    return json.loads(raw) if raw else None


async def set_satellite_mode(satellite_id: str, mode: str, observed_at: datetime) -> None:
    """Cache the latest mode with the time it was observed. No TTL: whether
    the value is still trustworthy is decided from observed_at by the
    caller (v0.1.0 let the key expire after 2 h and the policy check then
    silently stopped applying)."""
    r = get_client()
    value = json.dumps({"mode": mode, "at": observed_at.isoformat()})
    await r.set(f"satellite:mode:{satellite_id}", value)


async def get_satellite_mode(satellite_id: str) -> tuple[str, str] | None:
    """(mode, observed_at ISO string), or None if absent or in the legacy
    plain-string format written by v0.1.0."""
    r = get_client()
    raw = await r.get(f"satellite:mode:{satellite_id}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return str(data["mode"]), str(data["at"])
    except (ValueError, KeyError, TypeError):
        return None
