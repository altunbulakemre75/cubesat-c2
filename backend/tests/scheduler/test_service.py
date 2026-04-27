"""
CommandScheduler unit tests.

We mock asyncpg.Pool and JetStreamContext so the tests run without a
running postgres or NATS. Behaviour-level coverage:
  - _schedule_once: PENDING → SCHEDULED via next pass AOS
  - _execute_once: SCHEDULED → TRANSMITTING → SENT, JS publish called
  - _execute_once: publish failure rolls back to SCHEDULED
  - _mark_acked: SENT → ACKED (idempotent if already ACKED)
  - _mark_timed_out: SENT → TIMEOUT → RETRY (safe_retry) → SCHEDULED
  - _mark_timed_out: SENT → TIMEOUT → DEAD (unsafe / max retries)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.scheduler.service import CommandScheduler


# ─────────────────────────────────────────────────────────────────────
# Pool stub: every conn.fetch / fetchrow / fetchval / execute call is
# scripted via a deque of return values per method.
# ─────────────────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self) -> None:
        self.fetch = AsyncMock()
        self.fetchrow = AsyncMock()
        self.fetchval = AsyncMock()
        self.execute = AsyncMock()


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def acquire(self):
        return self  # use the pool as its own ctx manager + conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


def _make_scheduler() -> tuple[CommandScheduler, _FakePool, MagicMock]:
    pool = _FakePool()
    js = MagicMock()
    js.publish = AsyncMock()
    sched = CommandScheduler(pool, js)  # type: ignore[arg-type]
    return sched, pool, js


# ─────────────────────────────────────────────────────────────────────
# _schedule_once
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_once_uses_operator_supplied_scheduled_at():
    sched, pool, _ = _make_scheduler()
    target = datetime.now(timezone.utc) + timedelta(minutes=5)
    pool.conn.fetch.return_value = [
        {"id": "c1", "satellite_id": "SAT1",
         "scheduled_at": target, "command_type": "ping", "priority": 5}
    ]
    pool.conn.execute.return_value = "UPDATE 1"

    await sched._schedule_once()

    # First call selects pending; second updates one row.
    assert pool.conn.execute.await_count == 1
    args = pool.conn.execute.await_args.args
    assert args[0].lstrip().startswith("UPDATE commands")
    assert args[1] == "c1"
    assert args[2] == target  # operator's scheduled_at preserved
    assert sched.scheduled_count == 1


@pytest.mark.asyncio
async def test_schedule_once_falls_back_to_next_pass_aos():
    sched, pool, _ = _make_scheduler()
    aos = datetime.now(timezone.utc) + timedelta(minutes=12)
    pool.conn.fetch.return_value = [
        {"id": "c2", "satellite_id": "SAT2",
         "scheduled_at": None, "command_type": "ping", "priority": 5}
    ]
    pool.conn.fetchval.return_value = aos
    pool.conn.execute.return_value = "UPDATE 1"

    await sched._schedule_once()

    pool.conn.fetchval.assert_awaited_once()
    args = pool.conn.execute.await_args.args
    assert args[2] == aos


@pytest.mark.asyncio
async def test_schedule_once_skips_if_no_pass_available():
    sched, pool, _ = _make_scheduler()
    pool.conn.fetch.return_value = [
        {"id": "c3", "satellite_id": "SAT3",
         "scheduled_at": None, "command_type": "ping", "priority": 5}
    ]
    pool.conn.fetchval.return_value = None  # no upcoming pass

    await sched._schedule_once()

    pool.conn.execute.assert_not_called()
    assert sched.scheduled_count == 0


# ─────────────────────────────────────────────────────────────────────
# _execute_once
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_once_publishes_and_marks_sent():
    sched, pool, js = _make_scheduler()
    pool.conn.fetch.return_value = [
        {"id": "c4", "satellite_id": "SAT4", "command_type": "reboot",
         "params": {"force": True}, "retry_count": 0}
    ]
    # Three execute() calls in order: claim TRANSMITTING, then mark SENT.
    pool.conn.execute.side_effect = ["UPDATE 1", "UPDATE 1"]

    await sched._execute_once()

    js.publish.assert_awaited_once()
    subject, payload = js.publish.await_args.args
    assert subject == "commands.SAT4"
    body = json.loads(payload.decode())
    assert body["command_id"] == "c4"
    assert body["satellite_id"] == "SAT4"
    assert body["command_type"] == "reboot"
    assert body["params"] == {"force": True}
    assert sched.transmitted_count == 1


@pytest.mark.asyncio
async def test_execute_once_rolls_back_on_publish_failure():
    sched, pool, js = _make_scheduler()
    pool.conn.fetch.return_value = [
        {"id": "c5", "satellite_id": "SAT5", "command_type": "ping",
         "params": None, "retry_count": 0}
    ]
    pool.conn.execute.side_effect = ["UPDATE 1", "UPDATE 1"]
    js.publish.side_effect = RuntimeError("nats unreachable")

    await sched._execute_once()

    # Two execute()s: claim TRANSMITTING + rollback to SCHEDULED.
    assert pool.conn.execute.await_count == 2
    rollback_args = pool.conn.execute.await_args.args
    assert "scheduled" in rollback_args[0].lower()
    assert rollback_args[1] == "c5"
    assert sched.transmitted_count == 0


@pytest.mark.asyncio
async def test_execute_once_skips_if_claim_lost_to_other_scheduler():
    """If two schedulers race for the same row, the one that loses the
    UPDATE must NOT publish — otherwise we double-send."""
    sched, pool, js = _make_scheduler()
    pool.conn.fetch.return_value = [
        {"id": "c6", "satellite_id": "SAT6", "command_type": "ping",
         "params": None, "retry_count": 0}
    ]
    pool.conn.execute.return_value = "UPDATE 0"  # someone else claimed it

    await sched._execute_once()

    js.publish.assert_not_called()
    assert sched.transmitted_count == 0


# ─────────────────────────────────────────────────────────────────────
# _mark_acked
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_acked_increments_only_when_row_actually_changed():
    sched, pool, _ = _make_scheduler()
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._mark_acked("c7")
    assert sched.acked_count == 1

    # Idempotent: a second ACK for an already-ACKED row produces UPDATE 0.
    pool.conn.execute.return_value = "UPDATE 0"
    await sched._mark_acked("c7")
    assert sched.acked_count == 1


# ─────────────────────────────────────────────────────────────────────
# _mark_timed_out
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_with_safe_retry_under_limit_goes_to_scheduled():
    sched, pool, _ = _make_scheduler()
    pool.conn.fetchrow.return_value = {
        "status": "sent", "command_type": "ping",
        "safe_retry": True, "retry_count": 0,
    }
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._mark_timed_out("c8", "no ack")
    assert sched.timed_out_count == 1
    assert sched.dead_count == 0


@pytest.mark.asyncio
async def test_timeout_with_unsafe_command_type_goes_to_dead():
    sched, pool, _ = _make_scheduler()
    pool.conn.fetchrow.return_value = {
        "status": "sent", "command_type": "engine_fire",  # in UNSAFE_RETRY_TYPES
        "safe_retry": True, "retry_count": 0,
    }
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._mark_timed_out("c9", "no ack")
    assert sched.timed_out_count == 1
    assert sched.dead_count == 1


@pytest.mark.asyncio
async def test_timeout_when_max_retries_exhausted_goes_to_dead():
    sched, pool, _ = _make_scheduler()
    pool.conn.fetchrow.return_value = {
        "status": "sent", "command_type": "ping",
        "safe_retry": True, "retry_count": 99,  # past MAX_RETRIES
    }
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._mark_timed_out("c10", "no ack")
    assert sched.dead_count == 1


@pytest.mark.asyncio
async def test_timeout_skips_if_row_not_in_sent_state():
    """Race: command was already ACKED between the timeout sweep query
    and our follow-up fetchrow. Must do nothing."""
    sched, pool, _ = _make_scheduler()
    pool.conn.fetchrow.return_value = {
        "status": "acked", "command_type": "ping",
        "safe_retry": True, "retry_count": 0,
    }
    await sched._mark_timed_out("c11", "no ack")
    assert sched.timed_out_count == 0
    pool.conn.execute.assert_not_called()
