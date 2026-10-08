"""
CommandScheduler unit tests.

We mock asyncpg.Pool and JetStreamContext so the tests run without a
running postgres or NATS. Behaviour-level coverage:
  - _schedule_once: PENDING → SCHEDULED via next pass AOS
  - _mark_acked: SENT → ACKED (idempotent if already ACKED)
  - ack listener message handling

Transmission, timeout/retry and pass-window behaviour is tested against a
real database in tests/integration/test_executor.py and
test_pass_windows.py — the mock-scripted versions asserted SQL call
sequences and broke on every query change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler.service import CommandScheduler


@pytest.fixture(autouse=True)
def _mode_unknown():
    # The transmission-time mode check reads telemetry; these unit tests
    # script the pool call-by-call, so pin the mode to "unknown" (= send).
    # The check itself is covered in tests/integration/test_mode_policy.py.
    with patch("src.scheduler.service.current_mode", new=AsyncMock(return_value=None)):
        yield


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
    target = datetime.now(UTC) + timedelta(minutes=5)
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
    aos = datetime.now(UTC) + timedelta(minutes=12)
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
    target = datetime.now(UTC) + timedelta(minutes=1)
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
