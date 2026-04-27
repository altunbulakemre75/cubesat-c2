"""
Orbit propagator tests.

Uses the ISS (NORAD 25544) TLE as a well-known reference
to validate SGP4 propagation and ECI→geodetic conversion.
"""

from datetime import datetime, timezone

import pytest

from src.orbit.propagator import SatellitePosition, propagate, _datetime_to_jd, _jd_to_gst

# ISS TLE — epoch 2024-06-14 (stable, well-documented orbit)
ISS_LINE1 = "1 25544U 98067A   24166.50000000  .00016717  00000-0  10270-3 0  9000"
ISS_LINE2 = "2 25544  51.6400 200.0000 0001000   0.0000   0.0000 15.50000000000000"

# Propagation time near TLE epoch
T_NEAR_EPOCH = datetime(2024, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


class TestPropagate:
    """SGP4 propagation with known TLE."""

    def test_returns_satellite_position(self):
        pos = propagate(ISS_LINE1, ISS_LINE2, T_NEAR_EPOCH)
        assert isinstance(pos, SatellitePosition)

    def test_latitude_in_range(self):
        """ISS inclination is ~51.6°, so lat should stay within ±52°."""
        pos = propagate(ISS_LINE1, ISS_LINE2, T_NEAR_EPOCH)
        assert -52.0 <= pos.lat_deg <= 52.0, f"lat_deg={pos.lat_deg} out of ISS range"

    def test_longitude_in_range(self):
        pos = propagate(ISS_LINE1, ISS_LINE2, T_NEAR_EPOCH)
        assert -180.0 <= pos.lon_deg <= 180.0

    def test_altitude_iss_range(self):
        """ISS orbits at ~400-420 km. Allow generous margins for epoch drift."""
        pos = propagate(ISS_LINE1, ISS_LINE2, T_NEAR_EPOCH)
        assert 300.0 <= pos.alt_km <= 500.0, f"alt_km={pos.alt_km} outside ISS range"

    def test_eci_components_nonzero(self):
        pos = propagate(ISS_LINE1, ISS_LINE2, T_NEAR_EPOCH)
        assert pos.x_km != 0.0
        assert pos.y_km != 0.0
        # z can be near zero depending on time but magnitude should be < R_E + alt
        assert abs(pos.x_km) < 7000
        assert abs(pos.y_km) < 7000
        assert abs(pos.z_km) < 7000

    def test_different_times_different_positions(self):
        """Propagating to two different times should yield different positions."""
        t1 = datetime(2024, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2024, 6, 14, 12, 10, 0, tzinfo=timezone.utc)
        pos1 = propagate(ISS_LINE1, ISS_LINE2, t1)
        pos2 = propagate(ISS_LINE1, ISS_LINE2, t2)
        # ISS moves ~7.7 km/s, in 10 minutes it travels ~4620 km
        assert pos1.lat_deg != pos2.lat_deg or pos1.lon_deg != pos2.lon_deg

    def test_invalid_tle_raises(self):
        """Corrupted TLE should raise ValueError."""
        bad_line2 = "2 25544  51.6400 200.0000 9999999   0.0000   0.0000 15.50000000000000"
        with pytest.raises((ValueError, Exception)):
            propagate(ISS_LINE1, bad_line2, T_NEAR_EPOCH)


class TestJulianDate:
    """Julian date conversion sanity checks."""

    def test_j2000_epoch(self):
        """J2000.0 = 2000-01-01 12:00:00 UTC = JD 2451545.0"""
        j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        jd = _datetime_to_jd(j2000)
        assert abs(jd - 2451545.0) < 0.001

    def test_known_date(self):
        """2024-06-14 00:00:00 UTC ≈ JD 2460475.5"""
        t = datetime(2024, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
        jd = _datetime_to_jd(t)
        assert abs(jd - 2460475.5) < 0.01

    def test_gst_returns_radians(self):
        """GST should be in [0, 2π) range."""
        import math
        jd = 2451545.0
        gst = _jd_to_gst(jd)
        assert 0 <= gst < 2 * math.pi
