"""
KISS TNC adapter.

KISS (Keep It Simple, Stupid) is the standard TNC-to-computer protocol.
It wraps AX.25 frames with FEND (0xC0) delimiters and uses FESC escaping.

This adapter strips the KISS framing, unescapes special bytes, and delegates
the inner AX.25 frame to AX25Adapter.decode().

Reference: https://www.ax25.net/kiss.aspx

Frame layout:
  FEND | CMD | <AX.25 frame with FESC escaping> | FEND
  0xC0 | 0x00| ...                               | 0xC0

Escaping:
  0xC0 in data → 0xDB 0xDC  (FESC TFEND)
  0xDB in data → 0xDB 0xDD  (FESC TFESC)
"""

from src.ingestion.adapters.ax25 import AX25Adapter
from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.models import CanonicalTelemetry

_FEND = 0xC0
_FESC = 0xDB
_TFEND = 0xDC
_TFESC = 0xDD

_ax25 = AX25Adapter()


class KISSAdapter(ProtocolAdapter):
    @property
    def source_name(self) -> str:
        return "kiss"

    def decode(self, raw: bytes) -> CanonicalTelemetry:
        inner = self._strip_kiss(raw)
        # Delegate to AX.25 adapter, then override source to "kiss"
        canonical = _ax25.decode(inner)
        # Return a copy with source = "kiss" so we know the original framing
        return canonical.model_copy(update={"source": self.source_name})

    @staticmethod
    def _strip_kiss(raw: bytes) -> bytes:
        """Remove KISS FEND delimiters, command byte, and unescape."""
        if len(raw) < 3:
            raise ValueError(f"KISS frame too short: {len(raw)} bytes")

        # Strip leading/trailing FEND bytes
        data = raw
        while data and data[0] == _FEND:
            data = data[1:]
        while data and data[-1] == _FEND:
            data = data[:-1]

        if not data:
            raise ValueError("KISS frame contains only FEND bytes")

        # First byte after FEND stripping is the command byte
        # Upper nibble = port number, lower nibble = command type
        # 0x00 = data frame on port 0 (most common)
        cmd = data[0]
        port = (cmd >> 4) & 0x0F
        cmd_type = cmd & 0x0F

        if cmd_type != 0x00:
            raise ValueError(
                f"KISS command type 0x{cmd_type:X} on port {port} is not a data frame"
            )

        # The rest is the escaped AX.25 frame
        escaped = data[1:]
        return _unescape(escaped)


def _unescape(data: bytes) -> bytes:
    """Reverse KISS FESC escaping."""
    result = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == _FESC:
            if i + 1 >= len(data):
                raise ValueError("KISS frame ends with incomplete FESC escape")
            next_b = data[i + 1]
            if next_b == _TFEND:
                result.append(_FEND)
            elif next_b == _TFESC:
                result.append(_FESC)
            else:
                raise ValueError(
                    f"Invalid KISS escape sequence: 0xDB 0x{next_b:02X}"
                )
            i += 2
        else:
            result.append(b)
            i += 1
    return bytes(result)
