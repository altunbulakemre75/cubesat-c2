"""
Mode-based command policy, checked when a command is queued and again
when it is about to be transmitted.

- unknown / stale mode (no telemetry for 2 h, per docs/MIMARI.md) needs
  the operator's explicit confirmation instead of silently skipping the
  policy as v0.1.0 did
- a command queued for the next pass is dropped if, by then, the
  satellite is in a mode that forbids it
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.scheduler.service import CommandScheduler
from src.storage.db import _init_connection
from tests.integration.conftest import Db, bearer, login

PW = "mode-policy-password"


@pytest.fixture
def operator(client: TestClient, db: Db) -> dict[str, str]:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1')")
    db.create_user("op", PW, "operator")
    return bearer(login(client, "op", PW)["access_token"])


def _telemetry(db: Db, mode: str, age: timedelta) -> None:
    db.execute(
        "INSERT INTO telemetry (time, satellite_id, source, sequence, mode) "
        "VALUES (NOW() - $1::interval, 'SAT1', 'ax25', 1, $2)",
        age, mode,
    )


def _create(client: TestClient, headers: dict[str, str], command_type: str, **extra: Any):  # type: ignore[no-untyped-def]
    return client.post("/commands", json={
        "satellite_id": "SAT1", "command_type": command_type, **extra,
    }, headers=headers)


def test_unknown_mode_needs_explicit_confirmation(
    client: TestClient, operator: dict[str, str],
) -> None:
    refused = _create(client, operator, "camera_on")
    assert refused.status_code == 409
    assert "confirm_unverified_mode" in refused.json()["detail"]

    confirmed = _create(client, operator, "camera_on", confirm_unverified_mode=True)
    assert confirmed.status_code == 201


def test_stale_mode_needs_explicit_confirmation(
    client: TestClient, db: Db, operator: dict[str, str],
) -> None:
    _telemetry(db, "nominal", timedelta(hours=3))
    assert _create(client, operator, "camera_on").status_code == 409


def test_fresh_mode_from_telemetry_drives_the_policy(
    client: TestClient, db: Db, operator: dict[str, str],
) -> None:
    _telemetry(db, "safe", timedelta(minutes=1))
    assert _create(client, operator, "camera_on").status_code == 422
    assert _create(client, operator, "recovery").status_code == 201


def test_confirmation_does_not_override_a_fresh_denial(
    client: TestClient, db: Db, operator: dict[str, str],
) -> None:
    _telemetry(db, "safe", timedelta(minutes=1))
    resp = _create(client, operator, "camera_on", confirm_unverified_mode=True)
    assert resp.status_code == 422


class _CapturingJetStream:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, json.loads(payload)))


def _due_command(db: Db, command_type: str) -> str:
    cmd_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO commands (id, satellite_id, command_type, status, scheduled_at, scheduled_manually) "
        "VALUES ($1, 'SAT1', $2, 'scheduled', NOW() - interval '1 second', TRUE)",
        cmd_id, command_type,
    )
    return cmd_id


def _execute_once(dsn: str) -> _CapturingJetStream:
    js = _CapturingJetStream()

    async def _run() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await CommandScheduler(pool, js)._execute_once()  # type: ignore[arg-type]
        finally:
            await pool.close()
    asyncio.run(_run())
    return js


def test_command_forbidden_by_the_mode_at_transmission_is_dropped(
    db: Db, test_dsn: str, operator: dict[str, str],
) -> None:
    cmd = _due_command(db, "camera_on")
    _telemetry(db, "safe", timedelta(seconds=30))   # satellite fell into SAFE meanwhile

    js = _execute_once(test_dsn)

    row = db.fetchrow("SELECT status, error_message FROM commands WHERE id = $1::uuid", cmd)
    assert row["status"] == "dead"
    assert "safe" in row["error_message"]
    assert js.published == []


def test_command_allowed_by_the_mode_is_transmitted(
    db: Db, test_dsn: str, operator: dict[str, str],
) -> None:
    cmd = _due_command(db, "recovery")
    _telemetry(db, "safe", timedelta(seconds=30))

    js = _execute_once(test_dsn)

    assert db.fetchval("SELECT status FROM commands WHERE id = $1::uuid", cmd) == "sent"
    assert [s for s, _ in js.published] == ["commands.SAT1"]
