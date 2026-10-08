"""
Registered-satellite lookup for the ingestion hot path.

Telemetry is only accepted for satellites an operator registered (or the
simulator's SEED_SATELLITES). Frames arrive at up to hundreds per second,
so known IDs are cached and refreshed periodically; an unknown ID costs one
targeted lookup (so a satellite registered a moment ago is accepted at
once) and is then negatively cached, with a global cap on lookups so a
flood of random IDs can't turn into a query per frame.
"""

from __future__ import annotations

import time

import asyncpg

_REFRESH_S = 30.0
_NEGATIVE_TTL_S = 10.0
_MAX_LOOKUPS_PER_S = 20
_MAX_NEGATIVE_ENTRIES = 10_000


class SatelliteRegistry:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._known: set[str] = set()
        self._expires = 0.0
        self._unknown: dict[str, float] = {}
        self._window_start = 0.0
        self._window_lookups = 0

    async def is_registered(self, satellite_id: str) -> bool:
        now = time.monotonic()
        if now >= self._expires:
            await self._reload(now)
        if satellite_id in self._known:
            return True

        if self._unknown.get(satellite_id, 0.0) > now:
            return False
        if not self._lookup_allowed(now):
            return False

        found = bool(await self._pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM satellites WHERE id = $1)", satellite_id,
        ))
        if found:
            self._known.add(satellite_id)
        else:
            if len(self._unknown) >= _MAX_NEGATIVE_ENTRIES:
                self._unknown.clear()
            self._unknown[satellite_id] = now + _NEGATIVE_TTL_S
        return found

    async def _reload(self, now: float) -> None:
        rows = await self._pool.fetch("SELECT id FROM satellites")
        self._known = {r["id"] for r in rows}
        self._expires = now + _REFRESH_S

    def _lookup_allowed(self, now: float) -> bool:
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._window_lookups = 0
        self._window_lookups += 1
        return self._window_lookups <= _MAX_LOOKUPS_PER_S
