"""
TelemetryWriter failure-path tests.

Bunlar happy-path testleri DEĞİL: queue full, transient DB outage,
terminal DB error, malformed payloads, anomaly publish failure.
Mock-driven, çünkü gerçek asyncpg/NATS startup'ı 10sn'lik test'e değmez.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from src.ingestion.models import CanonicalTelemetry, TelemetryParams, SatelliteMode
from src.ingestion.writer import TelemetryWriter


def _make_telem(sat_id: str = "SAT1", seq: int = 1) -> CanonicalTelemetry:
    return CanonicalTelemetry(
        timestamp=datetime.now(timezone.utc),
        satellite_id=sat_id,
        source="ax25",
        sequence=seq,
        params=TelemetryParams(
            battery_voltage_v=3.9,
            temperature_obcs_c=25.0,
            temperature_eps_c=22.0,
            solar_power_w=2.5,
            rssi_dbm=-90.0,
            uptime_s=12345,
            mode=SatelliteMode.NOMINAL,
        ),
    )


def _make_msg() -> MagicMock:
    msg = MagicMock()
    msg.subject = "telemetry.canonical.SAT1"
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()
    return msg


class _Conn:
    def __init__(self) -> None:
        self.executemany = AsyncMock()
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock()
        self.fetchval = AsyncMock()

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


class _Pool:
    def __init__(self) -> None:
        self.conn = _Conn()

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


def _make_writer():
    js = MagicMock()
    js.publish = AsyncMock()
    pool = _Pool()
    w = TelemetryWriter(js, pool, detector=None, batch_size=10, flush_interval_s=0.01)  # type: ignore[arg-type]
    return w, pool, js


# ─────────────────────────────────────────────────────────────────────
# Empty / minimal cases
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flush_once_no_items_no_db_call():
    w, pool, _ = _make_writer()
    await w._flush_once()
    pool.conn.executemany.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# DB failure paths
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transient_db_error_naks_messages_for_redelivery():
    """asyncpg.PostgresConnectionError → JetStream redeliver. We must NAK
    every message, not silently ack."""
    w, pool, _ = _make_writer()
    msg = _make_msg()
    await w._queue.put((_make_telem(), msg))

    pool.conn.executemany.side_effect = asyncpg.PostgresConnectionError("db gone")
    await w._flush_once()

    msg.nak.assert_awaited()
    msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_db_error_acks_to_avoid_infinite_loop():
    """Schema/data error is NOT transient — NAKing would loop forever.
    We log loudly + ack."""
    w, pool, _ = _make_writer()
    msg = _make_msg()
    await w._queue.put((_make_telem(), msg))

    # Generic Exception simulating a constraint violation
    pool.conn.executemany.side_effect = ValueError("bad data shape")
    await w._flush_once()

    msg.ack.assert_awaited()


@pytest.mark.asyncio
async def test_terminal_error_increments_error_counter():
    w, pool, _ = _make_writer()
    msg = _make_msg()
    await w._queue.put((_make_telem(), msg))
    pool.conn.executemany.side_effect = ValueError("boom")
    await w._flush_once()
    assert w._errors == 1
    assert w._written == 0


# ─────────────────────────────────────────────────────────────────────
# Parse error path (callback)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_json_in_callback_terminates_message():
    """A garbage payload from NATS should be terminated (poison message),
    not NAKed (would redeliver forever) and not silently consumed."""
    w, _, _ = _make_writer()
    msg = MagicMock()
    msg.subject = "telemetry.canonical.X"
    msg.data = b"not json at all"
    msg.term = AsyncMock()
    msg.nak = AsyncMock()

    await w._enqueue(msg)
    msg.term.assert_awaited()
    msg.nak.assert_not_called()
    assert w._errors == 1


@pytest.mark.asyncio
async def test_valid_json_but_wrong_schema_terminates():
    """JSON parses fine but Pydantic validation fails. Treat as poison."""
    w, _, _ = _make_writer()
    msg = MagicMock()
    msg.subject = "telemetry.canonical.X"
    msg.data = json.dumps({"only_one_field": 1}).encode()
    msg.term = AsyncMock()

    await w._enqueue(msg)
    msg.term.assert_awaited()


# ─────────────────────────────────────────────────────────────────────
# Queue full
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_full_naks_for_backpressure():
    """When the in-memory queue is saturated, we'd rather NATS hold the
    message than drop it on the floor."""
    js = MagicMock()
    js.publish = AsyncMock()
    pool = _Pool()
    w = TelemetryWriter(js, pool, batch_size=1, flush_interval_s=10.0)  # type: ignore[arg-type]
    # batch_size=1 → maxsize = 10 (from constructor: batch_size * 10)
    for _ in range(10):
        await w._queue.put((_make_telem(), _make_msg()))

    msg = MagicMock()
    msg.subject = "telemetry.canonical.X"
    msg.data = json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "satellite_id": "X", "source": "ax25", "sequence": 1,
        "params": {
            "battery_voltage_v": 3.9, "temperature_obcs_c": 25,
            "temperature_eps_c": 22, "solar_power_w": 2.5,
            "rssi_dbm": -90, "uptime_s": 1, "mode": "nominal",
        },
    }).encode()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()

    # Replace queue with a full one so put_nowait raises in callback path.
    # Easier: monkeypatch put to raise QueueFull.
    import asyncio
    async def boom(_):
        raise asyncio.QueueFull
    w._queue.put = boom  # type: ignore[assignment]
    await w._enqueue(msg)
    msg.nak.assert_awaited()


# ─────────────────────────────────────────────────────────────────────
# Anomaly detector failure
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anomaly_detection_failure_does_not_block_ack():
    """If the detector throws, we still must ack the underlying telemetry.
    Telemetry writes succeeded; anomaly is a side-effect."""
    detector = MagicMock()
    detector.feed.side_effect = RuntimeError("detector exploded")

    js = MagicMock()
    js.publish = AsyncMock()
    pool = _Pool()
    w = TelemetryWriter(js, pool, detector=detector,  # type: ignore[arg-type]
                        batch_size=10, flush_interval_s=0.01)

    msg = _make_msg()
    await w._queue.put((_make_telem(), msg))
    await w._flush_once()
    msg.ack.assert_awaited()
    assert w._written == 1
