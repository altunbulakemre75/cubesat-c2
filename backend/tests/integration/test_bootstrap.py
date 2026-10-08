"""
First-run admin bootstrap.

The one-time admin password goes to a 600 file, never to the logs — logs
are shipped to Loki and indexed. v0.1.0 fell back to logging it in clear
text when the file couldn't be written (flagged by CodeQL), and never
removed the file despite saying it would.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api import bootstrap
from src.storage.db import _init_connection
from tests.integration.conftest import Db, bearer

KNOWN_PASSWORD = "Known-Bootstrap-Password-1234"


def _run_bootstrap(dsn: str) -> None:
    async def _run() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await bootstrap.ensure_admin_user(pool)
        finally:
            await pool.close()
    asyncio.run(_run())


@pytest.fixture
def known_password(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(bootstrap.secrets, "token_urlsafe", lambda _n: KNOWN_PASSWORD)
    return KNOWN_PASSWORD


def test_bootstrap_writes_the_password_to_a_file(
    db: Db, test_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, known_password: str,
) -> None:
    target = tmp_path / "bootstrap"
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_FILE", target)

    _run_bootstrap(test_dsn)

    assert known_password in target.read_text(encoding="utf-8")
    row = db.fetchrow("SELECT role, must_change_password FROM users WHERE username = 'admin'")
    assert row["role"] == "admin" and row["must_change_password"] is True


def test_unwritable_file_aborts_without_logging_the_password(
    db: Db, test_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, known_password: str,
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_FILE", blocker / "bootstrap")

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError, match="ADMIN_BOOTSTRAP_FILE"):
        _run_bootstrap(test_dsn)

    assert known_password not in caplog.text
    # No admin with an unknowable password: the next start can retry.
    assert db.fetchval("SELECT COUNT(*) FROM users") == 0


def test_taken_admin_username_leaves_no_misleading_password_file(
    db: Db, test_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, known_password: str,
) -> None:
    # A user called "admin" exists but is no longer an active admin. The
    # INSERT does nothing, so a written password would never work.
    db.create_user("admin", "some-other-password", "viewer")
    target = tmp_path / "bootstrap"
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_FILE", target)

    _run_bootstrap(test_dsn)

    assert not target.exists()


def test_password_change_by_the_bootstrapped_admin_removes_the_file(
    client: TestClient, db: Db, test_dsn: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, known_password: str,
) -> None:
    target = tmp_path / "bootstrap"
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_FILE", target)
    _run_bootstrap(test_dsn)

    tokens = client.post(
        "/auth/login", json={"username": "admin", "password": known_password},
    ).json()
    resp = client.post(
        "/auth/change-password",
        json={"old_password": known_password, "new_password": "a-real-admin-password"},
        headers=bearer(tokens["access_token"]),
    )
    assert resp.status_code == 200
    assert not target.exists()
