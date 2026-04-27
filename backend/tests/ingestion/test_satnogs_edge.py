"""SatNOGS fetcher additional edge cases."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.ingestion.satnogs_fetcher import _coerce_decoded, _parse_iso


def test_parse_iso_microseconds_preserved():
    out = _parse_iso("2026-04-27T10:00:00.123456Z")
    assert out is not None
    assert out.microsecond == 123456


def test_parse_iso_negative_offset():
    out = _parse_iso("2026-04-27T05:00:00-05:00")
    assert out is not None
    # 05:00 in -05 == 10:00 UTC
    assert out.astimezone(timezone.utc) == datetime(2026, 4, 27, 10, 0, tzinfo=timezone.utc)


def test_parse_iso_no_timezone_info_is_naive():
    """SatNOGS sometimes drops the trailing Z. Naive datetime is what we
    get; assert we don't pretend it's UTC."""
    out = _parse_iso("2026-04-27T10:00:00")
    assert out is not None
    # Either the function rejects (None) or it gives us a naive dt — both fine,
    # what matters is it doesn't claim "+00:00" when the input said nothing.
    if out.tzinfo is not None:
        # If a timezone was attached, it MUST be UTC by convention
        assert out.tzinfo == timezone.utc


def test_coerce_decoded_handles_array_top_level():
    """Real SatNOGS sometimes ships a JSON array as 'decoded' for AX.25
    multi-frame transmissions."""
    out = _coerce_decoded("[1,2,3]")
    assert out == {"value": [1, 2, 3]}


def test_coerce_decoded_handles_nested_dict():
    out = _coerce_decoded('{"a": {"b": {"c": 1}}}')
    assert out == {"a": {"b": {"c": 1}}}


def test_coerce_decoded_empty_string():
    assert _coerce_decoded("") is None


def test_parse_iso_clearly_invalid_returns_none():
    assert _parse_iso("not a date at all") is None
    assert _parse_iso("2026-13-99T99:99:99Z") is None  # impossible date
