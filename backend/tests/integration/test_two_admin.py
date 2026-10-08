"""
Two-admin approval for critical commands (separation, factory_reset).

v0.1.0 inserted the first admin's command as 'pending', and the scheduler
moves every 'pending' command to 'scheduled' within 5 s — so one admin
alone could transmit a separation command. The second-admin "approval"
was never actually waited for.
"""

from __future__ import annotations

import asyncio
import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.scheduler.service import CommandScheduler
from src.storage.db import _init_connection
from tests.integration.conftest import Db, bearer, login

PW = "password-two-admin"


@pytest.fixture
def admins(client: TestClient, db: Db) -> dict[str, str]:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1')")
    # Fresh NOMINAL telemetry: the mode policy allows everything.
    db.execute(
        "INSERT INTO telemetry (time, satellite_id, source, sequence, mode) "
        "VALUES (NOW(), 'SAT1', 'ax25', 1, 'nominal')"
    )
    db.create_user("alice", PW, "admin")
    db.create_user("bob", PW, "admin")
    db.create_user("olga", PW, "operator")
    return {u: login(client, u, PW)["access_token"] for u in ("alice", "bob", "olga")}


def _create_separation(client: TestClient, token: str) -> dict[str, object]:
    resp = client.post(
        "/commands",
        json={"satellite_id": "SAT1", "command_type": "separation"},
        headers=bearer(token),
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def _run_scheduler_cycle(dsn: str) -> None:
    async def _cycle() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            await CommandScheduler(pool, js=None)._schedule_once()  # type: ignore[arg-type]
        finally:
            await pool.close()
    asyncio.run(_cycle())


def _add_future_pass(db: Db) -> None:
    db.execute(
        "INSERT INTO ground_stations (id, name, latitude_deg, longitude_deg, uplink_capable) "
        "VALUES (1, 'GS', 39.9, 32.8, TRUE)"
    )
    db.execute(
        "INSERT INTO pass_schedule (satellite_id, station_id, aos, los, max_elevation_deg) "
        "VALUES ('SAT1', 1, NOW() + interval '10 minutes', NOW() + interval '20 minutes', 45)"
    )


def test_critical_command_waits_for_a_second_admin(
    client: TestClient, admins: dict[str, str],
) -> None:
    cmd = _create_separation(client, admins["alice"])
    assert cmd["status"] == "awaiting_approval"


def test_scheduler_does_not_pick_up_unapproved_commands(
    client: TestClient, db: Db, admins: dict[str, str], test_dsn: str,
) -> None:
    cmd = _create_separation(client, admins["alice"])
    _add_future_pass(db)

    _run_scheduler_cycle(test_dsn)

    status = db.fetchval("SELECT status FROM commands WHERE id = $1::uuid", cmd["id"])
    assert status == "awaiting_approval"


def test_second_admin_approval_releases_the_command(
    client: TestClient, db: Db, admins: dict[str, str], test_dsn: str,
) -> None:
    cmd = _create_separation(client, admins["alice"])
    _add_future_pass(db)

    resp = client.post(f"/commands/{cmd['id']}/approve", headers=bearer(admins["bob"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    assert resp.json()["approved_by"] == "bob"

    _run_scheduler_cycle(test_dsn)
    status = db.fetchval("SELECT status FROM commands WHERE id = $1::uuid", cmd["id"])
    assert status == "scheduled"


def test_creator_cannot_approve_their_own_command(
    client: TestClient, admins: dict[str, str],
) -> None:
    cmd = _create_separation(client, admins["alice"])
    resp = client.post(f"/commands/{cmd['id']}/approve", headers=bearer(admins["alice"]))
    assert resp.status_code == 403


def test_operator_cannot_approve(client: TestClient, admins: dict[str, str]) -> None:
    cmd = _create_separation(client, admins["alice"])
    resp = client.post(f"/commands/{cmd['id']}/approve", headers=bearer(admins["olga"]))
    assert resp.status_code == 403


def test_approving_twice_is_rejected(client: TestClient, admins: dict[str, str]) -> None:
    cmd = _create_separation(client, admins["alice"])
    client.post(f"/commands/{cmd['id']}/approve", headers=bearer(admins["bob"]))
    again = client.post(f"/commands/{cmd['id']}/approve", headers=bearer(admins["bob"]))
    assert again.status_code == 409


def test_unapproved_command_cannot_be_pushed_through_the_transition_api(
    client: TestClient, admins: dict[str, str],
) -> None:
    cmd = _create_separation(client, admins["alice"])
    resp = client.patch(
        f"/commands/{cmd['id']}/transition",
        json={"target_status": "scheduled"},
        headers=bearer(admins["olga"]),
    )
    assert resp.status_code == 422


def test_awaiting_command_can_be_cancelled(
    client: TestClient, db: Db, admins: dict[str, str],
) -> None:
    cmd = _create_separation(client, admins["alice"])
    resp = client.delete(f"/commands/{cmd['id']}", headers=bearer(admins["bob"]))
    assert resp.status_code == 204
    assert db.fetchval("SELECT status FROM commands WHERE id = $1::uuid", cmd["id"]) == "dead"


def test_ordinary_commands_are_not_held_for_approval(
    client: TestClient, admins: dict[str, str],
) -> None:
    resp = client.post(
        "/commands",
        json={"satellite_id": "SAT1", "command_type": "ping"},
        headers=bearer(admins["olga"]),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"
