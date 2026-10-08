import asyncio
import json
import logging

import asyncpg

from src.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs so asyncpg returns dicts, not strings."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


# On a fresh volume the Postgres image runs a temporary server for its init
# scripts and restarts it; a container that starts alongside sees "the
# database system is starting up" or a refused connection for a few
# seconds. Startup waits that out instead of crashing.
_CONNECT_ATTEMPTS = 30
_CONNECT_RETRY_DELAY_S = 2.0
_TRANSIENT_CONNECT_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.exceptions.CannotConnectNowError,
    ConnectionError,
    OSError,
)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        for attempt in range(1, _CONNECT_ATTEMPTS + 1):
            try:
                _pool = await asyncpg.create_pool(
                    dsn=settings.asyncpg_dsn,
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                    ssl=False,
                    init=_init_connection,
                )
                break
            except _TRANSIENT_CONNECT_ERRORS as exc:
                if attempt == _CONNECT_ATTEMPTS:
                    raise
                logger.warning("Database not ready (%s); retrying (%d/%d)",
                               exc, attempt, _CONNECT_ATTEMPTS)
                await asyncio.sleep(_CONNECT_RETRY_DELAY_S)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
