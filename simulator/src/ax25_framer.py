"""
Minimal AX.25 UI frame builder for the simulator.

Produces simplified AX.25 frames: no HDLC bit-stuffing, no CRC.
The C2 AX.25 adapter (backend/src/ingestion/adapters/ax25.py) expects
exactly this structure when decoding simulator frames.

Frame layout (bytes):
  [0:7]   Destination address (6-char callsign + SSID byte)
  [7:14]  Source address      (6-char callsign + SSID byte)
  [14]    Control byte        (0x03 = UI frame)
  [15]    PID byte            (0xF0 = no layer 3)
  [16:]   Information field   (UTF-8 JSON payload)
"""

import json

from src.satellite import SimulatedTelemetry

CONTROL_UI = 0x03
PID_NO_L3 = 0xF0
GROUND_CALLSIGN = "GROUND"


def encode_callsign(callsign: str, ssid: int = 0, last: bool = False) -> bytes:
    """
    Encode a callsign into 7 bytes per AX.25 spec.
    Each character is left-shifted by 1; the SSID byte carries end-of-address flag.
    """
    padded = callsign.upper().ljust(6)[:6]
    body = bytes(ord(c) << 1 for c in padded)
    # bits: 0110 SSID 0/1  (0x60 sets reserved bits, last bit = end-of-address)
    ssid_byte = 0x60 | ((ssid & 0x0F) << 1) | (0x01 if last else 0x00)
    return body + bytes([ssid_byte])


def build_frame(telemetry: SimulatedTelemetry, dest: str = GROUND_CALLSIGN) -> bytes:
    """Build an AX.25 UI frame carrying the telemetry as a JSON information field."""
    dest_addr = encode_callsign(dest, ssid=0, last=False)
    src_addr = encode_callsign(telemetry.satellite_id[:6], ssid=0, last=True)

    payload: dict = {
        "satellite_id": telemetry.satellite_id,
        "mode": telemetry.mode.value,
        "battery_voltage_v": telemetry.battery_voltage_v,
        "temperature_obcs_c": telemetry.temperature_obcs_c,
        "temperature_eps_c": telemetry.temperature_eps_c,
        "solar_power_w": telemetry.solar_power_w,
        "rssi_dbm": telemetry.rssi_dbm,
        "uptime_s": telemetry.uptime_s,
        "sequence": telemetry.sequence,
    }
    info = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return dest_addr + src_addr + bytes([CONTROL_UI, PID_NO_L3]) + info


def parse_frame(data: bytes) -> dict:
    """
    Decode a frame produced by build_frame().
    Returns the JSON payload as a dict.
    Raises ValueError on malformed input.
    """
    if len(data) < 17:
        raise ValueError(f"Frame too short: {len(data)} bytes")
    if data[14] != CONTROL_UI:
        raise ValueError(f"Unexpected control byte: 0x{data[14]:02X}")
    if data[15] != PID_NO_L3:
        raise ValueError(f"Unexpected PID byte: 0x{data[15]:02X}")

    info = data[16:]
    try:
        return json.loads(info.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Bad information field: {exc}") from exc
