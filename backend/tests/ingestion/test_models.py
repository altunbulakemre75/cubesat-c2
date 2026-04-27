from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.ingestion.models import CanonicalTelemetry, SatelliteMode, TelemetryParams


def _valid_params() -> dict:
    return {
        "battery_voltage_v": 3.9,
        "temperature_obcs_c": 25.0,
        "temperature_eps_c": 20.0,
        "solar_power_w": 2.5,
        "rssi_dbm": -95.0,
        "uptime_s": 3600,
        "mode": "nominal",
    }


def _valid_telemetry(**overrides) -> dict:
    base = {
        "timestamp": datetime.now(tz=timezone.utc),
        "satellite_id": "CUBESAT1",
        "source": "ax25",
        "sequence": 42,
        "params": _valid_params(),
    }
    base.update(overrides)
    return base


def test_valid_canonical_telemetry():
    ct = CanonicalTelemetry(**_valid_telemetry())
    assert ct.satellite_id == "CUBESAT1"
    assert ct.params.mode == SatelliteMode.NOMINAL
    assert ct.sequence == 42


def test_all_satellite_modes_accepted():
    for mode in SatelliteMode:
        params = _valid_params()
        params["mode"] = mode.value
        ct = CanonicalTelemetry(**_valid_telemetry(params=params))
        assert ct.params.mode == mode


def test_battery_voltage_above_max_rejected():
    # Range raised from 5 V to 20 V to support 1S–5S Li-ion / LiPo packs.
    params = _valid_params()
    params["battery_voltage_v"] = 25.0
    with pytest.raises(ValidationError):
        CanonicalTelemetry(**_valid_telemetry(params=params))


def test_battery_voltage_2s_lipo_accepted():
    """2S LiPo packs (~7.4 V nominal, 8.4 V full) must validate."""
    params = _valid_params()
    params["battery_voltage_v"] = 7.4
    ct = CanonicalTelemetry(**_valid_telemetry(params=params))
    assert ct.params.battery_voltage_v == 7.4


def test_battery_voltage_below_min_rejected():
    params = _valid_params()
    params["battery_voltage_v"] = -1.0
    with pytest.raises(ValidationError):
        CanonicalTelemetry(**_valid_telemetry(params=params))


def test_rssi_above_zero_rejected():
    params = _valid_params()
    params["rssi_dbm"] = 5.0
    with pytest.raises(ValidationError):
        CanonicalTelemetry(**_valid_telemetry(params=params))


def test_negative_uptime_rejected():
    params = _valid_params()
    params["uptime_s"] = -1
    with pytest.raises(ValidationError):
        CanonicalTelemetry(**_valid_telemetry(params=params))


def test_invalid_mode_rejected():
    params = _valid_params()
    params["mode"] = "warp_drive"
    with pytest.raises(ValidationError):
        CanonicalTelemetry(**_valid_telemetry(params=params))


def test_empty_satellite_id_rejected():
    with pytest.raises(ValidationError):
        CanonicalTelemetry(**_valid_telemetry(satellite_id=""))


def test_negative_sequence_rejected():
    with pytest.raises(ValidationError):
        CanonicalTelemetry(**_valid_telemetry(sequence=-1))


def test_telemetry_params_boundary_values():
    """Edge values at bounds should be accepted."""
    params = _valid_params()
    params["battery_voltage_v"] = 0.0
    params["rssi_dbm"] = -150.0
    params["uptime_s"] = 0
    ct = CanonicalTelemetry(**_valid_telemetry(params=params))
    assert ct.params.battery_voltage_v == 0.0
