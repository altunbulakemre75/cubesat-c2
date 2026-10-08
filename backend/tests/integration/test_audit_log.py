"""
The audit log is append-only at the database level, not just by
convention in application code (v0.1.0 claimed append-only in docs and
SECURITY.md but nothing stopped an UPDATE/DELETE).
"""

from __future__ import annotations

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import Db, bearer, login

PW = "audit-tests-password"


@pytest.fixture
def entry(db: Db) -> int:
    return int(db.fetchval(
        "INSERT INTO audit_log (username, action) VALUES ('alice', 'test.event') RETURNING id"
    ))


def test_audit_rows_cannot_be_modified(db: Db, entry: int) -> None:
    with pytest.raises(asyncpg.PostgresError, match="append-only"):
        db.execute("UPDATE audit_log SET username = 'mallory' WHERE id = $1", entry)


def test_audit_rows_cannot_be_deleted(db: Db, entry: int) -> None:
    with pytest.raises(asyncpg.PostgresError, match="append-only"):
        db.execute("DELETE FROM audit_log WHERE id = $1", entry)


def test_audit_log_cannot_be_truncated(db: Db, entry: int) -> None:
    with pytest.raises(asyncpg.PostgresError, match="append-only"):
        db.execute("TRUNCATE audit_log")


def test_appending_still_works(db: Db, entry: int) -> None:
    db.execute("INSERT INTO audit_log (username, action) VALUES ('bob', 'test.event')")
    assert db.fetchval("SELECT COUNT(*) FROM audit_log") == 2


def test_ground_station_changes_are_audited(client: TestClient, db: Db) -> None:
    db.create_user("root", PW, "admin")
    admin = bearer(login(client, "root", PW)["access_token"])

    created = client.post(
        "/stations", json={"name": "Ankara", "latitude_deg": 39.9, "longitude_deg": 32.8},
        headers=admin,
    )
    assert created.status_code == 201
    station_id = created.json()["id"]
    assert client.delete(f"/stations/{station_id}", headers=admin).status_code == 204

    actions = [r["action"] for r in db.fetch(
        "SELECT action FROM audit_log WHERE target_type = 'ground_station' ORDER BY id"
    )]
    assert actions == ["station.create", "station.delete"]
