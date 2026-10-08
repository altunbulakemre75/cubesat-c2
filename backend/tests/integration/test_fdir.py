"""
FDIR monitor against a real database.

- reads the latest telemetry from the database (v0.1.0 read a Redis key
  with a 1 h TTL, so after an hour every satellite reported "No telemetry
  received since startup")
- one alert per fault: no re-alert every sweep, none after a restart, and
  after an ack only when there is newer evidence
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

from src.fdir.monitor import FDIRMonitor
from src.storage.db import _init_connection
from tests.integration.conftest import Db


class _JS:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.events.append({"subject": subject, **json.loads(payload)})


def _sweep(dsn: str, js: _JS, times: int = 1) -> None:
    async def _run() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            monitor = FDIRMonitor(pool, js, check_interval_s=60)  # type: ignore[arg-type]
            for _ in range(times):
                await monitor._check_all_satellites()
        finally:
            await pool.close()
    asyncio.run(_run())


@pytest.fixture(autouse=True)
def _satellite(db: Db) -> None:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1')")


def _telemetry(db: Db, age: timedelta, battery: float = 3.9) -> None:
    db.execute(
        """
        INSERT INTO telemetry (time, satellite_id, source, sequence, battery_voltage_v,
                               temperature_obcs_c, temperature_eps_c, mode)
        VALUES (NOW() - $1::interval, 'SAT1', 'ax25', 1, $2, 25, 20, 'nominal')
        """,
        age, battery,
    )


def _own_station_pass(db: Db, ended_ago: timedelta, length: timedelta = timedelta(minutes=8)) -> None:
    db.execute(
        "INSERT INTO ground_stations (id, name, latitude_deg, longitude_deg, uplink_capable) "
        "VALUES (1, 'Club GS', 39.9, 32.8, TRUE) ON CONFLICT DO NOTHING"
    )
    db.execute(
        "INSERT INTO pass_schedule (satellite_id, station_id, aos, los, max_elevation_deg) "
        "VALUES ('SAT1', 1, NOW() - $1::interval - $2::interval, NOW() - $1::interval, 40)",
        ended_ago, length,
    )


def _alerts(db: Db) -> int:
    return int(db.fetchval("SELECT COUNT(*) FROM fdir_alerts WHERE satellite_id = 'SAT1'"))


def test_critical_battery_raises_one_persisted_alert(db: Db, test_dsn: str) -> None:
    _telemetry(db, timedelta(seconds=5), battery=3.0)
    js = _JS()

    _sweep(test_dsn, js, times=3)

    assert _alerts(db) == 1
    assert len(js.events) == 1
    alert_id = db.fetchval("SELECT id::text FROM fdir_alerts")
    assert js.events[0]["id"] == alert_id
    assert js.events[0]["subject"] == "events.fdir.SAT1"


def test_restart_does_not_duplicate_an_open_alert(db: Db, test_dsn: str) -> None:
    _telemetry(db, timedelta(seconds=5), battery=3.0)
    _sweep(test_dsn, _JS())
    _sweep(test_dsn, _JS())   # a new monitor instance = backend restart

    assert _alerts(db) == 1


def test_acked_fault_reraises_only_on_newer_evidence(db: Db, test_dsn: str) -> None:
    _telemetry(db, timedelta(seconds=30), battery=3.0)
    _sweep(test_dsn, _JS())
    db.execute("UPDATE fdir_alerts SET acknowledged = TRUE, acknowledged_at = NOW()")

    _sweep(test_dsn, _JS())          # same old reading: stays quiet
    assert _alerts(db) == 1

    _telemetry(db, timedelta(seconds=0), battery=3.0)   # fresh reading, still bad
    _sweep(test_dsn, _JS())
    assert _alerts(db) == 2


def test_last_telemetry_comes_from_the_database_not_a_cache(db: Db, test_dsn: str) -> None:
    # Redis is unavailable in integration tests; v0.1.0 would report
    # "No telemetry received since startup" here.
    _telemetry(db, timedelta(seconds=5))
    js = _JS()
    _sweep(test_dsn, js)
    assert js.events == []


def test_satellite_out_of_view_between_passes_is_quiet(db: Db, test_dsn: str) -> None:
    _own_station_pass(db, ended_ago=timedelta(minutes=40))
    _telemetry(db, timedelta(minutes=44))   # heard during that pass
    js = _JS()

    _sweep(test_dsn, js)

    assert js.events == []


def test_pass_with_no_telemetry_raises_an_alert(db: Db, test_dsn: str) -> None:
    _own_station_pass(db, ended_ago=timedelta(minutes=10))
    _telemetry(db, timedelta(hours=5))
    js = _JS()

    _sweep(test_dsn, js)

    assert len(js.events) == 1
    assert "No telemetry during the pass over Club GS" in js.events[0]["reason"]


def test_registered_but_never_heard_satellite_raises_nothing(db: Db, test_dsn: str) -> None:
    js = _JS()
    _sweep(test_dsn, js)
    assert js.events == []


ISS_L1 = "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9002"
ISS_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815305 30001"


def test_tle_refresh_keeps_recent_pass_history(db: Db, test_dsn: str) -> None:
    # Missed-pass detection needs the last completed pass. A TLE refresh
    # (every 6 h) used to delete every pass of the satellite, past ones too.
    from src.api.routes.satellites import _compute_and_store_passes

    _own_station_pass(db, ended_ago=timedelta(hours=2))      # recent history
    _own_station_pass(db, ended_ago=timedelta(days=8))       # too old to keep

    async def _refresh() -> None:
        pool = await asyncpg.create_pool(test_dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await _compute_and_store_passes(pool, "SAT1", ISS_L1, ISS_L2)
        finally:
            await pool.close()
    asyncio.run(_refresh())

    past = db.fetch("SELECT los FROM pass_schedule WHERE satellite_id = 'SAT1' AND los < NOW()")
    assert len(past) == 1
