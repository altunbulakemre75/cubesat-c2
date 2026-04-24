import json

import pytest

from src.ax25_framer import CONTROL_UI, PID_NO_L3, build_frame, encode_callsign, parse_frame
from src.satellite import CubeSat


def _make_telemetry(sat_id: str = "TEST1"):
    return CubeSat(sat_id).tick()


# --- encode_callsign ---

def test_callsign_encodes_to_7_bytes():
    assert len(encode_callsign("GROUND")) == 7


def test_callsign_chars_are_left_shifted():
    result = encode_callsign("GROUND")
    for i, char in enumerate("GROUND"):
        assert result[i] == ord(char) << 1


def test_short_callsign_is_padded():
    result = encode_callsign("AB")
    # Positions 2-5 should be space (0x20) << 1 = 0x40
    assert result[2] == 0x40
    assert result[3] == 0x40


def test_last_flag_sets_lsb_of_ssid_byte():
    last = encode_callsign("GROUND", last=True)
    not_last = encode_callsign("GROUND", last=False)
    assert last[6] & 0x01 == 1
    assert not_last[6] & 0x01 == 0


# --- build_frame / parse_frame roundtrip ---

def test_frame_minimum_length():
    frame = build_frame(_make_telemetry())
    # 7 (dest) + 7 (src) + 1 (ctrl) + 1 (pid) + at least 1 byte info
    assert len(frame) >= 17


def test_frame_control_byte_at_offset_14():
    frame = build_frame(_make_telemetry())
    assert frame[14] == CONTROL_UI


def test_frame_pid_byte_at_offset_15():
    frame = build_frame(_make_telemetry())
    assert frame[15] == PID_NO_L3


def test_frame_destination_callsign():
    frame = build_frame(_make_telemetry(), dest="GROUND")
    expected = bytes(ord(c) << 1 for c in "GROUND")
    assert frame[:6] == expected


def test_roundtrip_satellite_id():
    tm = _make_telemetry("CUBSAT")
    payload = parse_frame(build_frame(tm))
    assert payload["satellite_id"] == "CUBSAT"


def test_roundtrip_all_fields():
    tm = _make_telemetry()
    payload = parse_frame(build_frame(tm))

    assert payload["mode"] == tm.mode.value
    assert payload["battery_voltage_v"] == tm.battery_voltage_v
    assert payload["temperature_obcs_c"] == tm.temperature_obcs_c
    assert payload["temperature_eps_c"] == tm.temperature_eps_c
    assert payload["solar_power_w"] == tm.solar_power_w
    assert payload["rssi_dbm"] == tm.rssi_dbm
    assert payload["uptime_s"] == tm.uptime_s
    assert payload["sequence"] == tm.sequence


def test_parse_rejects_too_short_frame():
    with pytest.raises(ValueError, match="too short"):
        parse_frame(b"\x00" * 10)


def test_parse_rejects_wrong_control_byte():
    frame = bytearray(build_frame(_make_telemetry()))
    frame[14] = 0xFF
    with pytest.raises(ValueError, match="control"):
        parse_frame(bytes(frame))


def test_parse_rejects_wrong_pid_byte():
    frame = bytearray(build_frame(_make_telemetry()))
    frame[15] = 0xAB
    with pytest.raises(ValueError, match="PID"):
        parse_frame(bytes(frame))
