"""
Command ACK path against real NATS + TimescaleDB.

Needs both TEST_DATABASE_URL and TEST_NATS_URL (see test_nats_acl.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

import asyncpg
import nats
import pytest
from nats.js.api import StreamConfig

from src.ingestion.service import ensure_stream
from src.scheduler.service import CommandScheduler
from src.storage.db import _init_connection
from tests.integration.conftest import Db

NATS_URL = os.environ.get("TEST_NATS_URL")
BACKEND_PW = os.environ.get("TEST_NATS_BACKEND_PASSWORD", "")
GS_PW = os.environ.get("TEST_NATS_GROUNDSTATION_PASSWORD", "")

pytestmark = pytest.mark.skipif(not NATS_URL, reason="TEST_NATS_URL not set")
T = TypeVar("T")


def _run(dsn: str, body: Callable[[asyncpg.Pool, nats.NATS], Awaitable[T]]) -> T:
    async def _main() -> T:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=4)
        nc = await nats.connect(NATS_URL, user="backend", password=BACKEND_PW)
        try:
            # Each test gets a clean stream so consumers from earlier tests
            # (or earlier runs) don't leak in.
            js = nc.jetstream()
            try:
                await js.delete_stream("cubesat")
            except Exception:  # noqa: BLE001 — absent on first run
                pass
            await ensure_stream(js)
            return await body(pool, nc)
        finally:
            await nc.close()
            await pool.close()
    return asyncio.run(_main())


async def _start_listener(pool: asyncpg.Pool, nc: nats.NATS) -> asyncio.Task[None]:
    scheduler = CommandScheduler(pool, nc.jetstream())
    task = asyncio.create_task(scheduler._ack_listener())
    await asyncio.sleep(0.5)  # let it subscribe
    return task


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _sent_command(db: Db) -> str:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1') ON CONFLICT DO NOTHING")
    cmd_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO commands (id, satellite_id, command_type, status, sent_at) "
        "VALUES ($1, 'SAT1', 'ping', 'sent', NOW())",
        cmd_id,
    )
    return cmd_id


async def _ack_as_groundstation(cmd_id: str, ok: bool = True) -> None:
    gs = await nats.connect(NATS_URL, user="groundstation", password=GS_PW,
                            inbox_prefix="_INBOX_gs")
    try:
        await gs.jetstream().publish(
            "commands.ack.SAT1", json.dumps({"command_id": cmd_id, "ok": ok}).encode(),
        )
    finally:
        await gs.close()


def test_ground_station_ack_marks_the_command_acked(db: Db, test_dsn: str) -> None:
    cmd_id = _sent_command(db)

    async def body(pool: asyncpg.Pool, nc: nats.NATS) -> None:
        task = await _start_listener(pool, nc)
        await _ack_as_groundstation(cmd_id)
        for _ in range(50):
            if await pool.fetchval("SELECT status FROM commands WHERE id = $1", cmd_id) == "acked":
                break
            await asyncio.sleep(0.1)
        await _stop(task)

    _run(test_dsn, body)
    assert db.fetchval("SELECT status FROM commands WHERE id = $1::uuid", cmd_id) == "acked"


def test_restarts_reuse_one_durable_ack_consumer(db: Db, test_dsn: str) -> None:
    # v0.1.0 created "scheduler-ack-<random>" on every start: consumers
    # piled up in JetStream and each new one replayed the whole ACK history.
    async def body(pool: asyncpg.Pool, nc: nats.NATS) -> list[str]:
        for _ in range(3):
            await _stop(await _start_listener(pool, nc))
        names = [c.name async for c in _consumers(nc)]
        return names

    names = _run(test_dsn, body)
    ack_consumers = [n for n in names if n.startswith("scheduler-ack")]
    assert ack_consumers == ["scheduler-ack"]


def test_leftover_random_ack_consumers_are_cleaned_up(db: Db, test_dsn: str) -> None:
    async def body(pool: asyncpg.Pool, nc: nats.NATS) -> list[str]:
        js = nc.jetstream()
        await js.add_consumer("cubesat", durable_name="scheduler-ack-1a2b3c4d",
                              filter_subject="commands.ack.>")
        await _stop(await _start_listener(pool, nc))
        return [c.name async for c in _consumers(nc)]

    names = _run(test_dsn, body)
    assert "scheduler-ack-1a2b3c4d" not in names


async def _consumers(nc: nats.NATS):  # type: ignore[no-untyped-def]
    for info in await nc.jetstream().consumers_info("cubesat"):
        yield info


_ = StreamConfig  # imported for type context in failure messages
