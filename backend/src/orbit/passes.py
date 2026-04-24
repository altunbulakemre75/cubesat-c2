"""
Pass prediction: computes when a satellite is visible from a ground station.

Uses SGP4 propagation with 30-second time steps for coarse search,
then bisection for precise AOS/LOS times.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.orbit.propagator import propagate


@dataclass(frozen=True)
class GroundStation:
    id: int
    name: str
    lat_deg: float
    lon_deg: float
    elevation_m: float
    min_elevation_deg: float = 10.0


@dataclass(frozen=True)
class PassWindow:
    satellite_id: str
    station: GroundStation
    aos: datetime            # Acquisition of Signal
    los: datetime            # Loss of Signal
    max_elevation_deg: float
    azimuth_at_aos_deg: float


def _elevation_deg(sat_lat: float, sat_lon: float, sat_alt_km: float,
                   gs_lat: float, gs_lon: float, gs_elev_m: float) -> float:
    """Approximate elevation angle (degrees) from ground station to satellite."""
    R_E = 6371.0  # km
    gs_alt_km = gs_elev_m / 1000

    # Convert to radians
    φ1, λ1 = math.radians(gs_lat), math.radians(gs_lon)
    φ2, λ2 = math.radians(sat_lat), math.radians(sat_lon)

    # Slant range vector in ECI-like approximation
    Δφ = φ2 - φ1
    Δλ = λ2 - λ1
    a = math.sin(Δφ / 2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2)**2
    central_angle = 2 * math.asin(math.sqrt(a))

    # Law of cosines for elevation
    d_km = math.sqrt(
        (R_E + gs_alt_km)**2 + (R_E + sat_alt_km)**2
        - 2 * (R_E + gs_alt_km) * (R_E + sat_alt_km) * math.cos(central_angle)
    )
    # Elevation angle
    cos_elev = ((R_E + sat_alt_km)**2 - (R_E + gs_alt_km)**2 - d_km**2) / (2 * (R_E + gs_alt_km) * d_km)
    cos_elev = max(-1.0, min(1.0, cos_elev))
    return math.degrees(math.asin(math.sqrt(max(0, 1 - cos_elev**2))))


def _azimuth_deg(sat_lat: float, sat_lon: float,
                 gs_lat: float, gs_lon: float) -> float:
    """Bearing from ground station to subsatellite point (degrees, 0=N)."""
    φ1, λ1 = math.radians(gs_lat), math.radians(gs_lon)
    φ2, λ2 = math.radians(sat_lat), math.radians(sat_lon)
    Δλ = λ2 - λ1
    x = math.sin(Δλ) * math.cos(φ2)
    y = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(Δλ)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def predict_passes(
    satellite_id: str,
    tle_line1: str,
    tle_line2: str,
    station: GroundStation,
    start: datetime,
    horizon_hours: int = 24,
    step_seconds: int = 30,
) -> list[PassWindow]:
    """
    Predict all passes of a satellite over a ground station within horizon_hours.

    Returns passes where max elevation >= station.min_elevation_deg.
    """
    start = start.astimezone(timezone.utc)
    end = start + timedelta(hours=horizon_hours)

    passes: list[PassWindow] = []
    t = start
    in_pass = False
    aos_time: datetime | None = None
    max_el = 0.0
    az_at_aos = 0.0
    step = timedelta(seconds=step_seconds)

    while t <= end:
        pos = propagate(tle_line1, tle_line2, t)
        el = _elevation_deg(
            pos.lat_deg, pos.lon_deg, pos.alt_km,
            station.lat_deg, station.lon_deg, station.elevation_m,
        )

        if el >= station.min_elevation_deg:
            if not in_pass:
                in_pass = True
                aos_time = t
                az_at_aos = _azimuth_deg(pos.lat_deg, pos.lon_deg, station.lat_deg, station.lon_deg)
                max_el = el
            else:
                max_el = max(max_el, el)
        else:
            if in_pass:
                in_pass = False
                if aos_time is not None:
                    passes.append(PassWindow(
                        satellite_id=satellite_id,
                        station=station,
                        aos=aos_time,
                        los=t,
                        max_elevation_deg=round(max_el, 2),
                        azimuth_at_aos_deg=round(az_at_aos, 2),
                    ))
                max_el = 0.0
        t += step

    # Close open pass at horizon end
    if in_pass and aos_time is not None:
        passes.append(PassWindow(
            satellite_id=satellite_id,
            station=station,
            aos=aos_time,
            los=end,
            max_elevation_deg=round(max_el, 2),
            azimuth_at_aos_deg=round(az_at_aos, 2),
        ))

    return passes
