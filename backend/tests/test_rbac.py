"""
RBAC boundary tests — verify role enforcement without a live DB.

These tests use mocked dependencies so they run in CI without
Docker/TimescaleDB. They test the RBAC gate only, not business logic.
"""

import pytest

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


# Route-level RBAC (role resolved from the DB per request) is covered
# against a real database in tests/integration/test_auth_sessions.py.


# ── Password policy unit tests ────────────────────────────────────────────────

def test_password_hash_and_verify():
    from src.api.auth import hash_password, verify_password
    hashed = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong-password", hashed)
