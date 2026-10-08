"""
Pass prediction for many ground stations at once.

v0.1.0 predicted each station separately — 5 760 SGP4 steps per station
for a 48 h horizon, re-parsing the TLE at every step — synchronously on the
event loop. With a few hundred imported SatNOGS stations a single TLE
update froze the API (and every WebSocket) for tens of seconds.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from sgp4.api import Satrec

from src.orbit.passes import GroundStation, predict_passes, predict_passes_multi

ISS_L1 = "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9002"
ISS_L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49815305 30001"
START = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

STATIONS = [
    GroundStation(1, "Ankara", 39.93, 32.85, 900.0),
    GroundStation(2, "Darmstadt", 49.87, 8.65, 150.0),
    GroundStation(3, "Wallops", 37.94, -75.47, 10.0, min_elevation_deg=5.0),
]


def _key(p: Any) -> tuple[int, datetime]:
    return (p.station.id, p.aos)


def test_multi_station_prediction_matches_per_station_prediction() -> None:
    together = predict_passes_multi("ISS", ISS_L1, ISS_L2, STATIONS, START, horizon_hours=24)
    separately = [
        p for st in STATIONS
        for p in predict_passes("ISS", ISS_L1, ISS_L2, st, START, horizon_hours=24)
    ]
    assert sorted(together, key=_key) == sorted(separately, key=_key)
    assert together  # the ISS passes over these sites within a day


def test_tle_is_parsed_once_for_all_stations() -> None:
    real = Satrec.twoline2rv
    calls = 0

    def counting(*args: Any, **kwargs: Any) -> Satrec:
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    with patch("src.orbit.passes.Satrec.twoline2rv", side_effect=counting):
        predict_passes_multi("ISS", ISS_L1, ISS_L2, STATIONS * 10, START, horizon_hours=6)
    assert calls == 1


class _Conn:
    async def fetch(self, sql: str, *_a: Any) -> list[dict[str, Any]]:
        if "FROM ground_stations" not in sql:
            return []  # e.g. the in-progress pass lookup
        return [{"id": 1, "name": "GS", "latitude_deg": 39.9, "longitude_deg": 32.8,
                 "elevation_m": 0.0, "min_elevation_deg": 10.0}]

    async def execute(self, *_a: Any) -> None:
        return None

    async def executemany(self, *_a: Any) -> None:
        return None

    def transaction(self) -> "_Conn":
        return self

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _Pool:
    def acquire(self) -> _Conn:
        return _Conn()


async def test_pass_computation_runs_off_the_event_loop() -> None:
    from src.api.routes import satellites

    def slow_prediction(*_a: Any, **_k: Any) -> list[Any]:
        time.sleep(0.5)  # stands in for a large CPU-bound computation
        return []

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.04)
            ticks += 1

    with patch.object(satellites, "predict_passes_multi", side_effect=slow_prediction):
        await asyncio.gather(
            satellites._compute_and_store_passes(_Pool(), "ISS", ISS_L1, ISS_L2),  # type: ignore[arg-type]
            ticker(),
        )
    # A blocked loop would let the ticker run only after the 0.5 s sleep.
    assert ticks == 10
