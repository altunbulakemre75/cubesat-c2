"""
SGP4-based orbit propagator using the sgp4 library.
Returns satellite position as (lat_deg, lon_deg, alt_km) at a given UTC datetime.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from dataclasses import dataclass

from sgp4.api import Satrec, WGS84


@dataclass(frozen=True)
class SatellitePosition:
    lat_deg: float
    lon_deg: float
    alt_km: float
    # ECI components (km) — needed for Doppler and CesiumJS
    x_km: float
    y_km: float
    z_km: float


def _eci_to_geodetic(x: float, y: float, z: float, t: datetime) -> tuple[float, float, float]:
    """Convert ECI (km) to geodetic lat/lon/alt using WGS-84."""
    # Greenwich Sidereal Time
    jd = _datetime_to_jd(t)
    gst = _jd_to_gst(jd)

    # ECI → ECEF
    cos_gst = math.cos(gst)
    sin_gst = math.sin(gst)
    x_ecef = x * cos_gst + y * sin_gst
    y_ecef = -x * sin_gst + y * cos_gst
    z_ecef = z

    # ECEF → geodetic (iterative)
    a = 6378.137          # WGS-84 equatorial radius km
    e2 = 6.694379990e-3   # first eccentricity squared
    lon = math.atan2(y_ecef, x_ecef)
    p = math.sqrt(x_ecef**2 + y_ecef**2)
    lat = math.atan2(z_ecef, p * (1 - e2))
    for _ in range(5):
        sin_lat = math.sin(lat)
        N = a / math.sqrt(1 - e2 * sin_lat**2)
        lat = math.atan2(z_ecef + e2 * N * sin_lat, p)
    sin_lat = math.sin(lat)
    N = a / math.sqrt(1 - e2 * sin_lat**2)
    alt = p / math.cos(lat) - N if abs(math.cos(lat)) > 1e-9 else abs(z_ecef) / abs(sin_lat) - N * (1 - e2)

    return math.degrees(lat), math.degrees(lon), alt


def _datetime_to_jd(t: datetime) -> float:
    """Julian date from UTC datetime."""
    t = t.astimezone(timezone.utc)
    y, m, d = t.year, t.month, t.day
    h = t.hour + t.minute / 60 + t.second / 3600 + t.microsecond / 3_600_000_000
    if m <= 2:
        y -= 1
        m += 12
    A = int(y / 100)
    B = 2 - A + int(A / 4)
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + h / 24 + B - 1524.5


def _jd_to_gst(jd: float) -> float:
    """Greenwich Sidereal Time (radians) from Julian date."""
    T = (jd - 2451545.0) / 36525
    gst_deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
               + 0.000387933 * T**2 - T**3 / 38710000) % 360
    return math.radians(gst_deg)


def propagate(tle_line1: str, tle_line2: str, t: datetime) -> SatellitePosition:
    """Compute satellite position at time t using SGP4."""
    sat = Satrec.twoline2rv(tle_line1, tle_line2, WGS84)
    t_utc = t.astimezone(timezone.utc)
    jd = _datetime_to_jd(t_utc)
    jd_frac = jd % 1

    e, r, v = sat.sgp4(jd - jd_frac, jd_frac)
    if e != 0:
        raise ValueError(f"SGP4 propagation error code {e} for TLE")

    x, y, z = r  # ECI km
    lat, lon, alt = _eci_to_geodetic(x, y, z, t_utc)
    return SatellitePosition(lat_deg=lat, lon_deg=lon, alt_km=alt, x_km=x, y_km=y, z_km=z)
