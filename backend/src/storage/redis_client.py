import json
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
            decode_responses=True,
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


async def set_satellite_mode(satellite_id: str, mode: str) -> None:
    r = get_client()
    await r.set(f"satellite:mode:{satellite_id}", mode, ex=7200)


async def get_satellite_mode(satellite_id: str) -> str | None:
    r = get_client()
    return await r.get(f"satellite:mode:{satellite_id}")
