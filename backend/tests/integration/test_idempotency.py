"""
Idempotent command submission and race-free state transitions.

"Idempotent commands" is one of the project's stated differentiators, yet
re-sending a request with the same idempotency_key hit the UNIQUE
constraint and returned 500. Transitions read the status and then wrote
it in a separate statement, so a concurrent scheduler update in between
could be overwritten.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import Db, bearer, login

PW = "idempotency-password"


@pytest.fixture
def operator(client: TestClient, db: Db) -> dict[str, str]:
    db.execute("INSERT INTO satellites (id, name) VALUES ('SAT1', 'SAT1')")
    db.execute(
        "INSERT INTO telemetry (time, satellite_id, source, sequence, mode) "
        "VALUES (NOW() - $1::interval, 'SAT1', 'ax25', 1, 'nominal')",
        timedelta(seconds=10),
    )
    db.create_user("op", PW, "operator")
    db.create_user("op2", PW, "operator")
    return bearer(login(client, "op", PW)["access_token"])


def _post(client: TestClient, headers: dict[str, str], **body: Any):  # type: ignore[no-untyped-def]
    payload = {"satellite_id": "SAT1", "command_type": "ping", **body}
    return client.post("/commands", json=payload, headers=headers)


def test_repeating_a_request_returns_the_original_command(
    client: TestClient, db: Db, operator: dict[str, str],
) -> None:
    first = _post(client, operator, idempotency_key="k-1")
    again = _post(client, operator, idempotency_key="k-1")

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert db.fetchval("SELECT COUNT(*) FROM commands") == 1


def test_reusing_a_key_for_a_different_command_is_a_conflict(
    client: TestClient, operator: dict[str, str],
) -> None:
    _post(client, operator, idempotency_key="k-2")
    other = _post(client, operator, idempotency_key="k-2", command_type="diagnostic")
    assert other.status_code == 409


def test_another_users_key_is_not_resolved_to_their_command(
    client: TestClient, db: Db, operator: dict[str, str],
) -> None:
    _post(client, operator, idempotency_key="k-3")
    other_user = bearer(login(client, "op2", PW)["access_token"])
    assert _post(client, other_user, idempotency_key="k-3").status_code == 409


def test_transition_from_a_state_the_command_has_left_is_refused(
    client: TestClient, db: Db, operator: dict[str, str],
) -> None:
    cmd = _post(client, operator).json()
    # The scheduler moved it on in the meantime.
    db.execute("UPDATE commands SET status = 'sent' WHERE id = $1::uuid", cmd["id"])

    resp = client.patch(
        f"/commands/{cmd['id']}/transition",
        json={"target_status": "scheduled"},
        headers=operator,
    )
    assert resp.status_code == 422
    assert db.fetchval("SELECT status FROM commands WHERE id = $1::uuid", cmd["id"]) == "sent"


def test_valid_transition_still_works(
    client: TestClient, operator: dict[str, str],
) -> None:
    cmd = _post(client, operator).json()
    resp = client.patch(
        f"/commands/{cmd['id']}/transition",
        json={"target_status": "scheduled"},
        headers=operator,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "scheduled"
