from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SatelliteMode(str, Enum):
    BEACON = "beacon"
    DEPLOYMENT = "deployment"
    NOMINAL = "nominal"
    SCIENCE = "science"
    SAFE = "safe"


class TelemetryParams(BaseModel):
    """Physical telemetry values. Units are explicit in field names."""

    # 0-20 V covers 1S-5S Li-ion / LiPo packs typical of CubeSats up to 12U.
    # Per-mission BatteryBar in the UI scales display to the actual chemistry.
    battery_voltage_v: float = Field(..., ge=0.0, le=20.0)
    temperature_obcs_c: float = Field(..., ge=-100.0, le=100.0)
    temperature_eps_c: float = Field(..., ge=-100.0, le=100.0)
    solar_power_w: float = Field(..., ge=0.0, le=20.0)
    rssi_dbm: float = Field(..., ge=-150.0, le=0.0)
    uptime_s: int = Field(..., ge=0)
    mode: SatelliteMode


class CanonicalTelemetry(BaseModel):
    """
    Single telemetry format regardless of source protocol.
    All adapters produce this; all consumers read this.
    """

    timestamp: datetime
    satellite_id: str = Field(..., min_length=1, max_length=64)
    source: str = Field(..., description="ax25 | kiss | ccsds | simulated")
    sequence: int = Field(..., ge=0)
    params: TelemetryParams
