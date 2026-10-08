"""
Background services run in exactly one process; migrations never race.

Production runs `uvicorn --workers 2` (and k8s scales the backend with
an HPA). Every worker started its own scheduler, FDIR monitor, ingestion,
writer and TLE refresher: duplicate FDIR alerts, two schedulers racing
for the same commands, two subscribers on one durable consumer. Every
worker also ran the migrations at the same time.
"""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlparse, urlunparse

import asyncpg

from src.storage.leader import LeaderElector
from src.storage.migrations import run_migrations
from tests.integration.conftest import ADMIN_DSN


class _Services:
    def __init__(self) -> None:
        self.running = False
        self.starts = 0

    async def start(self) -> None:
        self.running = True
        self.starts += 1

    async def stop(self) -> None:
        self.running = False


async def _wait_for(predicate, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


def test_exactly_one_candidate_runs_the_services(test_dsn: str) -> None:
    async def scenario() -> tuple[int, int]:
        a, b = _Services(), _Services()
        ea = LeaderElector(test_dsn, a.start, a.stop, poll_s=0.1)
        eb = LeaderElector(test_dsn, b.start, b.stop, poll_s=0.1)
        tasks = [asyncio.create_task(ea.run()), asyncio.create_task(eb.run())]
        await _wait_for(lambda: a.running or b.running)
        await asyncio.sleep(0.5)  # give the follower every chance to (wrongly) start
        result = (int(a.running), int(b.running))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return result

    assert sum(asyncio.run(scenario())) == 1


def test_leadership_fails_over_when_the_leader_stops(test_dsn: str) -> None:
    async def scenario() -> bool:
        a, b = _Services(), _Services()
        ta = asyncio.create_task(LeaderElector(test_dsn, a.start, a.stop, poll_s=0.1).run())
        await _wait_for(lambda: a.running)
        tb = asyncio.create_task(LeaderElector(test_dsn, b.start, b.stop, poll_s=0.1).run())
        await asyncio.sleep(0.3)
        assert not b.running

        ta.cancel()                      # leader process goes away
        await asyncio.gather(ta, return_exceptions=True)
        assert not a.running             # its services were stopped
        await _wait_for(lambda: b.running)

        tb.cancel()
        await asyncio.gather(tb, return_exceptions=True)
        return True

    assert asyncio.run(scenario())


def test_concurrent_workers_apply_each_migration_once() -> None:
    assert ADMIN_DSN is not None
    dbname = f"cubesat_mig_{uuid.uuid4().hex[:8]}"
    dsn = urlunparse(urlparse(ADMIN_DSN)._replace(path=f"/{dbname}"))

    async def scenario() -> list[str]:
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'CREATE DATABASE "{dbname}"')
        try:
            pools = [await asyncpg.create_pool(dsn, min_size=1, max_size=2) for _ in range(3)]
            try:
                await asyncio.gather(*(run_migrations(p) for p in pools))
                rows = await pools[0].fetch("SELECT filename FROM _migrations ORDER BY filename")
                return [r["filename"] for r in rows]
            finally:
                for p in pools:
                    await p.close()
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            await admin.close()

    applied = asyncio.run(scenario())
    assert applied == sorted(set(applied))
    assert "001_initial_schema.sql" in applied
