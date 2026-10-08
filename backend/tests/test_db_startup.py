"""The backend waits for a database that is still starting.

On a fresh volume the Postgres image runs a temporary server for its init
scripts and then restarts; connecting during that window fails with
CannotConnectNowError. The backend used to crash its startup there — and
with uvicorn --reload the container stayed up without ever serving.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from src.storage import db


@pytest.fixture(autouse=True)
def _fresh_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_CONNECT_RETRY_DELAY_S", 0.0)


async def test_retries_while_the_database_is_starting() -> None:
    pool = object()
    attempts: list[Any] = [
        asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
        ConnectionRefusedError(),
        pool,
    ]

    async def create_pool(**_kwargs: Any) -> Any:
        result = attempts.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    with patch("src.storage.db.asyncpg.create_pool", side_effect=create_pool):
        assert await db.get_pool() is pool


async def test_gives_up_after_the_retry_budget() -> None:
    failing = AsyncMock(side_effect=ConnectionRefusedError())
    with patch("src.storage.db.asyncpg.create_pool", new=failing), \
         pytest.raises(ConnectionRefusedError):
        await db.get_pool()
    assert failing.await_count == db._CONNECT_ATTEMPTS


async def test_wrong_password_is_not_retried() -> None:
    # A configuration error won't fix itself; fail fast with the real reason.
    failing = AsyncMock(side_effect=asyncpg.exceptions.InvalidPasswordError("bad password"))
    with patch("src.storage.db.asyncpg.create_pool", new=failing), \
         pytest.raises(asyncpg.exceptions.InvalidPasswordError):
        await db.get_pool()
    assert failing.await_count == 1
