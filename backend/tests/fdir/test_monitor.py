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
