"""
Bug #6: AX25Adapter.decode with exactly HEADER_LEN=16 bytes passes the
length check but the empty info field crashes with a misleading error.

The header check currently is `if len(raw) <= _HEADER_LEN`, which does
protect empty-info frames — but verify: a 17-byte frame (header + 1 byte
info that's not valid JSON) should produce a *clear* decode error.
"""

import pytest

from src.ingestion.adapters.ax25 import AX25Adapter

adapter = AX25Adapter()


def _minimal_header() -> bytes:
    """16-byte valid AX.25 header (dest + src callsigns + control + pid)."""
    from_chars = bytes(ord(c) << 1 for c in "GROUND")
    to_chars = bytes(ord(c) << 1 for c in "CUBSAT")
    return from_chars + b"\x60" + to_chars + b"\x61" + b"\x03\xf0"


def test_one_byte_info_decoded_with_clear_error():
    """A 17-byte frame (valid header + 1 byte of info) must produce a specific
    'Information field' decode error, not a cryptic one."""
    frame = _minimal_header() + b"\xff"  # invalid UTF-8 byte
    with pytest.raises(ValueError) as exc:
        adapter.decode(frame)
    msg = str(exc.value)
    assert "UTF-8" in msg or "utf" in msg.lower(), f"Expected UTF-8 error, got: {msg}"


def test_whitespace_only_info_decoded_as_json_error():
    """Info field that's just whitespace is not valid JSON → must be 422-worthy."""
    frame = _minimal_header() + b"   \t\n  "
    with pytest.raises(ValueError) as exc:
        adapter.decode(frame)
    assert "JSON" in str(exc.value)


def test_null_byte_in_info_rejected():
    """A null byte in the info field shouldn't hang parsing."""
    frame = _minimal_header() + b"\x00\x00\x00"
    with pytest.raises(ValueError):
        adapter.decode(frame)
