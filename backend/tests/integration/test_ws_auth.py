"""
WebSocket authentication.

Regression tests for Dream Security report #3 (WS skipped the revocation
check) and the related gaps found while fixing it:
  - WS accepted refresh tokens (7-day lifetime) as well as access tokens
  - the access token travelled in the URL, which uvicorn/Loki log verbatim
  - an open socket was never re-checked, so it outlived logout,
    deactivation and token expiry
  - the telemetry stream replayed the stream's whole history on connect
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from nats.js.api import DeliverPolicy
from starlette.websockets import WebSocketDisconnect

from src.config import settings
from tests.integration.conftest import Db, bearer, login

PW = "password-for-ws-tests"
FRAME = {"satellite_id": "CUBESAT1", "sequence": 1}


class _Msg:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def ack(self) -> None:
        return None


class _FakeSub:
    """Delivers the queued frames, then blocks like an idle subscription."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = frames
        self.unsubscribed = False

    @property
    def messages(self) -> AsyncIterator[_Msg]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[_Msg]:
        for f in self._frames:
            yield _Msg(json.dumps(f).encode())
        await asyncio.Event().wait()

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _FakeJetStream:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = frames
        self.subscribe_calls: list[tuple[str, dict[str, Any]]] = []

    async def subscribe(self, subject: str, **kwargs: Any) -> _FakeSub:
        self.subscribe_calls.append((subject, kwargs))
        return _FakeSub(self.frames)


class _FakeNats:
    is_connected = True

    def __init__(self, js: _FakeJetStream) -> None:
        self._js = js

    def jetstream(self) -> _FakeJetStream:
        return self._js


@pytest.fixture
def fake_js() -> Iterator[_FakeJetStream]:
    js = _FakeJetStream([FRAME])
    with patch("src.api.ws._get_shared_nats", new=AsyncMock(return_value=_FakeNats(js))):
        yield js


def _ticket(client: TestClient, access: str) -> str:
    resp = client.post("/auth/ws-ticket", headers=bearer(access))
    assert resp.status_code == 200, resp.text
    return str(resp.json()["ticket"])


def _rejected(client: TestClient, url: str) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url) as ws:
            ws.receive_text()
    return exc.value.code


def test_ticket_opens_the_telemetry_stream(
    client: TestClient, db: Db, fake_js: _FakeJetStream,
) -> None:
    db.create_user("ann", PW, "viewer")
    ticket = _ticket(client, login(client, "ann", PW)["access_token"])

    with client.websocket_connect(f"/ws/telemetry/CUBESAT1?ticket={ticket}") as ws:
        assert json.loads(ws.receive_text()) == FRAME


def test_telemetry_stream_only_delivers_new_messages(
    client: TestClient, db: Db, fake_js: _FakeJetStream,
) -> None:
    # DeliverAll (the default) replayed the stream's entire history on
    # every page load.
    db.create_user("ann", PW, "viewer")
    ticket = _ticket(client, login(client, "ann", PW)["access_token"])

    with client.websocket_connect(f"/ws/telemetry/CUBESAT1?ticket={ticket}") as ws:
        ws.receive_text()

    subject, kwargs = fake_js.subscribe_calls[0]
    assert subject == "telemetry.canonical.CUBESAT1"
    assert kwargs["config"].deliver_policy == DeliverPolicy.NEW


def test_raw_access_token_is_not_accepted_in_the_url(
    client: TestClient, db: Db, fake_js: _FakeJetStream,
) -> None:
    db.create_user("ann", PW, "viewer")
    access = login(client, "ann", PW)["access_token"]
    assert _rejected(client, f"/ws/telemetry/CUBESAT1?ticket={access}") == 1008


def test_refresh_token_is_not_accepted(client: TestClient, db: Db, fake_js: _FakeJetStream) -> None:
    db.create_user("ann", PW, "viewer")
    refresh = login(client, "ann", PW)["refresh_token"]
    assert _rejected(client, f"/ws/telemetry/CUBESAT1?ticket={refresh}") == 1008


def test_ticket_dies_with_the_session_that_issued_it(
    client: TestClient, db: Db, fake_js: _FakeJetStream,
) -> None:
    # Report #3 PoC: log out, then reuse the credential on the WebSocket.
    db.create_user("ann", PW, "viewer")
    access = login(client, "ann", PW)["access_token"]
    ticket = _ticket(client, access)
    client.post("/auth/logout", headers=bearer(access))

    assert _rejected(client, f"/ws/telemetry/CUBESAT1?ticket={ticket}") == 1008


def test_ticket_is_single_use(client: TestClient, db: Db, fake_js: _FakeJetStream) -> None:
    db.create_user("ann", PW, "viewer")
    ticket = _ticket(client, login(client, "ann", PW)["access_token"])

    with client.websocket_connect(f"/ws/telemetry/CUBESAT1?ticket={ticket}") as ws:
        ws.receive_text()

    assert _rejected(client, f"/ws/telemetry/CUBESAT1?ticket={ticket}") == 1008


def test_expired_ticket_is_rejected(client: TestClient, db: Db, fake_js: _FakeJetStream) -> None:
    db.create_user("ann", PW, "viewer")
    past = datetime.now(UTC) - timedelta(minutes=5)
    stale = jwt.encode(
        {"sub": "ann", "kind": "ws", "ver": 0, "jti": "t1", "sid": "s1",
         "sexp": int((past + timedelta(hours=1)).timestamp()),
         "iat": past, "exp": past + timedelta(seconds=30)},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm,
    )
    assert _rejected(client, f"/ws/telemetry/CUBESAT1?ticket={stale}") == 1008


def test_events_stream_checks_the_current_db_role(
    client: TestClient, db: Db, fake_js: _FakeJetStream,
) -> None:
    db.create_user("ops", PW, "operator")
    ticket = _ticket(client, login(client, "ops", PW)["access_token"])
    db.execute("UPDATE users SET role = 'viewer' WHERE username = 'ops'")

    assert _rejected(client, f"/ws/events?ticket={ticket}") == 1008


def test_open_socket_is_closed_when_the_user_is_disabled(
    client: TestClient, db: Db, fake_js: _FakeJetStream,
) -> None:
    db.create_user("ann", PW, "viewer")
    ticket = _ticket(client, login(client, "ann", PW)["access_token"])

    with patch("src.api.ws._GUARD_INTERVAL_S", 0.1):
        with client.websocket_connect(f"/ws/telemetry/CUBESAT1?ticket={ticket}") as ws:
            ws.receive_text()
            db.execute("UPDATE users SET active = FALSE WHERE username = 'ann'")
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
    assert exc.value.code == 1008


def test_open_socket_is_closed_when_the_session_expires(
    client: TestClient, db: Db, fake_js: _FakeJetStream,
) -> None:
    # The client is told to reconnect with a fresh ticket (4001), not to
    # log the user out (1008).
    db.create_user("ann", PW, "viewer")
    with patch.object(settings, "jwt_expire_minutes", 0.005):  # ~0.3 s session
        access = login(client, "ann", PW)["access_token"]
        ticket = _ticket(client, access)

    with patch("src.api.ws._GUARD_INTERVAL_S", 0.1):
        with client.websocket_connect(f"/ws/telemetry/CUBESAT1?ticket={ticket}") as ws:
            ws.receive_text()
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
    assert exc.value.code == 4001
