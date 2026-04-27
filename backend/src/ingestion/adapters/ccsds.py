"""
CCSDS Space Packet adapter (CCSDS 133.0-B-2).

The Space Packet has a 6-byte primary header followed by an optional
secondary header and the user data field.

Primary header (16-bit big-endian word + 16-bit + 16-bit):
  | Version (3) | Type (1) | SecHdr flag (1) | APID (11) |
  | Sequence flags (2) | Sequence count (14)             |
  | Packet data length (16)                              |

Secondary header (optional, controlled by SecHdr flag):
  | Coarse time (32) | Fine time (16) |   ← typical CUC time format

User data field:
  Mission-specific binary. For our reference implementation we expect a
  small JSON object inside the user data field — the same payload schema
  the simulator and AX25Adapter use. Real missions will swap this for a
  binary unpacker.
"""

import json
import struct
from datetime import datetime, timezone

from pydantic import ValidationError

from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.models import CanonicalTelemetry, TelemetryParams

_PRIMARY_HEADER_LEN = 6


class CCSDSAdapter(ProtocolAdapter):
    @property
    def source_name(self) -> str:
        return "ccsds"

    def decode(self, raw: bytes) -> CanonicalTelemetry:
        if len(raw) < _PRIMARY_HEADER_LEN:
            raise ValueError(
                f"CCSDS packet too short: {len(raw)} bytes "
                f"(min {_PRIMARY_HEADER_LEN} primary header)"
            )

        # Parse primary header
        word0, word1, word2 = struct.unpack(">HHH", raw[:6])
        version = (word0 >> 13) & 0x07
        pkt_type = (word0 >> 12) & 0x01
        sec_hdr_flag = (word0 >> 11) & 0x01
        apid = word0 & 0x07FF
        # seq_flags (bits 15-14 of word1) ignored — we accept any seq flag
        seq_count = word1 & 0x3FFF
        data_length = word2  # actually data_length - 1 per spec

        if version != 0:
            raise ValueError(f"CCSDS version {version} unsupported (expected 0)")
        if pkt_type != 0:
            raise ValueError("CCSDS packet is a telecommand, not telemetry")

        expected_total = _PRIMARY_HEADER_LEN + data_length + 1
        if len(raw) != expected_total:
            raise ValueError(
                f"CCSDS length mismatch: header says {expected_total}, "
                f"got {len(raw)}"
            )

        # Skip secondary header (6 bytes CUC) if present
        offset = _PRIMARY_HEADER_LEN
        if sec_hdr_flag:
            if len(raw) < offset + 6:
                raise ValueError("CCSDS secondary header flag set but packet too short")
            offset += 6

        user_data = raw[offset:]
        if not user_data:
            raise ValueError("CCSDS user data field empty")

        try:
            payload = json.loads(user_data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"CCSDS user data not UTF-8: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"CCSDS user data not JSON: {exc}") from exc

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
        except (KeyError, ValidationError) as exc:
            raise ValueError(f"CCSDS payload validation failed: {exc}") from exc

        return CanonicalTelemetry(
            timestamp=datetime.now(tz=timezone.utc),
            satellite_id=str(payload.get("satellite_id", f"APID-{apid}")),
            source=self.source_name,
            sequence=int(payload.get("sequence", seq_count)),
            params=params,
        )


def build_ccsds_packet(
    apid: int,
    seq_count: int,
    payload_json: bytes,
    secondary_header: bool = False,
) -> bytes:
    """Helper for tests: build a valid CCSDS packet around a JSON payload."""
    sec_hdr_bytes = b""
    if secondary_header:
        sec_hdr_bytes = b"\x00\x00\x00\x00\x00\x00"  # CUC time, all zeros for tests

    user_data = sec_hdr_bytes + payload_json
    data_length = len(user_data) - 1   # CCSDS spec: stored length = N-1

    word0 = (0 << 13) | (0 << 12) | ((1 if secondary_header else 0) << 11) | (apid & 0x07FF)
    word1 = (0b11 << 14) | (seq_count & 0x3FFF)  # standalone packet
    word2 = data_length & 0xFFFF

    header = struct.pack(">HHH", word0, word1, word2)
    return header + user_data
