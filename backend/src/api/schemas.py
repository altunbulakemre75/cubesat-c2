"""Request / response Pydantic schemas for the REST API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.ingestion.models import SatelliteMode


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


# ── Satellites ────────────────────────────────────────────────────────────────

class SatelliteListItem(BaseModel):
    id: str
    name: str
    mode: SatelliteMode | None
    last_seen: datetime | None
    battery_voltage_v: float | None
    norad_id: int | None


class SatelliteDetail(SatelliteListItem):
    description: str
    active: bool
    created_at: datetime


class TLEResponse(BaseModel):
    satellite_id: str
    epoch: datetime
    tle_line1: str
    tle_line2: str


# ── Telemetry ─────────────────────────────────────────────────────────────────

class TelemetryParamsOut(BaseModel):
    battery_voltage_v: float
    temperature_obcs_c: float
    temperature_eps_c: float
    solar_power_w: float
    rssi_dbm: float
    uptime_s: int
    mode: SatelliteMode


class TelemetryPoint(BaseModel):
    timestamp: datetime
    satellite_id: str
    sequence: int
    params: TelemetryParamsOut


# ── Commands ──────────────────────────────────────────────────────────────────

class CommandCreate(BaseModel):
    satellite_id: str
    command_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)
    safe_retry: bool = False
    idempotency_key: str | None = None
    scheduled_at: datetime | None = None


class CommandOut(BaseModel):
    id: str
    satellite_id: str
    command_type: str
    params: dict[str, Any]
    priority: int
    status: str
    safe_retry: bool
    created_by: str | None
    retry_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    scheduled_at: datetime | None
    sent_at: datetime | None
    acked_at: datetime | None


# ── Passes ────────────────────────────────────────────────────────────────────

class PassOut(BaseModel):
    id: int
    satellite_id: str
    station_id: int
    station_name: str
    aos: datetime
    los: datetime
    max_elevation_deg: float
    azimuth_at_aos_deg: float | None


# ── Anomalies ─────────────────────────────────────────────────────────────────

class AnomalyOut(BaseModel):
    id: int
    satellite_id: str
    parameter: str
    value: float
    z_score: float
    severity: str
    detected_at: datetime
    acknowledged: bool
