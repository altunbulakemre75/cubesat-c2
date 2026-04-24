"""
Unit tests for AX25Adapter.

Frames are built locally (no simulator import) so tests have no cross-package deps.
The frame format must match simulator/src/ax25_framer.py — that contract is
verified by the integration test (test_ax25_roundtrip.py, added in Faz 1.6).
"""

import json
from datetime import datetime, timezone

import pytest

from src.ingestion.adapters.ax25 import AX25Adapter
from src.ingestion.models import SatelliteMode

adapter = AX25Adapter()


# --- frame builder (mirrors simulator/src/ax25_framer.py) ---

def _encode_callsign(callsign: str, ssid: int = 0, last: bool = False) -> bytes:
    padded = callsign.upper().ljust(6)[:6]
    body = bytes(ord(c) << 1 for c in padded)
    ssid_byte = 0x60 | ((ssid & 0x0F) << 1) | (0x01 if last else 0x00)
    return body + bytes([ssid_byte])


def _build_frame(payload: dict, dest: str = "GROUND", src: str = "CUBSAT") -> bytes:
    return (
        _encode_callsign(dest, last=False)
        + _encode_callsign(src, last=True)
        + b"\x03\xf0"
        + json.dumps(payload, separators=(",", ":")).encode()
    )


def _valid_payload(**overrides) -> dict:
    base = {
        "satellite_id": "CUBESAT1",
        "mode": "nominal",
        "battery_voltage_v": 3.9,
        "temperature_obcs_c": 25.0,
        "temperature_eps_c": 20.0,
        "solar_power_w": 2.5,
        "rssi_dbm": -95.0,
        "uptime_s": 3600,
        "sequence": 42,
    }
    base.update(overrides)
    return base


# --- happy path ---

def test_decode_returns_canonical_telemetry():
    ct = adapter.decode(_build_frame(_valid_payload()))
    assert ct.satellite_id == "CUBESAT1"
    assert ct.source == "ax25"
    assert ct.sequence == 42


def test_decode_timestamp_is_utc_now():
    before = datetime.now(tz=timezone.utc)
    ct = adapter.decode(_build_frame(_valid_payload()))
    after = datetime.now(tz=timezone.utc)
    assert before <= ct.timestamp <= after


def test_decode_all_satellite_modes():
    for mode in SatelliteMode:
        ct = adapter.decode(_build_frame(_valid_payload(mode=mode.value)))
        assert ct.params.mode == mode


def test_decode_preserves_all_params():
    payload = _valid_payload(
        battery_voltage_v=4.1,
        temperature_obcs_c=35.5,
        temperature_eps_c=-10.0,
        solar_power_w=6.0,
        rssi_dbm=-82.3,
        uptime_s=7200,
    )
    ct = adapter.decode(_build_frame(payload))
    assert ct.params.battery_voltage_v == 4.1
    assert ct.params.temperature_obcs_c == 35.5
    assert ct.params.temperature_eps_c == -10.0
    assert ct.params.solar_power_w == 6.0
    assert ct.params.rssi_dbm == -82.3
    assert ct.params.uptime_s == 7200


def test_source_name_is_ax25():
    assert adapter.source_name == "ax25"


# --- error cases ---

def test_rejects_frame_too_short():
    with pytest.raises(ValueError, match="too short"):
        adapter.decode(b"\x00" * 10)


def test_rejects_frame_exact_header_no_info():
    # 16 bytes = header only, no information field
    with pytest.raises(ValueError, match="too short"):
        adapter.decode(b"\x00" * 16)


def test_rejects_wrong_control_byte():
    frame = bytearray(_build_frame(_valid_payload()))
    frame[14] = 0xFF
    with pytest.raises(ValueError, match="UI"):
        adapter.decode(bytes(frame))


def test_rejects_wrong_pid_byte():
    frame = bytearray(_build_frame(_valid_payload()))
    frame[15] = 0xAB
    with pytest.raises(ValueError, match="PID"):
        adapter.decode(bytes(frame))


def test_rejects_non_utf8_info():
    header = _encode_callsign("GROUND", last=False) + _encode_callsign("CUBSAT", last=True) + b"\x03\xf0"
    with pytest.raises(ValueError, match="UTF-8"):
        adapter.decode(header + b"\xff\xfe invalid")


def test_rejects_invalid_json():
    header = _encode_callsign("GROUND", last=False) + _encode_callsign("CUBSAT", last=True) + b"\x03\xf0"
    with pytest.raises(ValueError, match="JSON"):
        adapter.decode(header + b"not-json-at-all")


def test_rejects_missing_field():
    payload = _valid_payload()
    del payload["battery_voltage_v"]
    with pytest.raises(ValueError, match="Missing"):
        adapter.decode(_build_frame(payload))


def test_rejects_multiple_missing_fields():
    payload = _valid_payload()
    del payload["battery_voltage_v"]
    del payload["temperature_obcs_c"]
    with pytest.raises(ValueError, match="Missing"):
        adapter.decode(_build_frame(payload))


def test_rejects_invalid_mode_value():
    with pytest.raises(ValueError):
        adapter.decode(_build_frame(_valid_payload(mode="hyperspace")))


def test_rejects_battery_voltage_out_of_range():
    with pytest.raises(ValueError):
        adapter.decode(_build_frame(_valid_payload(battery_voltage_v=99.0)))


def test_rejects_rssi_above_zero():
    with pytest.raises(ValueError):
        adapter.decode(_build_frame(_valid_payload(rssi_dbm=10.0)))
