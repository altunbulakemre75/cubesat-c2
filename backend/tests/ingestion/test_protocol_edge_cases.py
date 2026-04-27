"""
Adversarial protocol-adapter tests.

Sentetik test verisiyle değil, gerçekte ortaya çıkacak senaryolarla:
truncated frames, malformed escapes, wrong header flags, empty payloads.
A real CC1101/SDR will produce these all the time.
"""

from __future__ import annotations

import json

import pytest

from src.ingestion.adapters.ax25 import AX25Adapter
from src.ingestion.adapters.ccsds import CCSDSAdapter, build_ccsds_packet
from src.ingestion.adapters.kiss import KISSAdapter, _unescape


_VALID_PAYLOAD = json.dumps({
    "satellite_id": "AO91", "mode": "nominal",
    "battery_voltage_v": 3.9, "temperature_obcs_c": 25.0,
    "temperature_eps_c": 22.0, "solar_power_w": 2.5,
    "rssi_dbm": -90, "uptime_s": 12345, "sequence": 42,
}).encode()


def _valid_ax25(info: bytes = _VALID_PAYLOAD) -> bytes:
    """6-byte dest + 6-byte src + 1-byte SSIDs + control + PID + info."""
    dest = b"DEST  " + b"\x00"
    src = b"SRC   " + b"\x01"
    return dest + src + bytes([0x03, 0xF0]) + info


# ─────────────────────────────────────────────────────────────────────
# AX.25
# ─────────────────────────────────────────────────────────────────────

def test_ax25_empty_bytes():
    with pytest.raises(ValueError, match="too short"):
        AX25Adapter().decode(b"")


def test_ax25_header_only_no_payload():
    """16-byte frame is just the header, no info field."""
    with pytest.raises(ValueError, match="too short"):
        AX25Adapter().decode(_valid_ax25(b"")[:16])


def test_ax25_invalid_utf8_in_info_field():
    bad = b"\xc3\x28\xa0\xa1"  # invalid UTF-8 byte sequence
    frame = _valid_ax25(bad)
    with pytest.raises(ValueError, match="UTF-8|JSON"):
        AX25Adapter().decode(frame)


def test_ax25_wrong_pid_byte_rejected():
    """PID byte at offset 15 must be 0xF0 — anything else is rejected."""
    frame = bytearray(_valid_ax25())
    frame[15] = 0xCC
    with pytest.raises(ValueError, match="PID"):
        AX25Adapter().decode(bytes(frame))


def test_ax25_missing_required_field_raises():
    incomplete = json.dumps({"satellite_id": "X"}).encode()  # missing everything else
    with pytest.raises(ValueError, match="Missing"):
        AX25Adapter().decode(_valid_ax25(incomplete))


# ─────────────────────────────────────────────────────────────────────
# KISS
# ─────────────────────────────────────────────────────────────────────

def test_kiss_only_fend_bytes_rejected():
    """Frame containing nothing but FENDs should not crash, must raise."""
    with pytest.raises(ValueError):
        KISSAdapter().decode(b"\xc0\xc0\xc0")


def test_kiss_incomplete_fesc_at_end_rejected():
    """FESC at the very end of the data is malformed — must not silently
    discard or buffer-overrun."""
    bad = b"\xdb"  # FESC then nothing
    with pytest.raises(ValueError, match="FESC"):
        _unescape(bad)


def test_kiss_invalid_escape_sequence_rejected():
    """FESC must be followed by TFEND (0xDC) or TFESC (0xDD). Anything
    else is malformed."""
    bad = b"\xdb\xff"  # FESC + invalid byte
    with pytest.raises(ValueError, match="escape"):
        _unescape(bad)


def test_kiss_unescape_round_trip():
    """Real escape: 0xC0 in payload → 0xDB 0xDC; 0xDB → 0xDB 0xDD."""
    original = b"\xc0middle\xdbend"
    escaped = b"\xdb\xdcmiddle\xdb\xddend"
    assert _unescape(escaped) == original


def test_kiss_command_type_other_than_data_rejected():
    """Lower nibble of command byte != 0x00 is not a data frame."""
    # FEND + cmd 0x01 (set tx delay) + dummy + FEND
    with pytest.raises(ValueError, match="command type"):
        KISSAdapter().decode(b"\xc0\x01dummy\xc0")


# ─────────────────────────────────────────────────────────────────────
# CCSDS
# ─────────────────────────────────────────────────────────────────────

def test_ccsds_packet_too_short_for_primary_header():
    with pytest.raises(ValueError, match="too short"):
        CCSDSAdapter().decode(b"\x00\x01")


def test_ccsds_wrong_version_rejected():
    """Version field must be 0 (CCSDS spec). Build a packet with version=1."""
    payload = _VALID_PAYLOAD
    pkt = bytearray(build_ccsds_packet(apid=42, seq_count=1, payload_json=payload))
    # Version is bits 15-13 of word0. Set version = 1.
    pkt[0] = pkt[0] | 0b00100000
    with pytest.raises(ValueError, match="version"):
        CCSDSAdapter().decode(bytes(pkt))


def test_ccsds_telecommand_type_rejected_on_telemetry_path():
    """Type bit = 1 means TC, not TM. We're a telemetry decoder; reject."""
    pkt = bytearray(build_ccsds_packet(apid=42, seq_count=1, payload_json=_VALID_PAYLOAD))
    # Type bit is bit 12 of word0
    pkt[0] = pkt[0] | 0b00010000
    with pytest.raises(ValueError, match="telecommand"):
        CCSDSAdapter().decode(bytes(pkt))


def test_ccsds_length_field_mismatch_rejected():
    """Header says N bytes but actual frame is shorter — must catch."""
    pkt = bytearray(build_ccsds_packet(apid=42, seq_count=1, payload_json=_VALID_PAYLOAD))
    # Truncate by 5 bytes — header still claims original length
    truncated = bytes(pkt[:-5])
    with pytest.raises(ValueError, match="length mismatch"):
        CCSDSAdapter().decode(truncated)


def test_ccsds_secondary_header_consumed_then_user_data_parsed():
    """When sec_hdr_flag=1, the next 6 bytes are skipped and JSON starts
    after them. Round-trip should work."""
    pkt = build_ccsds_packet(apid=42, seq_count=1,
                             payload_json=_VALID_PAYLOAD,
                             secondary_header=True)
    canonical = CCSDSAdapter().decode(pkt)
    assert canonical.satellite_id == "AO91"


def test_ccsds_apid_falls_back_when_no_satellite_id_in_payload():
    """If the JSON payload omits satellite_id, decoder uses APID-{n}."""
    payload = json.dumps({
        "mode": "nominal",
        "battery_voltage_v": 3.9, "temperature_obcs_c": 25.0,
        "temperature_eps_c": 22.0, "solar_power_w": 2.5,
        "rssi_dbm": -90, "uptime_s": 1, "sequence": 1,
    }).encode()
    pkt = build_ccsds_packet(apid=257, seq_count=1, payload_json=payload)
    canonical = CCSDSAdapter().decode(pkt)
    assert canonical.satellite_id == "APID-257"
