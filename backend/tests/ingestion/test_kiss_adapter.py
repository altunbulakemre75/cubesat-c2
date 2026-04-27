"""
KISS adapter tests.

Builds KISS-wrapped AX.25 frames and verifies the adapter
correctly strips framing, unescapes, and decodes the telemetry.
"""

import json

import pytest

from src.ingestion.adapters.kiss import KISSAdapter, _unescape, _FEND, _FESC, _TFEND, _TFESC


def _make_ax25_frame() -> bytes:
    """Build a minimal valid AX.25 UI frame with JSON telemetry payload."""
    # Destination address (7 bytes): "GROUND" callsign shifted left + SSID
    dest = bytes(ord(c) << 1 for c in "GROUND") + bytes([0x60])
    # Source address (7 bytes): "TEST1 " callsign shifted left + SSID + end-of-address
    src = bytes(ord(c) << 1 for c in "TEST1 ") + bytes([0x61])
    # Control (UI frame) + PID (no L3)
    control_pid = bytes([0x03, 0xF0])
    # JSON telemetry payload
    payload = json.dumps({
        "satellite_id": "TEST1",
        "mode": "nominal",
        "battery_voltage_v": 3.9,
        "temperature_obcs_c": 25.0,
        "temperature_eps_c": 20.0,
        "solar_power_w": 4.5,
        "rssi_dbm": -95.0,
        "uptime_s": 1234,
        "sequence": 42,
    }, separators=(",", ":")).encode("utf-8")
    return dest + src + control_pid + payload


def _wrap_kiss(ax25_frame: bytes, cmd: int = 0x00) -> bytes:
    """Wrap an AX.25 frame in KISS framing."""
    return bytes([_FEND, cmd]) + ax25_frame + bytes([_FEND])


class TestUnescape:
    """KISS byte unescaping."""

    def test_no_escaping(self):
        data = b"\x01\x02\x03"
        assert _unescape(data) == data

    def test_fend_escape(self):
        """FESC+TFEND → FEND (0xC0)."""
        data = bytes([_FESC, _TFEND])
        assert _unescape(data) == bytes([_FEND])

    def test_fesc_escape(self):
        """FESC+TFESC → FESC (0xDB)."""
        data = bytes([_FESC, _TFESC])
        assert _unescape(data) == bytes([_FESC])

    def test_mixed_escaping(self):
        data = bytes([0x01, _FESC, _TFEND, 0x02, _FESC, _TFESC, 0x03])
        assert _unescape(data) == bytes([0x01, _FEND, 0x02, _FESC, 0x03])

    def test_incomplete_escape_raises(self):
        with pytest.raises(ValueError, match="incomplete FESC"):
            _unescape(bytes([0x01, _FESC]))

    def test_invalid_escape_raises(self):
        with pytest.raises(ValueError, match="Invalid KISS escape"):
            _unescape(bytes([_FESC, 0x00]))


class TestKISSAdapter:
    """End-to-end KISS decode."""

    def test_decode_valid_frame(self):
        adapter = KISSAdapter()
        ax25 = _make_ax25_frame()
        kiss_frame = _wrap_kiss(ax25)
        canonical = adapter.decode(kiss_frame)
        assert canonical.source == "kiss"
        assert canonical.satellite_id == "TEST1"

    def test_source_name(self):
        assert KISSAdapter().source_name == "kiss"

    def test_frame_too_short(self):
        adapter = KISSAdapter()
        with pytest.raises(ValueError, match="too short"):
            adapter.decode(bytes([_FEND, _FEND]))

    def test_non_data_command_rejected(self):
        """Command type != 0x00 should be rejected."""
        adapter = KISSAdapter()
        ax25 = _make_ax25_frame()
        # cmd = 0x01 (not a data frame)
        kiss_frame = _wrap_kiss(ax25, cmd=0x01)
        with pytest.raises(ValueError, match="not a data frame"):
            adapter.decode(kiss_frame)

    def test_multiple_fend_stripped(self):
        """Multiple leading/trailing FEND bytes should be handled."""
        adapter = KISSAdapter()
        ax25 = _make_ax25_frame()
        kiss_frame = bytes([_FEND, _FEND, 0x00]) + ax25 + bytes([_FEND, _FEND])
        canonical = adapter.decode(kiss_frame)
        assert canonical.source == "kiss"

    def test_telemetry_values_preserved(self):
        """Verify the decoded telemetry contains correct values."""
        adapter = KISSAdapter()
        ax25 = _make_ax25_frame()
        kiss_frame = _wrap_kiss(ax25)
        canonical = adapter.decode(kiss_frame)
        assert canonical.params.battery_voltage_v == 3.9
        assert canonical.params.temperature_obcs_c == 25.0
        assert canonical.params.mode.value == "nominal"
        assert canonical.sequence == 42
