"""
Commands are scheduled onto passes over stations that can transmit.

v0.1.0 picked the next AOS over *any* active station, including stations
imported from SatNOGS — a receive-only network — so commands were
"transmitted" during passes nobody could uplink in.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
from fastapi.testclient import TestClient

from src.scheduler.service import CommandScheduler
from src.storage.db import _init_connection
from tests.integration.conftest import Db, bearer, login


def _schedule_once(dsn: str) -> None:
    async def _run() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await CommandScheduler(pool, js=None)._schedule_once()  # type: ignore[arg-type]
        finally:
            await pool.close()
    asyncio.run(_run())


def _station(db: Db, station_id: int, *, uplink: bool, satnogs_id: int | None = None) -> None:
    db.execute(
        "INSERT INTO ground_stations "
        "(id, name, satnogs_id, latitude_deg, longitude_deg, uplink_capable) "
        "VALUES ($1, $2, $3, 39.9, 32.8, $4)",
        station_id, f"GS{station_id}", satnogs_id, uplink,
    )


def _pass(db: Db, station_id: int, aos_in_min: float, los_in_min: float) -> None:
    db.execute(
        "INSERT INTO pass_schedule (satellite_id, station_id, aos, los, max_elevation_deg) "
        "VALUES ('SAT1', $1, NOW() + make_interval(secs => $2), "
        "NOW() + make_interval(secs => $3), 40)",
        station_id, aos_in_min * 60, los_in_min * 60,
    )


def _pending_command(db: Db) -> str:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1') ON CONFLICT DO NOTHING")
    cmd_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO commands (id, satellite_id, command_type) VALUES ($1, 'SAT1', 'ping')", cmd_id,
    )
    return cmd_id


def _row(db: Db, cmd_id: str) -> asyncpg.Record:
    return db.fetchrow("SELECT status, scheduled_at FROM commands WHERE id = $1::uuid", cmd_id)


def test_receive_only_satnogs_passes_are_not_used(db: Db, test_dsn: str) -> None:
    cmd = _pending_command(db)
    _station(db, 1, uplink=False, satnogs_id=42)
    _pass(db, 1, 5, 15)

    _schedule_once(test_dsn)

    assert _row(db, cmd)["status"] == "pending"


def test_next_pass_over_an_uplink_station_is_used(db: Db, test_dsn: str) -> None:
    cmd = _pending_command(db)
    _station(db, 1, uplink=False, satnogs_id=42)
    _station(db, 2, uplink=True)
    _pass(db, 1, 5, 15)     # earlier, but receive-only
    _pass(db, 2, 30, 40)

    _schedule_once(test_dsn)

    row = _row(db, cmd)
    assert row["status"] == "scheduled"
    expected = datetime.now(UTC) + timedelta(minutes=30)
    assert abs((row["scheduled_at"] - expected).total_seconds()) < 60


def test_pass_already_in_progress_is_used_immediately(db: Db, test_dsn: str) -> None:
    cmd = _pending_command(db)
    _station(db, 2, uplink=True)
    _pass(db, 2, -3, 5)     # AOS three minutes ago, LOS in five

    _schedule_once(test_dsn)

    row = _row(db, cmd)
    assert row["status"] == "scheduled"
    assert row["scheduled_at"] <= datetime.now(UTC) + timedelta(seconds=5)


def test_inactive_uplink_station_is_ignored(db: Db, test_dsn: str) -> None:
    cmd = _pending_command(db)
    _station(db, 2, uplink=True)
    db.execute("UPDATE ground_stations SET active = FALSE WHERE id = 2")
    _pass(db, 2, 5, 15)

    _schedule_once(test_dsn)

    assert _row(db, cmd)["status"] == "pending"


def test_stations_created_by_operators_are_uplink_capable_by_default(
    client: TestClient, db: Db,
) -> None:
    db.create_user("root", "station-admin-password", "admin")
    admin = bearer(login(client, "root", "station-admin-password")["access_token"])

    own = client.post("/stations", json={
        "name": "Club GS", "latitude_deg": 39.9, "longitude_deg": 32.8,
    }, headers=admin)
    rx_only = client.post("/stations", json={
        "name": "RX only", "latitude_deg": 41.0, "longitude_deg": 29.0, "uplink_capable": False,
    }, headers=admin)

    assert own.json()["uplink_capable"] is True
    assert rx_only.json()["uplink_capable"] is False


def test_pass_list_tells_which_passes_can_carry_commands(client: TestClient, db: Db) -> None:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1') ON CONFLICT DO NOTHING")
    _station(db, 1, uplink=False, satnogs_id=42)
    _station(db, 2, uplink=True)
    _pass(db, 1, 5, 15)
    _pass(db, 2, 30, 40)
    db.create_user("viewer", "pass-list-password", "viewer")
    viewer = bearer(login(client, "viewer", "pass-list-password")["access_token"])

    passes = client.get("/passes", params={"satellite_id": "SAT1"}, headers=viewer).json()

    assert [(p["station_id"], p["uplink_capable"]) for p in passes] == [(1, False), (2, True)]
