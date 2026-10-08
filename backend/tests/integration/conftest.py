"""
Integration test harness — runs the real FastAPI app against a real
TimescaleDB instead of mocks.

Set TEST_DATABASE_URL to a superuser DSN on any reachable Postgres +
TimescaleDB server, e.g.

    docker run -d --name cubesat-testdb -e POSTGRES_PASSWORD=test \
        -p 127.0.0.1:55432:5432 timescale/timescaledb:latest-pg16
    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres pytest

The harness creates a throwaway database, applies every migration once per
session and truncates all tables before each test. Without the variable
every test in this package is skipped, so the unit suite still runs on a
laptop with no Docker.

Why sync tests + asyncio.run helpers instead of async fixtures: Starlette's
TestClient runs the app on its own event loop in a worker thread. An
asyncpg pool belongs to the loop that created it, so the app's pool is
created lazily *inside* the app loop, and test-side seeding opens its own
short-lived connection on a fresh loop.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import hash_password
from src.storage.db import _init_connection
from src.storage.migrations import run_migrations

ADMIN_DSN = os.environ.get("TEST_DATABASE_URL")


def _dsn_for(dbname: str) -> str:
    assert ADMIN_DSN is not None
    return urlunparse(urlparse(ADMIN_DSN)._replace(path=f"/{dbname}"))


@pytest.fixture(scope="session")
def test_dsn() -> Iterator[str]:
    if not ADMIN_DSN:
        pytest.skip("TEST_DATABASE_URL not set — integration tests skipped")

    dbname = f"cubesat_test_{uuid.uuid4().hex[:8]}"
    dsn = _dsn_for(dbname)

    async def _setup() -> None:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            await admin.close()
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await run_migrations(pool)
        finally:
            await pool.close()

    async def _teardown() -> None:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        finally:
            await admin.close()

    asyncio.run(_setup())
    yield dsn
    asyncio.run(_teardown())


class Db:
    """Tiny sync facade over asyncpg for seeding and asserting from tests."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _run(self, method: str, sql: str, *args: Any) -> Any:
        conn = await asyncpg.connect(self._dsn)
        try:
            await _init_connection(conn)
            return await getattr(conn, method)(sql, *args)
        finally:
            await conn.close()

    def execute(self, sql: str, *args: Any) -> Any:
        return asyncio.run(self._run("execute", sql, *args))

    def fetchrow(self, sql: str, *args: Any) -> Any:
        return asyncio.run(self._run("fetchrow", sql, *args))

    def fetchval(self, sql: str, *args: Any) -> Any:
        return asyncio.run(self._run("fetchval", sql, *args))

    def fetch(self, sql: str, *args: Any) -> Any:
        return asyncio.run(self._run("fetch", sql, *args))

    def create_user(
        self, username: str, password: str, role: str = "viewer", *, active: bool = True,
    ) -> None:
        self.execute(
            """
            INSERT INTO users (username, email, password_hash, role, active)
            VALUES ($1, $2, $3, $4, $5)
            """,
            username, f"{username}@example.test", hash_password(password), role, active,
        )


@pytest.fixture
def db(test_dsn: str) -> Iterator[Db]:
    async def _truncate_all() -> None:
        conn = await asyncpg.connect(test_dsn)
        try:
            tables = [
                r["tablename"]
                for r in await conn.fetch(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> '_migrations'"
                )
            ]
            # replica role skips user triggers, so the append-only audit_log
            # guard doesn't block cleanup between tests.
            await conn.execute("SET session_replication_role = replica")
            await conn.execute(
                "TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"
            )
        finally:
            await conn.close()

    asyncio.run(_truncate_all())
    yield Db(test_dsn)


class _UnavailableRedis:
    """Every call fails at once, like a Redis that is down. Redis is only a
    cache in this system, so the app must behave correctly without it —
    and tests stay fast and deterministic instead of waiting on connect
    timeouts to a Redis that isn't running."""

    def __getattr__(self, name: str) -> Any:
        async def _fail(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("redis unavailable (integration tests)")
        return _fail


@pytest.fixture(autouse=True)
def _redis_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.storage import redis_client
    monkeypatch.setattr(redis_client, "get_client", lambda: _UnavailableRedis())


@asynccontextmanager
async def _noop_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


@pytest.fixture
def app(test_dsn: str, db: Db, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from src.api.deps import db_pool
    from src.api.main import create_app
    from src.config import settings

    # The limiter has its own unit tests; here it would only add a Redis
    # round-trip (and a connect timeout when Redis isn't running) per login.
    monkeypatch.setattr(settings, "login_rate_limit_enabled", False)

    application = create_app()
    application.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]

    state: dict[str, asyncpg.Pool] = {}

    async def _lazy_pool() -> asyncpg.Pool:
        if "pool" not in state:
            state["pool"] = await asyncpg.create_pool(
                test_dsn, init=_init_connection, min_size=1, max_size=4,
            )
        return state["pool"]

    application.dependency_overrides[db_pool] = _lazy_pool
    application.state.test_pool_state = state
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
        pool = app.state.test_pool_state.get("pool")
        if pool is not None:
            c.portal.call(pool.close)


def login(client: TestClient, username: str, password: str) -> dict[str, Any]:
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
