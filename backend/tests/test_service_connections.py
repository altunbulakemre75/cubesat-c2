"""Every service connection must carry credentials from settings — an
unauthenticated client would simply be refused by the hardened compose
stack, which is how v0.1.0's open NATS/Redis went unnoticed."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Patch settings through the module that reads them: tests/test_config.py
# reloads src.config, which leaves other modules holding the old object.


async def test_nats_connections_authenticate(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.storage import nats_conn

    monkeypatch.setattr(nats_conn.settings, "nats_user", "backend")
    monkeypatch.setattr(nats_conn.settings, "nats_password", "s3cret")
    with patch("src.storage.nats_conn.nats.connect", new=AsyncMock()) as connect:
        await nats_conn.connect_nats()
    kwargs = connect.call_args.kwargs
    assert kwargs["user"] == "backend"
    assert kwargs["password"] == "s3cret"


async def test_ws_shared_connection_uses_the_same_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import ws

    monkeypatch.setattr(ws, "_shared_nc", None)
    with patch("src.api.ws.connect_nats", new=AsyncMock()) as connect:
        await ws._get_shared_nats()
    connect.assert_awaited_once()


def test_redis_client_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.storage import redis_client

    monkeypatch.setattr(redis_client.settings, "redis_password", "r3dis")
    monkeypatch.setattr(redis_client, "_client", None)
    with patch("src.storage.redis_client.aioredis.Redis") as redis_cls:
        redis_client.get_client()
    assert redis_cls.call_args.kwargs["password"] == "r3dis"
    monkeypatch.setattr(redis_client, "_client", None)
