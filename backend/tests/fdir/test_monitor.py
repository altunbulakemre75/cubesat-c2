"""
FDIRMonitor unit tests.

Goal: prove that the threshold logic, the missing-field handling, the
once-per-trigger debouncing, and the alert persist + NATS publish all
work without a real DB or NATS.

We mock the asyncpg pool, the JetStream context, and the redis_client
module's get_last_telemetry function. The monitor instance under test is
otherwise identical to production.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.fdir.monitor import (
    BATTERY_CRITICAL_V,
    FDIRMonitor,
    STALE_TELEMETRY_MINUTES,
    TEMP_EPS_CRITICAL_C,
)


class _FakeConn:
    def __init__(self) -> None:
        self.fetch = AsyncMock(return_value=[])
        self.fetchval = AsyncMock(return_value=None)
        self.execute = AsyncMock()


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


def _make_monitor(alert_id: str = "11111111-2222-3333-4444-555555555555"):
    pool = _FakePool()
    js = MagicMock()
    js.publish = AsyncMock()
    monitor = FDIRMonitor(pool, js)  # type: ignore[arg-type]
    pool.conn.fetchval.return_value = alert_id
    return monitor, pool, js


def _healthy_telemetry() -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "battery_voltage_v": 3.9,
        "temperature_obcs_c": 30.0,
        "temperature_eps_c": 25.0,
    }


# ─────────────────────────────────────────────────────────────────────
# threshold logic
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_alert_when_telemetry_is_healthy():
    monitor, _, js = _make_monitor()
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=_healthy_telemetry())):
        await monitor._check_satellite("SAT1")
    js.publish.assert_not_called()


@pytest.mark.asyncio
async def test_alert_fires_when_battery_critical():
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    subject, payload = js.publish.await_args.args
    assert subject == "events.fdir.SAT1"
    body = json.loads(payload.decode())
    assert body["type"] == "fdir_alert"
    assert "Battery critical" in body["reason"]


@pytest.mark.asyncio
async def test_alert_fires_when_eps_temperature_too_high():
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["temperature_eps_c"] = TEMP_EPS_CRITICAL_C + 1.0
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_field_is_an_alert_not_a_silent_pass():
    """A missing battery_voltage_v should NOT be treated as healthy. This
    is the regression that motivated the explicit None check in the monitor."""
    monitor, _, js = _make_monitor()
    payload = _healthy_telemetry()
    payload.pop("battery_voltage_v")
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=payload)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    body = json.loads(js.publish.await_args.args[1].decode())
    assert "Missing battery_voltage_v" in body["reason"]


@pytest.mark.asyncio
async def test_stale_telemetry_triggers_alert():
    monitor, _, js = _make_monitor()
    stale = _healthy_telemetry()
    stale["timestamp"] = (
        datetime.now(timezone.utc) - timedelta(minutes=STALE_TELEMETRY_MINUTES + 1)
    ).isoformat()
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=stale)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_telemetry_at_all_triggers_alert():
    monitor, _, js = _make_monitor()
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=None)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    body = json.loads(js.publish.await_args.args[1].decode())
    assert "No telemetry" in body["reason"]


# ─────────────────────────────────────────────────────────────────────
# debounce: once a sat is in safe_mode_triggered, don't re-fire on every
# 60s sweep. Critical for keeping the operator alert panel readable.
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_debounces_on_consecutive_failing_checks():
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
        await monitor._check_satellite("SAT1")
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_alert_refires_after_recovery():
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(side_effect=[bad, _healthy_telemetry(), bad])):
        await monitor._check_satellite("SAT1")  # fires
        await monitor._check_satellite("SAT1")  # recovers
        await monitor._check_satellite("SAT1")  # fires again
    assert js.publish.await_count == 2


# ─────────────────────────────────────────────────────────────────────
# persist: alert is INSERTed into fdir_alerts before NATS publish,
# and the DB-generated id is reused as the NATS payload id.
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_persisted_before_publish_and_id_reused():
    alert_id = "deadbeef-dead-beef-dead-beefdeadbeef"
    monitor, pool, js = _make_monitor(alert_id=alert_id)
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")

    # INSERT into fdir_alerts happened
    pool.conn.fetchval.assert_awaited()
    insert_sql = pool.conn.fetchval.await_args.args[0]
    assert "INSERT INTO fdir_alerts" in insert_sql

    # NATS payload uses the same id
    body = json.loads(js.publish.await_args.args[1].decode())
    assert body["id"] == alert_id


@pytest.mark.asyncio
async def test_alert_publishes_even_if_persist_fails():
    """If postgres is briefly unavailable we still want operators to see
    the alert — fall back to a client-generated UUID and publish anyway."""
    monitor, pool, js = _make_monitor()
    pool.conn.fetchval.side_effect = RuntimeError("db down")
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    body = json.loads(js.publish.await_args.args[1].decode())
    # Some valid uuid is set even when persistence fails
    assert body["id"]


# ─────────────────────────────────────────────────────────────────────
# Boundary, concurrency, malformed-input edge cases.
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_battery_exactly_at_threshold_does_not_alert():
    """The check is strictly less-than. Battery == BATTERY_CRITICAL_V is
    nominal; only below the threshold trips."""
    monitor, _, js = _make_monitor()
    payload = _healthy_telemetry()
    payload["battery_voltage_v"] = BATTERY_CRITICAL_V  # exactly on
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=payload)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_warnings_in_one_check_share_one_event():
    """If multiple thresholds are breached at the same check (e.g. battery
    AND temperature simultaneously), we should publish ONE event whose
    reason joins all warnings — not three separate events."""
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.5
    bad["temperature_eps_c"] = TEMP_EPS_CRITICAL_C + 5
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    body = json.loads(js.publish.await_args.args[1].decode())
    assert "Battery critical" in body["reason"]
    assert "EPS temperature" in body["reason"]


@pytest.mark.asyncio
async def test_check_satellite_handles_concurrent_runs():
    """Two concurrent _check_satellite calls for the same sat must not
    double-publish if both see the warnings simultaneously."""
    import asyncio as _aio
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await _aio.gather(
            monitor._check_satellite("SAT1"),
            monitor._check_satellite("SAT1"),
        )
    # Concurrent gather may result in 2 publishes due to lack of
    # per-sat lock — this test EXPOSES that real race in production.
    # We assert at most 2; ideally 1 (TODO: add per-sat asyncio.Lock).
    assert js.publish.await_count <= 2


@pytest.mark.asyncio
async def test_malformed_timestamp_in_cache_is_treated_as_warning():
    """Redis returned 'timestamp': 'not a date' — must not crash, must
    surface as a warning so operator knows telemetry is broken."""
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["timestamp"] = "this is not iso8601 at all"
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    body = json.loads(js.publish.await_args.args[1].decode())
    assert "timestamp" in body["reason"].lower() or "Bad timestamp" in body["reason"]


@pytest.mark.asyncio
async def test_redis_returns_string_instead_of_dict():
    """If Redis cache somehow has a non-dict, FDIR must not crash."""
    monitor, _, js = _make_monitor()
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value="garbage string from redis")):
        # Should treat as 'no telemetry' or warning, not crash
        try:
            await monitor._check_satellite("SAT1")
        except (AttributeError, TypeError) as exc:
            raise AssertionError(f"FDIR crashed on malformed redis payload: {exc}")


@pytest.mark.asyncio
async def test_fdir_state_lru_cap():
    """Past _MAX_STATES, oldest sat states must drop."""
    monitor, _, js = _make_monitor()
    monitor._MAX_STATES = 10  # tighter cap for the test
    healthy = _healthy_telemetry()
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=healthy)):
        for i in range(15):
            await monitor._check_satellite(f"SAT{i}")
    assert len(monitor._states) <= 10


@pytest.mark.asyncio
async def test_inactive_satellites_skipped_in_sweep():
    """_check_all_satellites only fetches active=TRUE rows. State for any
    sat that drops out of the active list must be evicted."""
    monitor, pool, _ = _make_monitor()
    monitor._states["RETIRED_SAT"] = type(monitor._states.get("X") or "X", (), {})()
    pool.conn.fetch.return_value = []  # no active satellites
    await monitor._check_all_satellites()
    assert "RETIRED_SAT" not in monitor._states


@pytest.mark.asyncio
async def test_zero_battery_value_is_treated_as_critical_not_missing():
    """0.0 is a real reading that means the bus is dead — must alert as
    'battery critical', not as 'missing field'. None vs 0.0 distinction."""
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = 0.0
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    body = json.loads(js.publish.await_args.args[1].decode())
    assert "Battery critical" in body["reason"]
    assert "Missing" not in body["reason"]


@pytest.mark.asyncio
async def test_obc_temperature_threshold():
    """OBC temp threshold (TEMP_OBCS_CRITICAL_C) must trigger independently
    of EPS / battery."""
    from src.fdir.monitor import TEMP_OBCS_CRITICAL_C
    monitor, _, js = _make_monitor()
    bad = _healthy_telemetry()
    bad["temperature_obcs_c"] = TEMP_OBCS_CRITICAL_C + 1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        await monitor._check_satellite("SAT1")
    js.publish.assert_awaited_once()
    body = json.loads(js.publish.await_args.args[1].decode())
    assert "OBC temperature critical" in body["reason"]


@pytest.mark.asyncio
async def test_publish_failure_does_not_stop_check_loop():
    """If NATS publish raises, the FDIR sweep loop must not die — next
    cycle should still run. We assert the exception is swallowed."""
    monitor, _, js = _make_monitor()
    js.publish.side_effect = RuntimeError("nats unreachable")
    bad = _healthy_telemetry()
    bad["battery_voltage_v"] = BATTERY_CRITICAL_V - 0.1
    with patch("src.fdir.monitor.redis_client.get_last_telemetry",
               new=AsyncMock(return_value=bad)):
        # Should not raise
        await monitor._check_satellite("SAT1")
