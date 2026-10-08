"""
Leader election for background services via a Postgres advisory lock.

Every API worker/replica serves HTTP, but the background services
(ingestion, telemetry writer, scheduler, FDIR, TLE refresh, SatNOGS
fetcher) must run exactly once — otherwise alerts are duplicated and
schedulers race for the same commands.

Each process keeps one dedicated connection and tries
pg_try_advisory_lock(). The holder starts the services and keeps checking
its connection; if the connection (or the process) dies, Postgres releases
the lock and a follower takes over within poll_s. A brief overlap of at
most poll_s is possible after a network partition; the scheduler's
"WHERE status = ..." claims keep that from double-sending commands.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import asyncpg

logger = logging.getLogger(__name__)

# Arbitrary, stable 64-bit key ("cubesatL" in ASCII).
LEADER_LOCK_KEY = 0x6375626573617C4C


class LeaderElector:
    def __init__(
        self,
        dsn: str,
        start: Callable[[], Awaitable[None]],
        stop: Callable[[], Awaitable[None]],
        *,
        poll_s: float = 5.0,
    ) -> None:
        self._dsn = dsn
        self._start = start
        self._stop = stop
        self._poll = poll_s
        self.is_leader = False

    async def run(self) -> None:
        while True:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(self._dsn)
                while not await conn.fetchval("SELECT pg_try_advisory_lock($1)", LEADER_LOCK_KEY):
                    await asyncio.sleep(self._poll)

                logger.info("Acquired leadership — starting background services")
                self.is_leader = True
                await self._start()
                while True:
                    await asyncio.sleep(self._poll)
                    await conn.fetchval("SELECT 1")  # lost connection = lost lock
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Leader election connection problem: %s", exc)
            finally:
                if self.is_leader:
                    self.is_leader = False
                    logger.info("Leadership released — stopping background services")
                    await self._stop()
                if conn is not None:
                    conn.terminate()  # releases the session-level lock at once
            await asyncio.sleep(self._poll)
