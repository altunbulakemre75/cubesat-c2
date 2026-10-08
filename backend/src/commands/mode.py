"""
Current satellite mode for the policy engine.

Source order: the Redis cache written by the telemetry writer, then the
latest telemetry row in the database. The reading carries the time it was
observed, so callers can tell a fresh mode from a stale one: a LEO
satellite is out of view most of the time, and "we last heard it was in
NOMINAL three hours ago" must not be treated like current knowledge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

from src.ingestion.models import SatelliteMode
from src.storage import redis_client

logger = logging.getLogger(__name__)

# docs/MIMARI.md: mode information older than 2 h is stale and needs the
# operator's confirmation before it is relied on.
MODE_STALE_AFTER = timedelta(hours=2)


@dataclass(frozen=True)
class ModeReading:
    mode: SatelliteMode
    observed_at: datetime

    @property
    def stale(self) -> bool:
        return datetime.now(timezone.utc) - self.observed_at > MODE_STALE_AFTER


async def current_mode(pool: asyncpg.Pool, satellite_id: str) -> ModeReading | None:
    """Latest known mode, or None when the satellite has never reported a
    valid one."""
    try:
        cached = await redis_client.get_satellite_mode(satellite_id)
    except Exception as exc:  # noqa: BLE001 — the DB is the source of truth
        logger.debug("Mode cache unavailable, using DB: %s", exc)
        cached = None

    if cached is not None:
        mode, observed = cached
        reading = _reading(mode, datetime.fromisoformat(observed))
        if reading is not None:
            return reading

    row = await pool.fetchrow(
        "SELECT mode, time FROM telemetry WHERE satellite_id = $1 ORDER BY time DESC LIMIT 1",
        satellite_id,
    )
    if row is None or row["mode"] is None:
        return None
    return _reading(row["mode"], row["time"])


def _reading(mode: str, observed_at: datetime) -> ModeReading | None:
    try:
        return ModeReading(SatelliteMode(mode), observed_at)
    except ValueError:
        logger.warning("Ignoring unknown satellite mode value %r", mode)
        return None
