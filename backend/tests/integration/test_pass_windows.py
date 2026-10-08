"""
Transmission respects pass windows; retries follow docs/MIMARI.md.

- A pass-planned command whose window was missed (e.g. the backend was
  down during the pass) is re-planned, not fired into an empty sky.
- Retries back off 1 s, 4 s, 16 s (max 3).
- LOS protection: a timeout caused by the pass ending doesn't consume a
  retry; the command waits for the next pass instead.
- An explicit NACK from the satellite is final — retrying a command the
  satellite refused can't succeed.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import pytest

from src.scheduler.service import CommandScheduler
from src.storage.db import _init_connection
from tests.integration.conftest import Db


class _JS:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, subject: str, _payload: bytes) -> None:
        self.published.append(subject)


def _run(dsn: str, step: Callable[[CommandScheduler], Awaitable[Any]]) -> _JS:
    js = _JS()

    async def _main() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await step(CommandScheduler(pool, js))  # type: ignore[arg-type]
        finally:
            await pool.close()
    asyncio.run(_main())
    return js


@pytest.fixture(autouse=True)
def _satellite_and_station(db: Db) -> None:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1')")
    db.execute(
        "INSERT INTO ground_stations (id, name, latitude_deg, longitude_deg, uplink_capable) "
        "VALUES (1, 'GS', 39.9, 32.8, TRUE)"
    )


def _window(db: Db, start: timedelta, end: timedelta) -> None:
    db.execute(
        "INSERT INTO pass_schedule (satellite_id, station_id, aos, los, max_elevation_deg) "
        "VALUES ('SAT1', 1, NOW() + $1::interval, NOW() + $2::interval, 40)",
        start, end,
    )


def _command(db: Db, *, status: str, manual: bool = False, retry_count: int = 0,
             safe_retry: bool = True, command_type: str = "ping",
             sent_ago: timedelta | None = None) -> str:
    cmd_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO commands (id, satellite_id, command_type, status, scheduled_at,
                              scheduled_manually, retry_count, safe_retry, sent_at)
        VALUES ($1, 'SAT1', $2, $3, NOW() - interval '5 seconds', $4, $5, $6,
                CASE WHEN $7::interval IS NULL THEN NULL ELSE NOW() - $7::interval END)
        """,
        cmd_id, command_type, status, manual, retry_count, safe_retry, sent_ago,
    )
    return cmd_id


def _row(db: Db, cmd_id: str) -> asyncpg.Record:
    return db.fetchrow(
        "SELECT status, retry_count, scheduled_at, error_message FROM commands WHERE id = $1::uuid",
        cmd_id,
    )


# ── Transmission window ──────────────────────────────────────────────────────

def test_missed_window_is_replanned_not_transmitted(db: Db, test_dsn: str) -> None:
    _window(db, timedelta(minutes=-20), timedelta(minutes=-10))   # pass is over
    cmd = _command(db, status="scheduled")

    js = _run(test_dsn, lambda s: s._execute_once())

    row = _row(db, cmd)
    assert (row["status"], row["scheduled_at"]) == ("pending", None)
    assert js.published == []


def test_command_inside_its_window_is_transmitted(db: Db, test_dsn: str) -> None:
    _window(db, timedelta(minutes=-2), timedelta(minutes=6))
    cmd = _command(db, status="scheduled")

    js = _run(test_dsn, lambda s: s._execute_once())

    assert _row(db, cmd)["status"] == "sent"
    assert js.published == ["commands.SAT1"]


def test_operator_scheduled_command_is_not_held_to_a_window(db: Db, test_dsn: str) -> None:
    cmd = _command(db, status="scheduled", manual=True)

    js = _run(test_dsn, lambda s: s._execute_once())

    assert _row(db, cmd)["status"] == "sent"
    assert js.published == ["commands.SAT1"]


# ── Retry and LOS protection ─────────────────────────────────────────────────

@pytest.mark.parametrize(("retry_count", "backoff_s"), [(0, 1), (1, 4), (2, 16)])
def test_timeout_inside_the_window_retries_with_backoff(
    db: Db, test_dsn: str, retry_count: int, backoff_s: int,
) -> None:
    _window(db, timedelta(minutes=-5), timedelta(minutes=5))
    cmd = _command(db, status="sent", retry_count=retry_count, sent_ago=timedelta(seconds=61))

    _run(test_dsn, lambda s: s._timeout_once())

    row = _row(db, cmd)
    assert row["status"] == "scheduled"
    assert row["retry_count"] == retry_count + 1
    expected = datetime.now(timezone.utc) + timedelta(seconds=backoff_s)
    assert abs((row["scheduled_at"] - expected).total_seconds()) < 3


def test_timeout_after_los_waits_for_the_next_pass_without_using_a_retry(
    db: Db, test_dsn: str,
) -> None:
    _window(db, timedelta(minutes=-10), timedelta(seconds=-20))   # LOS 20 s ago
    cmd = _command(db, status="sent", retry_count=1, sent_ago=timedelta(seconds=70))

    _run(test_dsn, lambda s: s._timeout_once())

    row = _row(db, cmd)
    assert (row["status"], row["scheduled_at"], row["retry_count"]) == ("pending", None, 1)


def test_exhausted_retries_end_dead(db: Db, test_dsn: str) -> None:
    _window(db, timedelta(minutes=-5), timedelta(minutes=5))
    cmd = _command(db, status="sent", retry_count=3, sent_ago=timedelta(seconds=61))

    _run(test_dsn, lambda s: s._timeout_once())

    assert _row(db, cmd)["status"] == "dead"


def test_unsafe_command_is_never_retried(db: Db, test_dsn: str) -> None:
    _window(db, timedelta(minutes=-10), timedelta(seconds=-20))
    cmd = _command(db, status="sent", command_type="deploy_antenna",
                   sent_ago=timedelta(seconds=70))

    _run(test_dsn, lambda s: s._timeout_once())

    assert _row(db, cmd)["status"] == "dead"


def test_satellite_nack_is_final(db: Db, test_dsn: str) -> None:
    _window(db, timedelta(minutes=-5), timedelta(minutes=5))
    cmd = _command(db, status="sent", sent_ago=timedelta(seconds=2))

    _run(test_dsn, lambda s: s._mark_rejected(cmd, "unsupported command type"))

    row = _row(db, cmd)
    assert row["status"] == "dead"
    assert "unsupported command type" in row["error_message"]
