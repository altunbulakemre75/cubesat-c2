"""
SGP4 propagator + pass predictor edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.orbit.passes import GroundStation, predict_passes
from src.orbit.propagator import propagate


# A well-known recent ISS TLE (epoch ~2025) — fine for behavioral tests.
ISS_L1 = "1 25544U 98067A   25101.50000000  .00001000  00000-0  20000-4 0  9998"
ISS_L2 = "2 25544  51.6400 100.0000 0001000  90.0000  90.0000 15.50000000000018"


def test_propagate_invalid_tle_raises():
    with pytest.raises(Exception):
        propagate("not a tle", "definitely not", datetime.now(timezone.utc))


def test_propagate_returns_finite_coordinates():
    """SGP4 must return real finite numbers, never NaN/Inf, for a valid TLE
    and a reasonable timestamp."""
    pos = propagate(ISS_L1, ISS_L2, datetime(2025, 4, 27, tzinfo=timezone.utc))
    assert -90.0 <= pos.lat_deg <= 90.0
    assert -180.0 <= pos.lon_deg <= 180.0
    assert 100.0 < pos.alt_km < 10000.0  # LEO sanity


def test_propagate_position_changes_over_time():
    """A satellite that doesn't move means the propagator is broken."""
    t0 = datetime(2025, 4, 27, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=10)
    p0 = propagate(ISS_L1, ISS_L2, t0)
    p1 = propagate(ISS_L1, ISS_L2, t1)
    # At least one of the three coords differs by more than 1 km
    assert (
        abs(p0.x_km - p1.x_km) > 1.0
        or abs(p0.y_km - p1.y_km) > 1.0
        or abs(p0.z_km - p1.z_km) > 1.0
    )


def test_propagate_with_naive_datetime_should_either_work_or_raise_clearly():
    """Defensive: passing a naive datetime is a common operator mistake.
    The propagator should either treat it as UTC or raise a clear error,
    NOT silently produce wrong coordinates."""
    naive = datetime(2025, 4, 27)  # no tzinfo
    try:
        pos = propagate(ISS_L1, ISS_L2, naive)
        # If we got here, ensure we still produced reasonable LEO coords
        assert 100.0 < pos.alt_km < 10000.0
    except (TypeError, ValueError):
        pass  # explicit rejection is also fine


def test_predict_passes_high_min_elevation_returns_few_or_none():
    """Setting min_elevation_deg = 89 (essentially overhead-only) should
    drastically cut the number of passes vs. min_elevation_deg = 5."""
    station_low = GroundStation(id=1, name="A", lat_deg=41.0, lon_deg=29.0,
                                elevation_m=100.0, min_elevation_deg=5.0)
    station_high = GroundStation(id=2, name="B", lat_deg=41.0, lon_deg=29.0,
                                 elevation_m=100.0, min_elevation_deg=89.0)
    start = datetime(2025, 4, 27, tzinfo=timezone.utc)
    low = predict_passes("ISS", ISS_L1, ISS_L2, station_low, start=start, horizon_hours=24)
    high = predict_passes("ISS", ISS_L1, ISS_L2, station_high, start=start, horizon_hours=24)
    assert len(low) >= len(high)


def test_predict_passes_horizon_zero_returns_empty():
    station = GroundStation(id=1, name="A", lat_deg=41.0, lon_deg=29.0,
                            elevation_m=100.0, min_elevation_deg=5.0)
    start = datetime(2025, 4, 27, tzinfo=timezone.utc)
    passes = predict_passes("ISS", ISS_L1, ISS_L2, station, start=start, horizon_hours=0)
    assert passes == []


def test_predict_passes_aos_before_los_for_each_window():
    """Sanity invariant: for each predicted pass, AOS must precede LOS."""
    station = GroundStation(id=1, name="A", lat_deg=41.0, lon_deg=29.0,
                            elevation_m=100.0, min_elevation_deg=5.0)
    start = datetime(2025, 4, 27, tzinfo=timezone.utc)
    passes = predict_passes("ISS", ISS_L1, ISS_L2, station, start=start, horizon_hours=24)
    for p in passes:
        assert p.aos < p.los, f"AOS {p.aos} not before LOS {p.los}"
        assert p.max_elevation_deg >= station.min_elevation_deg
