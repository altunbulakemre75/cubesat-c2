"""
RBAC boundary tests — verify role enforcement without a live DB.

These tests use mocked dependencies so they run in CI without
Docker/TimescaleDB. They test the RBAC gate only, not business logic.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_access_token
from src.api.rbac import Role, require_role


# ── require_role unit tests ────────────────────────────────────────────────────

def test_admin_passes_admin_gate():
    require_role(Role.ADMIN, "admin")  # must not raise


def test_operator_passes_operator_gate():
    require_role(Role.OPERATOR, "operator")


def test_viewer_passes_viewer_gate():
    require_role(Role.VIEWER, "viewer")


def test_viewer_blocked_by_operator_gate():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        require_role(Role.OPERATOR, "viewer")
    assert exc.value.status_code == 403


def test_viewer_blocked_by_admin_gate():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        require_role(Role.ADMIN, "viewer")
    assert exc.value.status_code == 403


def test_operator_blocked_by_admin_gate():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        require_role(Role.ADMIN, "operator")
    assert exc.value.status_code == 403


def test_unknown_role_blocked():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        require_role(Role.VIEWER, "superuser")
    assert exc.value.status_code == 403


# ── JWT token content tests ────────────────────────────────────────────────────

def test_token_encodes_role():
    from src.api.auth import decode_token
    token = create_access_token("alice", "operator")
    payload = decode_token(token)
    assert payload["sub"] == "alice"
    assert payload["role"] == "operator"


def test_expired_token_raises():
    from datetime import timedelta
    from jose import JWTError
    from src.api.auth import decode_token
    from src.config import settings
    from jose import jwt
    from datetime import datetime, timezone

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode(
        {"sub": "alice", "role": "admin", "exp": past},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(JWTError):
        decode_token(token)


# ── Minimal FastAPI route integration tests (no DB) ──────────────────────────

def _make_app_with_token(role: str) -> tuple[FastAPI, TestClient]:
    """Returns a tiny FastAPI app + client that injects a JWT for the given role."""
    from fastapi import Depends
    from src.api.deps import current_user
    from src.api.auth import create_access_token

    app = FastAPI()
    token = create_access_token("testuser", role)

    @app.get("/test-admin")
    async def admin_only(user=Depends(current_user)):
        require_role(Role.ADMIN, user["role"])
        return {"ok": True}

    @app.get("/test-operator")
    async def operator_only(user=Depends(current_user)):
        require_role(Role.OPERATOR, user["role"])
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    client.headers = {"Authorization": f"Bearer {token}"}
    return app, client


def test_viewer_cannot_reach_admin_route():
    _, client = _make_app_with_token("viewer")
    resp = client.get("/test-admin")
    assert resp.status_code == 403


def test_operator_cannot_reach_admin_route():
    _, client = _make_app_with_token("operator")
    resp = client.get("/test-admin")
    assert resp.status_code == 403


def test_admin_can_reach_admin_route():
    _, client = _make_app_with_token("admin")
    resp = client.get("/test-admin")
    assert resp.status_code == 200


def test_viewer_cannot_reach_operator_route():
    _, client = _make_app_with_token("viewer")
    resp = client.get("/test-operator")
    assert resp.status_code == 403


def test_operator_can_reach_operator_route():
    _, client = _make_app_with_token("operator")
    resp = client.get("/test-operator")
    assert resp.status_code == 200


def test_no_token_returns_401():
    _, client = _make_app_with_token("admin")
    client.headers = {}  # remove token
    resp = client.get("/test-admin")
    assert resp.status_code == 401  # HTTPBearer: no credentials = 401 Unauthorized


# ── Password policy unit tests ────────────────────────────────────────────────

def test_password_hash_and_verify():
    from src.api.auth import hash_password, verify_password
    hashed = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong-password", hashed)
