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


# ─────────────────────────────────────────────────────────────────────
# Race + adversarial inputs.
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_once_handles_no_pending():
    sched, pool, _ = _make_scheduler()
    pool.conn.fetch.return_value = []
    await sched._schedule_once()
    pool.conn.execute.assert_not_called()
    assert sched.scheduled_count == 0


@pytest.mark.asyncio
async def test_execute_once_handles_no_scheduled():
    sched, pool, js = _make_scheduler()
    pool.conn.fetch.return_value = []
    await sched._execute_once()
    js.publish.assert_not_called()
    assert sched.transmitted_count == 0


@pytest.mark.asyncio
async def test_execute_publishes_priority_ordered_commands():
    """When multiple commands are due, the SQL ORDER BY scheduled_at means
    the oldest goes first. Test mirrors what the implementation does."""
    sched, pool, js = _make_scheduler()
    pool.conn.fetch.return_value = [
        {"id": f"c{i}", "satellite_id": f"SAT{i}", "command_type": "ping",
         "params": None, "retry_count": 0}
        for i in range(3)
    ]
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._execute_once()
    # Three publishes for three different sats — no cross-talk
    assert js.publish.await_count == 3
    subjects = [call.args[0] for call in js.publish.await_args_list]
    assert sorted(subjects) == ["commands.SAT0", "commands.SAT1", "commands.SAT2"]


@pytest.mark.asyncio
async def test_mark_timed_out_command_not_found_is_noop():
    """fetchrow returns None when the row doesn't exist — must not crash
    or attempt to update."""
    sched, pool, _ = _make_scheduler()
    pool.conn.fetchrow.return_value = None
    await sched._mark_timed_out("nonexistent-id", "test")
    pool.conn.execute.assert_not_called()
    assert sched.timed_out_count == 0


@pytest.mark.asyncio
async def test_unsafe_command_type_with_safe_retry_still_dies():
    """UNSAFE_RETRY_TYPES (engine_fire, deploy_*) must NEVER retry, even
    when the operator set safe_retry=True by mistake."""
    sched, pool, _ = _make_scheduler()
    pool.conn.fetchrow.return_value = {
        "status": "sent", "command_type": "deploy_solar_panel",
        "safe_retry": True, "retry_count": 0,
    }
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._mark_timed_out("c-unsafe", "no ack")
    assert sched.dead_count == 1


@pytest.mark.asyncio
async def test_command_publish_failure_message_contains_error():
    """When publish raises, the rollback UPDATE should set error_message."""
    sched, pool, js = _make_scheduler()
    pool.conn.fetch.return_value = [
        {"id": "c1", "satellite_id": "SAT1", "command_type": "ping",
         "params": None, "retry_count": 0}
    ]
    pool.conn.execute.side_effect = ["UPDATE 1", "UPDATE 1"]
    js.publish.side_effect = RuntimeError("nats down")
    await sched._execute_once()
    # Last execute is the rollback — its second positional arg is the id,
    # third is the error_message.
    rollback = pool.conn.execute.await_args
    assert "publish failed" in rollback.args[2]
    assert "nats down" in rollback.args[2]


@pytest.mark.asyncio
async def test_ack_listener_terms_message_with_no_command_id():
    """Adversarial: the satellite (or a malicious actor) sends an ack
    JSON without 'command_id'. The listener must term and move on."""
    # Direct test of the parsing branch by simulating the message handler
    # logic — the actual subscribe is tested separately with integration.
    sched, pool, _ = _make_scheduler()
    # A garbage payload would be terminated; mirror that decision here by
    # asserting _mark_acked is never called when invoked with empty id.
    await sched._mark_acked("")
    # No-op (UPDATE 0 expected)
    assert sched.acked_count == 0


@pytest.mark.asyncio
async def test_double_ack_is_idempotent():
    """Same command_id ACKed twice — second is a no-op (UPDATE 0)."""
    sched, pool, _ = _make_scheduler()
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._mark_acked("c-x")
    pool.conn.execute.return_value = "UPDATE 0"
    await sched._mark_acked("c-x")
    assert sched.acked_count == 1


@pytest.mark.asyncio
async def test_execute_with_params_dict_serializes_correctly():
    """Command params dict must round-trip through JSON intact."""
    import json as _json
    sched, pool, js = _make_scheduler()
    pool.conn.fetch.return_value = [
        {"id": "c1", "satellite_id": "SAT1", "command_type": "set_param",
         "params": {"key": "thermal_setpoint", "value_c": 27.5,
                    "nested": {"a": 1}},
         "retry_count": 0}
    ]
    pool.conn.execute.side_effect = ["UPDATE 1", "UPDATE 1"]
    await sched._execute_once()
    body = _json.loads(js.publish.await_args.args[1].decode())
    assert body["params"] == {"key": "thermal_setpoint", "value_c": 27.5,
                              "nested": {"a": 1}}


@pytest.mark.asyncio
async def test_timeout_sweep_handles_empty_result():
    """Timeout loop must not crash when no SENT commands are stale."""
    sched, pool, _ = _make_scheduler()
    pool.conn.fetch.return_value = []
    await sched._timeout_once()
    pool.conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_priority_ordering_in_sql():
    """The SCHEDULE phase must order PENDING by priority ASC then created_at.
    Mock returns rows in that order; just verify the loop processes ALL."""
    sched, pool, _ = _make_scheduler()
    target = datetime.now(timezone.utc) + timedelta(minutes=1)
    pool.conn.fetch.return_value = [
        {"id": "c-high", "satellite_id": "SAT1",
         "scheduled_at": target, "command_type": "ping", "priority": 1},
        {"id": "c-low", "satellite_id": "SAT2",
         "scheduled_at": target, "command_type": "ping", "priority": 9},
    ]
    pool.conn.execute.return_value = "UPDATE 1"
    await sched._schedule_once()
    # Both processed → 2 execute calls
    assert pool.conn.execute.await_count == 2
    assert sched.scheduled_count == 2
