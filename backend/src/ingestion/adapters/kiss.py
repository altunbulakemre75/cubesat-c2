"""
KISS TNC adapter skeleton.

KISS (Keep It Simple, Stupid) is the standard TNC-to-computer protocol.
It wraps AX.25 frames with FEND (0xC0) delimiters and FESC escaping.

To implement: strip KISS framing, extract the inner AX.25 frame,
then delegate to AX25Adapter.decode().

Reference: https://www.ax25.net/kiss.aspx
"""

from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.models import CanonicalTelemetry

_FEND = 0xC0
_FESC = 0xDB
_TFEND = 0xDC
_TFESC = 0xDD


class KISSAdapter(ProtocolAdapter):
    @property
    def source_name(self) -> str:
        return "kiss"

    def decode(self, raw: bytes) -> CanonicalTelemetry:
        # TODO (Faz 1.3 followup): strip KISS framing, unescape, delegate to AX25Adapter
        raise NotImplementedError(
            "KISS adapter not yet implemented. "
            "Strip FEND delimiters, unescape FESC sequences, "
            "then pass the inner AX.25 frame to AX25Adapter."
        )
