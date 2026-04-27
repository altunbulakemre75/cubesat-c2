"""
SatnogsTelemetryFetcher unit tests.

Behavioural coverage:
  - _coerce_decoded handles JSON-string, dict, plain-text, and None inputs.
  - _poll_one builds an INSERT batch from raw SatNOGS frames, dropping
    frames without a timestamp.
  - _poll_all_satellites does nothing if no satellite has a NORAD ID.

We mock asyncpg.Pool and SatNOGSClient — the fetcher never talks to the
network in the test process.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from datetime import datetime, timezone

from src.ingestion.satnogs_fetcher import SatnogsTelemetryFetcher, _coerce_decoded, _parse_iso


class _FakeConn:
    def __init__(self) -> None:
        self.fetch = AsyncMock(return_value=[])
        self.fetchval = AsyncMock(side_effect=[0, 0])
        self.executemany = AsyncMock()


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


# ─────────────────────────────────────────────────────────────────────
# _coerce_decoded
# ─────────────────────────────────────────────────────────────────────

def test_coerce_decoded_returns_none_for_empty():
    assert _coerce_decoded(None) is None
    assert _coerce_decoded("") is None


def test_coerce_decoded_passes_dict_through():
    assert _coerce_decoded({"battery_v": 3.9}) == {"battery_v": 3.9}


def test_coerce_decoded_parses_json_string_to_dict():
    out = _coerce_decoded('{"battery_v": 3.9, "temp_c": 25}')
    assert out == {"battery_v": 3.9, "temp_c": 25}


def test_coerce_decoded_wraps_invalid_json_under_raw():
    out = _coerce_decoded("battery low!")
    assert out == {"raw": "battery low!"}


def test_coerce_decoded_wraps_json_non_dict_under_value():
    """SatNOGS sometimes stringifies a list or a scalar — preserve it."""
    out = _coerce_decoded("[1, 2, 3]")
    assert out == {"value": [1, 2, 3]}


# ─────────────────────────────────────────────────────────────────────
# _parse_iso — asyncpg requires real datetime objects, not strings
# ─────────────────────────────────────────────────────────────────────

def test_parse_iso_handles_z_suffix():
    """SatNOGS uses '...Z' which Python <3.11 fromisoformat doesn't accept.
    The fix replaces Z with +00:00 before parsing."""
    out = _parse_iso("2026-04-27T10:00:00Z")
    assert out == datetime(2026, 4, 27, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_handles_explicit_offset():
    out = _parse_iso("2026-04-27T10:00:00+00:00")
    assert out is not None
    assert out.tzinfo is not None


def test_parse_iso_returns_none_for_garbage():
    assert _parse_iso("not a date") is None
    assert _parse_iso("") is None
    assert _parse_iso(None) is None


# ─────────────────────────────────────────────────────────────────────
# _poll_all_satellites — no satellites with norad_id → no client created
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_skips_when_no_satellites_have_norad_id():
    pool = _FakePool()
    pool.conn.fetch.return_value = []
    fetcher = SatnogsTelemetryFetcher(pool)  # type: ignore[arg-type]

    with patch("src.ingestion.satnogs_fetcher.SatNOGSClient") as cls:
        await fetcher._poll_all_satellites()
        cls.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# _poll_one — happy path
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_one_inserts_observations_and_skips_timestampless():
    pool = _FakePool()
    fetcher = SatnogsTelemetryFetcher(pool)  # type: ignore[arg-type]

    client = MagicMock()
    client.get_recent_observations = AsyncMock(return_value=[
        {
            "id": 9001,
            "start": "2026-04-27T10:00:00Z",
            "end": "2026-04-27T10:10:00Z",
            "ground_station": 425,
            "transmitter": "tx1",
            "vetted_status": "good",
            "demoddata": [],
        },
        {
            # No timestamp — must be skipped, not crash.
            "id": 9002,
            "ground_station": 426,
        },
        {
            "id": 9003,
            "start": "2026-04-27T10:05:00Z",
            "ground_station": 427,
            "vetted_status": "unknown",
        },
    ])
    pool.conn.fetchval.side_effect = [10, 12]

    await fetcher._poll_one(client, "AO91", 43017)

    pool.conn.executemany.assert_awaited_once()
    rows = pool.conn.executemany.await_args.args[1]
    assert len(rows) == 2  # timestampless dropped
    # timestamp column is now a real datetime, not a string — asyncpg requires it
    assert isinstance(rows[0][4], datetime)
    assert rows[0][4].tzinfo is not None
    # observer column derived from ground_station id
    assert rows[0][2] == "GS-425"
    assert rows[1][2] == "GS-427"
    # decoded_json carries the full metadata under known keys
    meta_0 = json.loads(rows[0][6])
    assert meta_0["observation_id"] == 9001
    assert meta_0["ground_station"] == 425
    assert meta_0["vetted_status"] == "good"
    # frame_hex is None for network/observations source
    assert rows[0][5] is None
    # app_source labels where the row came from
    assert rows[0][7] == "network"
    assert fetcher.persisted_total == 2


@pytest.mark.asyncio
async def test_poll_one_no_op_when_satnogs_returns_no_observations():
    pool = _FakePool()
    fetcher = SatnogsTelemetryFetcher(pool)  # type: ignore[arg-type]

    client = MagicMock()
    client.get_recent_observations = AsyncMock(return_value=[])

    await fetcher._poll_one(client, "AO91", 43017)

    pool.conn.executemany.assert_not_called()
    assert fetcher.persisted_total == 0
