"""
AX.25 UI frame adapter.

Decodes the simplified AX.25 frames produced by the simulator and any real
ground station running gr-satellites or a compatible TNC.

Frame layout (matches simulator/src/ax25_framer.py):
  [0:7]   Destination address (6-char callsign shifted left + SSID byte)
  [7:14]  Source address      (same encoding, last bit = 1)
  [14]    Control byte        (0x03 = UI frame)
  [15]    PID byte            (0xF0 = no layer 3)
  [16:]   Information field   (UTF-8 JSON telemetry payload)
"""

import json
from datetime import datetime, timezone

from pydantic import ValidationError

from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.models import CanonicalTelemetry, TelemetryParams

_CONTROL_UI = 0x03
_PID_NO_L3 = 0xF0
_HEADER_LEN = 16  # dest(7) + src(7) + control(1) + pid(1)

_REQUIRED_FIELDS = frozenset({
    "satellite_id", "mode", "battery_voltage_v", "temperature_obcs_c",
    "temperature_eps_c", "solar_power_w", "rssi_dbm", "uptime_s", "sequence",
})


class AX25Adapter(ProtocolAdapter):
    @property
    def source_name(self) -> str:
        return "ax25"

    def decode(self, raw: bytes) -> CanonicalTelemetry:
        self._validate_header(raw)
        payload = self._parse_info(raw[_HEADER_LEN:])
        return self._to_canonical(payload)

    # --- private ---

    @staticmethod
    def _validate_header(raw: bytes) -> None:
        if len(raw) <= _HEADER_LEN:
            raise ValueError(f"AX.25 frame too short: {len(raw)} bytes (min {_HEADER_LEN + 1})")
        if raw[14] != _CONTROL_UI:
            raise ValueError(f"Not a UI frame: control byte is 0x{raw[14]:02X}, expected 0x{_CONTROL_UI:02X}")
        if raw[15] != _PID_NO_L3:
            raise ValueError(f"Unexpected PID byte: 0x{raw[15]:02X}, expected 0x{_PID_NO_L3:02X}")

    @staticmethod
    def _parse_info(info: bytes) -> dict:
        try:
            payload = json.loads(info.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"Information field is not valid UTF-8: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON parse error in information field: {exc}") from exc

        missing = _REQUIRED_FIELDS - payload.keys()
        if missing:
            raise ValueError(f"Missing telemetry fields: {sorted(missing)}")

        return payload

    def _to_canonical(self, payload: dict) -> CanonicalTelemetry:
        try:
            params = TelemetryParams(
                battery_voltage_v=payload["battery_voltage_v"],
                temperature_obcs_c=payload["temperature_obcs_c"],
                temperature_eps_c=payload["temperature_eps_c"],
                solar_power_w=payload["solar_power_w"],
                rssi_dbm=payload["rssi_dbm"],
                uptime_s=payload["uptime_s"],
                mode=payload["mode"],
            )
        except ValidationError as exc:
            raise ValueError(f"Telemetry parameter validation failed: {exc}") from exc

        return CanonicalTelemetry(
            timestamp=datetime.now(tz=timezone.utc),
            satellite_id=str(payload["satellite_id"]),
            source=self.source_name,
            sequence=int(payload["sequence"]),
            params=params,
        )
