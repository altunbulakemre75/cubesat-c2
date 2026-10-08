"""
Satellite mode lookup for the policy engine.

v0.1.0 kept the mode only in Redis with a 2 h TTL and skipped the policy
check entirely when the key was missing. For a LEO satellite that is out
of view most of the time, "no key" was the normal case, so mode-based
command restrictions silently stopped applying between passes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.commands.mode import MODE_STALE_AFTER, ModeReading, current_mode
from src.ingestion.models import SatelliteMode

NOW = datetime.now(UTC)


def _pool(row: dict[str, Any] | None) -> MagicMock:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


async def test_cached_reading_is_used_without_touching_the_db() -> None:
    pool = _pool(None)
    cached = ("safe", NOW.isoformat())
    with patch("src.commands.mode.redis_client.get_satellite_mode",
               new=AsyncMock(return_value=cached)):
        reading = await current_mode(pool, "SAT1")
    assert reading == ModeReading(SatelliteMode.SAFE, NOW)
    pool.fetchrow.assert_not_called()


async def test_falls_back_to_latest_telemetry_when_cache_is_empty() -> None:
    pool = _pool({"mode": "science", "time": NOW})
    with patch("src.commands.mode.redis_client.get_satellite_mode",
               new=AsyncMock(return_value=None)):
        reading = await current_mode(pool, "SAT1")
    assert reading == ModeReading(SatelliteMode.SCIENCE, NOW)


async def test_falls_back_to_the_db_when_redis_is_down() -> None:
    pool = _pool({"mode": "nominal", "time": NOW})
    boom = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch("src.commands.mode.redis_client.get_satellite_mode", new=boom):
        reading = await current_mode(pool, "SAT1")
    assert reading is not None and reading.mode == SatelliteMode.NOMINAL


async def test_no_telemetry_at_all_means_unknown() -> None:
    with patch("src.commands.mode.redis_client.get_satellite_mode",
               new=AsyncMock(return_value=None)):
        assert await current_mode(_pool(None), "SAT1") is None


async def test_garbage_mode_value_is_treated_as_unknown() -> None:
    with patch("src.commands.mode.redis_client.get_satellite_mode",
               new=AsyncMock(return_value=("warp-speed", NOW.isoformat()))):
        assert await current_mode(_pool(None), "SAT1") is None


def test_readings_older_than_the_threshold_are_stale() -> None:
    assert not ModeReading(SatelliteMode.NOMINAL, NOW - timedelta(minutes=5)).stale
    assert ModeReading(SatelliteMode.NOMINAL, NOW - MODE_STALE_AFTER - timedelta(seconds=1)).stale


async def test_cache_stores_the_observation_time_without_expiry() -> None:
    from src.storage import redis_client

    fake = MagicMock()
    fake.set = AsyncMock()
    with patch.object(redis_client, "get_client", return_value=fake):
        await redis_client.set_satellite_mode("SAT1", "safe", NOW)
    _key, value = fake.set.call_args.args
    assert "safe" in value and NOW.isoformat() in value
    assert "ex" not in fake.set.call_args.kwargs
