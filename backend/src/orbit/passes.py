"""
Pass prediction: computes when a satellite is visible from a ground station.

Uses SGP4 propagation with 30-second time steps for coarse search,
then bisection for precise AOS/LOS times.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sgp4.api import WGS84, Satrec

from src.orbit.propagator import position_at


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
    """
    Elevation angle (degrees) from ground station to satellite.

    Formula: el = atan2(cos(ρ) - R_E/(R_E+h), sin(ρ))
    where ρ is the great-circle angle between GS and the subsatellite point.
    Returns negative values below the horizon.
    """
    R_E = 6371.0  # km

    φ1, λ1 = math.radians(gs_lat), math.radians(gs_lon)
    φ2, λ2 = math.radians(sat_lat), math.radians(sat_lon)

    Δφ = φ2 - φ1
    Δλ = λ2 - λ1
    a = math.sin(Δφ / 2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2)**2
    rho = 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))  # central angle (radians)

    if rho < 1e-9:   # satellite directly overhead
        return 90.0

    # Include ground station altitude as a small correction to the Earth radius
    r_gs = R_E + gs_elev_m / 1000.0
    el_rad = math.atan2(math.cos(rho) - r_gs / (R_E + sat_alt_km), math.sin(rho))
    return math.degrees(el_rad)


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
    """Predict all passes of a satellite over one ground station within
    horizon_hours. Returns passes where elevation >= station.min_elevation_deg."""
    return predict_passes_multi(
        satellite_id, tle_line1, tle_line2, [station], start, horizon_hours, step_seconds,
    )


@dataclass
class _Track:
    """Per-station state while sweeping through time."""
    station: GroundStation
    aos: datetime | None = None
    max_el: float = 0.0
    az_at_aos: float = 0.0


def predict_passes_multi(
    satellite_id: str,
    tle_line1: str,
    tle_line2: str,
    stations: list[GroundStation],
    start: datetime,
    horizon_hours: int = 24,
    step_seconds: int = 30,
) -> list[PassWindow]:
    """Predict passes over many stations in one sweep.

    The satellite position at each time step is the same for every station,
    so it is propagated once per step (and the TLE parsed once overall);
    only the cheap elevation test runs per station. CPU-bound — call it via
    asyncio.to_thread from async code."""
    sat = Satrec.twoline2rv(tle_line1, tle_line2, WGS84)
    start = start.astimezone(UTC)
    end = start + timedelta(hours=horizon_hours)
    step = timedelta(seconds=step_seconds)
    tracks = [_Track(st) for st in stations]
    passes: list[PassWindow] = []

    def _close(track: _Track, los: datetime) -> None:
        assert track.aos is not None
        passes.append(PassWindow(
            satellite_id=satellite_id,
            station=track.station,
            aos=track.aos,
            los=los,
            max_elevation_deg=round(track.max_el, 2),
            azimuth_at_aos_deg=round(track.az_at_aos, 2),
        ))
        track.aos = None
        track.max_el = 0.0

    t = start
    while t <= end:
        pos = position_at(sat, t)
        for track in tracks:
            st = track.station
            el = _elevation_deg(pos.lat_deg, pos.lon_deg, pos.alt_km,
                                st.lat_deg, st.lon_deg, st.elevation_m)
            if el >= st.min_elevation_deg:
                if track.aos is None:
                    track.aos = t
                    track.az_at_aos = _azimuth_deg(pos.lat_deg, pos.lon_deg, st.lat_deg, st.lon_deg)
                    track.max_el = el
                else:
                    track.max_el = max(track.max_el, el)
            elif track.aos is not None:
                _close(track, t)
        t += step

    # Close passes still open at the horizon end
    for track in tracks:
        if track.aos is not None:
            _close(track, end)

    passes.sort(key=lambda p: p.aos)
    return passes
