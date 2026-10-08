"""
Session validity is decided by the database, not by claims baked into a JWT.

Regression tests for the Dream Security report against v0.1.0:
  #1 refresh token kept minting admin tokens after the user was demoted
  #2 revoked tokens became valid again whenever Redis was unreachable
plus the same class of bug on the access-token path.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.integration.conftest import Db, bearer, login

ADMIN_PW = "admin-password-123"
VIEWER_PW = "viewer-password-123"


def test_refresh_after_demotion_issues_viewer_session(client: TestClient, db: Db) -> None:
    # Report #1 PoC: demote in DB, then use the old refresh token.
    db.create_user("admin", ADMIN_PW, "admin")
    tokens = login(client, "admin", ADMIN_PW)

    db.execute("UPDATE users SET role = 'viewer' WHERE username = 'admin'")

    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    if resp.status_code == 200:
        new_access = resp.json()["access_token"]
        assert client.get("/users", headers=bearer(new_access)).status_code == 403
    else:
        assert resp.status_code == 401


def test_existing_access_token_loses_admin_rights_immediately(
    client: TestClient, db: Db,
) -> None:
    db.create_user("admin", ADMIN_PW, "admin")
    access = login(client, "admin", ADMIN_PW)["access_token"]
    assert client.get("/users", headers=bearer(access)).status_code == 200

    db.execute("UPDATE users SET role = 'viewer' WHERE username = 'admin'")

    assert client.get("/users", headers=bearer(access)).status_code == 403


def test_role_change_via_api_ends_target_sessions(client: TestClient, db: Db) -> None:
    db.create_user("root", ADMIN_PW, "admin")
    db.create_user("bob", ADMIN_PW, "admin")
    root = login(client, "root", ADMIN_PW)["access_token"]
    bob = login(client, "bob", ADMIN_PW)

    resp = client.patch("/users/bob/role", json={"role": "viewer"}, headers=bearer(root))
    assert resp.status_code == 200

    assert client.get("/satellites", headers=bearer(bob["access_token"])).status_code == 401
    refresh = client.post("/auth/refresh", json={"refresh_token": bob["refresh_token"]})
    assert refresh.status_code == 401


def test_disabled_user_tokens_stop_working(client: TestClient, db: Db) -> None:
    db.create_user("eve", VIEWER_PW, "operator")
    tokens = login(client, "eve", VIEWER_PW)

    db.execute("UPDATE users SET active = FALSE WHERE username = 'eve'")

    assert client.get("/satellites", headers=bearer(tokens["access_token"])).status_code == 401
    refresh = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401


def test_deleted_user_tokens_stop_working(client: TestClient, db: Db) -> None:
    db.create_user("ghost", VIEWER_PW, "viewer")
    access = login(client, "ghost", VIEWER_PW)["access_token"]

    db.execute("DELETE FROM users WHERE username = 'ghost'")

    assert client.get("/satellites", headers=bearer(access)).status_code == 401


def test_logged_out_token_stays_revoked_when_redis_is_down(
    client: TestClient, db: Db,
) -> None:
    # Report #2 PoC: logout, then take Redis away.
    db.create_user("admin", ADMIN_PW, "admin")
    access = login(client, "admin", ADMIN_PW)["access_token"]
    assert client.post("/auth/logout", headers=bearer(access)).status_code == 204

    class _DeadRedis:
        def __getattr__(self, _name: str) -> object:
            raise ConnectionError("redis unreachable")

    with patch("src.storage.redis_client.get_client", return_value=_DeadRedis()):
        assert client.get("/users", headers=bearer(access)).status_code == 401


def test_logout_also_revokes_the_refresh_token(client: TestClient, db: Db) -> None:
    db.create_user("admin", ADMIN_PW, "admin")
    tokens = login(client, "admin", ADMIN_PW)

    resp = client.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=bearer(tokens["access_token"]),
    )
    assert resp.status_code == 204

    refresh = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401


def test_rotated_refresh_token_cannot_be_replayed(client: TestClient, db: Db) -> None:
    db.create_user("admin", ADMIN_PW, "admin")
    first = login(client, "admin", ADMIN_PW)

    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200

    replay = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401


def test_refresh_token_replay_kills_the_whole_session_family(
    client: TestClient, db: Db,
) -> None:
    # A replayed refresh token means one copy leaked. We can't tell which
    # holder is legitimate, so every session for that user ends.
    db.create_user("admin", ADMIN_PW, "admin")
    first = login(client, "admin", ADMIN_PW)
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert client.get("/users", headers=bearer(second["access_token"])).status_code == 401


def test_refresh_token_is_not_accepted_as_access_token(client: TestClient, db: Db) -> None:
    db.create_user("admin", ADMIN_PW, "admin")
    refresh = login(client, "admin", ADMIN_PW)["refresh_token"]
    assert client.get("/users", headers=bearer(refresh)).status_code == 401


def test_password_change_returns_new_tokens_and_ends_old_sessions(
    client: TestClient, db: Db,
) -> None:
    db.create_user("admin", ADMIN_PW, "admin")
    other_device = login(client, "admin", ADMIN_PW)
    this_device = login(client, "admin", ADMIN_PW)

    resp = client.post(
        "/auth/change-password",
        json={"old_password": ADMIN_PW, "new_password": "a-brand-new-password"},
        headers=bearer(this_device["access_token"]),
    )
    assert resp.status_code == 200
    fresh = resp.json()

    assert client.get("/users", headers=bearer(fresh["access_token"])).status_code == 200
    assert client.get("/users", headers=bearer(other_device["access_token"])).status_code == 401
    assert client.get("/users", headers=bearer(this_device["access_token"])).status_code == 401


# ── Offboarding: an admin can disable an account without touching the DB ────

def test_admin_can_deactivate_a_user_and_their_sessions_end(
    client: TestClient, db: Db,
) -> None:
    db.create_user("root", ADMIN_PW, "admin")
    db.create_user("leaver", VIEWER_PW, "operator")
    root = login(client, "root", ADMIN_PW)["access_token"]
    leaver = login(client, "leaver", VIEWER_PW)

    resp = client.patch("/users/leaver/active", json={"active": False}, headers=bearer(root))
    assert resp.status_code == 200
    assert resp.json() == {"username": "leaver", "active": False}

    assert client.get("/satellites", headers=bearer(leaver["access_token"])).status_code == 401
    relogin = client.post("/auth/login", json={"username": "leaver", "password": VIEWER_PW})
    assert relogin.status_code == 403


def test_admin_cannot_deactivate_themselves(client: TestClient, db: Db) -> None:
    db.create_user("root", ADMIN_PW, "admin")
    db.create_user("second", ADMIN_PW, "admin")
    root = login(client, "root", ADMIN_PW)["access_token"]

    resp = client.patch("/users/root/active", json={"active": False}, headers=bearer(root))
    assert resp.status_code == 400


def test_non_admin_cannot_deactivate_users(client: TestClient, db: Db) -> None:
    db.create_user("op", VIEWER_PW, "operator")
    db.create_user("victim", VIEWER_PW, "viewer")
    op = login(client, "op", VIEWER_PW)["access_token"]

    resp = client.patch("/users/victim/active", json={"active": False}, headers=bearer(op))
    assert resp.status_code == 403
