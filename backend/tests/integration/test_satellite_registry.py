"""
Only registered satellites exist.

v0.1.0 created a satellite row for any satellite_id that appeared in a
frame (writer), a command or a TLE upload. Combined with the open NATS bus
that let anyone conjure phantom satellites — which then also drew FDIR
alerts (Dream Security report #4, attack A). Registration is now an
explicit operator action (or SEED_SATELLITES for the simulator).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from unittest.mock import AsyncMock

import asyncpg
from fastapi.testclient import TestClient

from src.api.bootstrap import ensure_seed_satellites
from src.ingestion.service import IngestionService
from src.storage.db import _init_connection
from tests.integration.conftest import Db, bearer, login

PW = "registry-tests-password"
T = TypeVar("T")


def _frame(satellite_id: str) -> bytes:
    def callsign(cs: str, last: bool) -> bytes:
        return bytes(ord(c) << 1 for c in cs.ljust(6)[:6]) + bytes([0x60 | (1 if last else 0)])

    payload = {
        "satellite_id": satellite_id, "sequence": 1, "battery_voltage_v": 3.9,
        "temperature_obcs_c": 20.0, "temperature_eps_c": 18.0, "solar_power_w": 1.0,
        "rssi_dbm": -90.0, "uptime_s": 10, "mode": "nominal",
    }
    return callsign("GROUND", False) + callsign("CUBSAT", True) + b"\x03\xf0" + json.dumps(payload).encode()


class _Msg:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.subject = "telemetry.raw.test"
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


def _with_pool(dsn: str, fn: Callable[[asyncpg.Pool], Awaitable[T]]) -> T:
    async def _run() -> T:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            return await fn(pool)
        finally:
            await pool.close()
    return asyncio.run(_run())


def _ingest(dsn: str, *satellite_ids: str, register_between: Db | None = None) -> list[str]:
    """Feed one frame per id through the ingestion service; return the
    satellite ids it published canonical telemetry for."""
    published: list[str] = []

    async def _fn(pool: asyncpg.Pool) -> list[str]:
        js = AsyncMock()

        async def _publish(subject: str, _data: bytes) -> None:
            published.append(subject.rsplit(".", 1)[-1])
        js.publish = _publish
        service = IngestionService(js, pool=pool)
        for i, sat in enumerate(satellite_ids):
            msg = _Msg(_frame(sat))
            await service._handle(msg)  # type: ignore[arg-type]
            assert msg.acked
            if register_between is not None and i == 0:
                await pool.execute("INSERT INTO satellites (id, name) VALUES ('LATE1', 'LATE1')")
        return published

    return _with_pool(dsn, _fn)


def test_frames_for_unregistered_satellites_are_dropped(db: Db, test_dsn: str) -> None:
    db.execute("INSERT INTO satellites (id, name) VALUES ('CUBESAT1', 'CUBESAT1')")

    published = _ingest(test_dsn, "EVIL_SAT_666", "CUBESAT1")

    assert published == ["CUBESAT1"]


def test_newly_registered_satellite_is_accepted_promptly(db: Db, test_dsn: str) -> None:
    db.execute("INSERT INTO satellites (id, name) VALUES ('CUBESAT1', 'CUBESAT1')")

    published = _ingest(test_dsn, "CUBESAT1", "LATE1", register_between=db)

    assert published == ["CUBESAT1", "LATE1"]


def test_seed_satellites_are_registered_once(db: Db, test_dsn: str) -> None:
    _with_pool(test_dsn, lambda pool: ensure_seed_satellites(pool, ["SIM1", "SIM2"]))
    _with_pool(test_dsn, lambda pool: ensure_seed_satellites(pool, ["SIM1", "SIM2"]))

    rows = db.fetch("SELECT id FROM satellites ORDER BY id")
    assert [r["id"] for r in rows] == ["SIM1", "SIM2"]


def _operator(client: TestClient, db: Db) -> dict[str, str]:
    db.create_user("op", PW, "operator")
    return bearer(login(client, "op", PW)["access_token"])


def test_command_for_unknown_satellite_is_rejected(client: TestClient, db: Db) -> None:
    resp = client.post(
        "/commands", json={"satellite_id": "GHOST", "command_type": "ping"},
        headers=_operator(client, db),
    )
    assert resp.status_code == 404
    assert db.fetchval("SELECT COUNT(*) FROM satellites") == 0


ISS_TLE: dict[str, Any] = {
    "tle_line1": "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9002",
    "tle_line2": "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815305 30001",
}


def test_tle_for_unknown_satellite_is_rejected(client: TestClient, db: Db) -> None:
    resp = client.post("/satellites/GHOST/tle", json=ISS_TLE, headers=_operator(client, db))
    assert resp.status_code == 404
    assert db.fetchval("SELECT COUNT(*) FROM satellites") == 0
