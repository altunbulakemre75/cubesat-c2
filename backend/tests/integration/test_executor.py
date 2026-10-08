"""
Executor and timeout behaviour against a real database.

Replaces mock-scripted unit tests that asserted the exact sequence of SQL
calls; these assert the resulting command state instead, so they survive
refactoring of the queries.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import asyncpg
import pytest

from src.scheduler.service import CommandScheduler
from src.storage.db import _init_connection
from tests.integration.conftest import Db


class _JS:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []
        self._fail_with = fail_with

    async def publish(self, subject: str, payload: bytes) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.published.append((subject, json.loads(payload)))


@pytest.fixture(autouse=True)
def _satellite(db: Db) -> None:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1')")


def _due(db: Db, *, seconds_ago: int = 5, params: dict[str, Any] | None = None,
         command_type: str = "ping") -> str:
    cmd_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO commands (id, satellite_id, command_type, params, status,
                              scheduled_at, scheduled_manually)
        VALUES ($1, 'SAT1', $2, $3, 'scheduled', NOW() - make_interval(secs => $4), TRUE)
        """,
        cmd_id, command_type, params or {}, seconds_ago,
    )
    return cmd_id


def _status(db: Db, cmd_id: str) -> asyncpg.Record:
    return db.fetchrow("SELECT status, error_message FROM commands WHERE id = $1::uuid", cmd_id)


def _with_scheduler(dsn: str, js: _JS, *steps: str) -> CommandScheduler:
    holder: dict[str, CommandScheduler] = {}

    async def _main() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=4)
        try:
            sched = CommandScheduler(pool, js)  # type: ignore[arg-type]
            holder["s"] = sched
            for step in steps:
                await getattr(sched, step)()
        finally:
            await pool.close()
    asyncio.run(_main())
    return holder["s"]


def test_payload_carries_id_type_params_and_retry_count(db: Db, test_dsn: str) -> None:
    cmd = _due(db, params={"mode": "science"}, command_type="mode_change")
    js = _JS()

    _with_scheduler(test_dsn, js, "_execute_once")

    subject, payload = js.published[0]
    assert subject == "commands.SAT1"
    assert payload["command_id"] == cmd
    assert payload["command_type"] == "mode_change"
    assert payload["params"] == {"mode": "science"}
    assert payload["retry_count"] == 0


def test_due_commands_go_out_oldest_first(db: Db, test_dsn: str) -> None:
    newer = _due(db, seconds_ago=5)
    older = _due(db, seconds_ago=60)
    js = _JS()

    _with_scheduler(test_dsn, js, "_execute_once")

    assert [p["command_id"] for _, p in js.published] == [older, newer]


def test_publish_failure_returns_the_command_to_the_schedule(db: Db, test_dsn: str) -> None:
    cmd = _due(db)
    js = _JS(fail_with=RuntimeError("nats unreachable"))

    sched = _with_scheduler(test_dsn, js, "_execute_once")

    row = _status(db, cmd)
    assert row["status"] == "scheduled"
    assert "nats unreachable" in row["error_message"]
    assert sched.transmitted_count == 0


def test_two_executors_never_send_the_same_command_twice(db: Db, test_dsn: str) -> None:
    cmd = _due(db)
    js = _JS()

    async def _race() -> None:
        pool = await asyncpg.create_pool(test_dsn, init=_init_connection, min_size=2, max_size=6)
        try:
            a = CommandScheduler(pool, js)  # type: ignore[arg-type]
            b = CommandScheduler(pool, js)  # type: ignore[arg-type]
            await asyncio.gather(a._execute_once(), b._execute_once())
        finally:
            await pool.close()
    asyncio.run(_race())

    assert [p["command_id"] for _, p in js.published] == [cmd]
    assert _status(db, cmd)["status"] == "sent"


def test_timeout_of_an_already_acked_command_is_ignored(db: Db, test_dsn: str) -> None:
    cmd = _due(db)
    db.execute("UPDATE commands SET status = 'acked' WHERE id = $1::uuid", cmd)

    async def _main() -> None:
        pool = await asyncpg.create_pool(test_dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await CommandScheduler(pool, _JS())._mark_timed_out(cmd, "late")  # type: ignore[arg-type]
            await CommandScheduler(pool, _JS())._mark_timed_out(  # type: ignore[arg-type]
                str(uuid.uuid4()), "unknown id")
        finally:
            await pool.close()
    asyncio.run(_main())

    assert _status(db, cmd)["status"] == "acked"


# ── ACK timing ───────────────────────────────────────────────────────────────
# Found end-to-end: the simulator ACKs within milliseconds, i.e. before
# the executor has moved the command from 'transmitting' to 'sent'. The ACK
# was matched with "WHERE status = 'sent'", found nothing, and was dropped;
# the command then timed out although the satellite had executed it.

def _apply(dsn: str, method: str, cmd_id: str, *args: str) -> None:
    async def _main() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await getattr(CommandScheduler(pool, _JS()), method)(cmd_id, *args)  # type: ignore[arg-type]
        finally:
            await pool.close()
    asyncio.run(_main())


def test_ack_that_overtakes_the_sent_update_is_recorded(db: Db, test_dsn: str) -> None:
    cmd = _due(db)
    db.execute("UPDATE commands SET status = 'transmitting' WHERE id = $1::uuid", cmd)

    _apply(test_dsn, "_mark_acked", cmd)

    assert _status(db, cmd)["status"] == "acked"


def test_late_ack_after_a_retry_was_scheduled_is_recorded(db: Db, test_dsn: str) -> None:
    # The satellite did execute the first transmission; its ACK just came
    # after our timeout. Recording it prevents a pointless retransmission.
    cmd = _due(db)
    db.execute(
        "UPDATE commands SET status = 'scheduled', retry_count = 1, "
        "sent_at = NOW() - interval '70 seconds' "
        "WHERE id = $1::uuid", cmd,
    )

    _apply(test_dsn, "_mark_acked", cmd)

    assert _status(db, cmd)["status"] == "acked"


def test_ack_for_a_never_transmitted_command_is_ignored(db: Db, test_dsn: str) -> None:
    cmd = _due(db)   # scheduled, never sent

    _apply(test_dsn, "_mark_acked", cmd)

    assert _status(db, cmd)["status"] == "scheduled"


def test_nack_that_overtakes_the_sent_update_is_recorded(db: Db, test_dsn: str) -> None:
    cmd = _due(db)
    db.execute("UPDATE commands SET status = 'transmitting' WHERE id = $1::uuid", cmd)

    _apply(test_dsn, "_mark_rejected", cmd, "unsupported command type")

    assert _status(db, cmd)["status"] == "dead"
