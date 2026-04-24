"""
CCSDS Space Packet adapter skeleton.

CCSDS (Consultative Committee for Space Data Systems) packets are used
by most professional CubeSat missions (ECSS-E-ST-70-41C standard).

Primary header (6 bytes):
  - Version (3 bits)
  - Type (1 bit): 0=telemetry, 1=telecommand
  - Secondary header flag (1 bit)
  - APID (11 bits): Application Process Identifier — identifies subsystem
  - Sequence flags (2 bits)
  - Packet sequence count (14 bits)
  - Packet data length (16 bits): total length - 7

To implement: parse primary header, optionally secondary header,
extract user data field, map APID → telemetry parameter set.

Reference: CCSDS 133.0-B-2 (Space Packet Protocol)
"""

from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.models import CanonicalTelemetry

_PRIMARY_HEADER_LEN = 6


class CCSDSAdapter(ProtocolAdapter):
    @property
    def source_name(self) -> str:
        return "ccsds"

    def decode(self, raw: bytes) -> CanonicalTelemetry:
        # TODO (Faz 1.3 followup): parse CCSDS primary/secondary header,
        # map APID to parameter set, extract engineering values.
        raise NotImplementedError(
            "CCSDS adapter not yet implemented. "
            "Parse 6-byte primary header, extract APID and sequence count, "
            "then map the user data field to TelemetryParams."
        )
