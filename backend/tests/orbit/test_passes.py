"""
Pass prediction tests.

Validates elevation/azimuth geometry and the predict_passes function
using the ISS TLE over a known ground station (Ankara).
"""

from datetime import datetime, timezone

import pytest

from src.orbit.passes import (
    GroundStation,
    PassWindow,
    _azimuth_deg,
    _elevation_deg,
    predict_passes,
)

# ISS TLE
ISS_LINE1 = "1 25544U 98067A   24166.50000000  .00016717  00000-0  10270-3 0  9000"
ISS_LINE2 = "2 25544  51.6400 200.0000 0001000   0.0000   0.0000 15.50000000000000"

# Ankara ground station
ANKARA_GS = GroundStation(
    id=1,
    name="Ankara",
    lat_deg=39.9334,
    lon_deg=32.8597,
    elevation_m=938.0,
    min_elevation_deg=10.0,
)


class TestElevation:
    """Elevation angle geometry."""

    def test_directly_overhead(self):
        """Satellite directly above GS should have ~90° elevation."""
        el = _elevation_deg(
            sat_lat=39.93, sat_lon=32.86, sat_alt_km=400.0,
            gs_lat=39.93, gs_lon=32.86, gs_elev_m=938.0,
        )
        assert el > 85.0

    def test_far_away_negative(self):
        """Satellite on the opposite side of Earth → below horizon."""
        el = _elevation_deg(
            sat_lat=-39.93, sat_lon=-147.14, sat_alt_km=400.0,
            gs_lat=39.93, gs_lon=32.86, gs_elev_m=0.0,
        )
        assert el < 0.0

    def test_horizon_range(self):
        """Satellite ~2000 km away at 400 km alt → low but positive elevation."""
        el = _elevation_deg(
            sat_lat=55.0, sat_lon=32.86, sat_alt_km=400.0,
            gs_lat=39.93, gs_lon=32.86, gs_elev_m=0.0,
        )
        # ~15° lat away ≈ ~1670 km — should be near horizon
        assert -10.0 < el < 30.0


class TestAzimuth:
    """Azimuth bearing checks."""

    def test_north(self):
        """Satellite due north → azimuth ~0° (or 360°)."""
        az = _azimuth_deg(
            sat_lat=50.0, sat_lon=32.86,
            gs_lat=39.93, gs_lon=32.86,
        )
        assert az < 5.0 or az > 355.0

    def test_east(self):
        """Satellite due east → azimuth ~90°."""
        az = _azimuth_deg(
            sat_lat=39.93, sat_lon=42.86,
            gs_lat=39.93, gs_lon=32.86,
        )
        assert 80.0 < az < 100.0

    def test_range(self):
        """Azimuth must always be [0, 360)."""
        az = _azimuth_deg(
            sat_lat=-10.0, sat_lon=-50.0,
            gs_lat=39.93, gs_lon=32.86,
        )
        assert 0.0 <= az < 360.0


class TestPredictPasses:
    """Integration test for pass prediction."""

    def test_returns_pass_windows(self):
        """Over 24 hours, ISS should have at least one pass over Ankara."""
        start = datetime(2024, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
        passes = predict_passes(
            satellite_id="ISS",
            tle_line1=ISS_LINE1,
            tle_line2=ISS_LINE2,
            station=ANKARA_GS,
            start=start,
            horizon_hours=24,
        )
        # ISS typically has 3-6 visible passes per day over mid-latitude stations
        assert len(passes) >= 1, "Expected at least 1 ISS pass over Ankara in 24h"
        assert all(isinstance(p, PassWindow) for p in passes)

    def test_aos_before_los(self):
        """AOS must always be before LOS."""
        start = datetime(2024, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
        passes = predict_passes(
            satellite_id="ISS",
            tle_line1=ISS_LINE1,
            tle_line2=ISS_LINE2,
            station=ANKARA_GS,
            start=start,
            horizon_hours=24,
        )
        for p in passes:
            assert p.aos < p.los, f"AOS {p.aos} >= LOS {p.los}"

    def test_max_elevation_above_minimum(self):
        """Returned passes should meet the minimum elevation threshold."""
        start = datetime(2024, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
        passes = predict_passes(
            satellite_id="ISS",
            tle_line1=ISS_LINE1,
            tle_line2=ISS_LINE2,
            station=ANKARA_GS,
            start=start,
            horizon_hours=24,
        )
        for p in passes:
            assert p.max_elevation_deg >= ANKARA_GS.min_elevation_deg

    def test_short_horizon_may_have_no_passes(self):
        """Very short horizon (1 min) may return zero passes — no crash."""
        start = datetime(2024, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
        passes = predict_passes(
            satellite_id="ISS",
            tle_line1=ISS_LINE1,
            tle_line2=ISS_LINE2,
            station=ANKARA_GS,
            start=start,
            horizon_hours=0,
            step_seconds=30,
        )
        assert isinstance(passes, list)
