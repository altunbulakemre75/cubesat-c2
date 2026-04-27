"""
/health and /ready endpoint tests.

/health is a liveness probe — should be cheap and never fail because of
downstreams.
/ready is a readiness probe — must return 503 when any downstream
(DB, NATS, Redis) is unreachable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _build_test_app():
    """Build the FastAPI app with a no-op lifespan so we don't actually
    open DB/NATS/Redis connections during these unit tests."""
    from contextlib import asynccontextmanager
    from src.api.main import create_app

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app = create_app()
    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    return app


def test_health_returns_ok_unconditionally():
    app = _build_test_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_returns_ok_when_all_downstreams_healthy():
    app = _build_test_app()

    fake_pool = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(return_value=None)

    class _AcquireCtx:
        async def __aenter__(self):
            return fake_conn
        async def __aexit__(self, *_exc):
            return None
    fake_pool.acquire = lambda: _AcquireCtx()

    fake_nats = MagicMock()
    fake_nats.is_connected = True

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)

    with patch("src.api.main.get_pool", new=AsyncMock(return_value=fake_pool)), \
         patch("src.api.ws._get_shared_nats", new=AsyncMock(return_value=fake_nats)), \
         patch("src.storage.redis_client.get_client", return_value=fake_redis), \
         TestClient(app) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"db": True, "nats": True, "redis": True}


def test_ready_returns_503_when_db_down():
    app = _build_test_app()

    async def _boom():
        raise ConnectionError("db unreachable")

    fake_nats = MagicMock()
    fake_nats.is_connected = True

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)

    with patch("src.api.main.get_pool", new=AsyncMock(side_effect=_boom)), \
         patch("src.api.ws._get_shared_nats", new=AsyncMock(return_value=fake_nats)), \
         patch("src.storage.redis_client.get_client", return_value=fake_redis), \
         TestClient(app) as client:
        resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["db"] is False
    assert body["checks"]["nats"] is True
    assert body["checks"]["redis"] is True


def test_ready_returns_503_when_nats_disconnected():
    app = _build_test_app()

    fake_pool = MagicMock()
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(return_value=None)

    class _AcquireCtx:
        async def __aenter__(self):
            return fake_conn
        async def __aexit__(self, *_exc):
            return None
    fake_pool.acquire = lambda: _AcquireCtx()

    fake_nats = MagicMock()
    fake_nats.is_connected = False  # NATS hung up

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)

    with patch("src.api.main.get_pool", new=AsyncMock(return_value=fake_pool)), \
         patch("src.api.ws._get_shared_nats", new=AsyncMock(return_value=fake_nats)), \
         patch("src.storage.redis_client.get_client", return_value=fake_redis), \
         TestClient(app) as client:
        resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["nats"] is False


def test_ready_does_not_hang_when_downstream_is_slow():
    """If one downstream takes 30s to respond, /ready must still answer
    within ~2s. We can't easily test the actual timeout fire here, but
    we can ensure the endpoint returns within a small budget."""
    import time
    app = _build_test_app()

    async def _slow_pool():
        # Simulate a hung pool
        import asyncio as _aio
        await _aio.sleep(10)
        raise ConnectionError("never reached")

    fake_nats = MagicMock()
    fake_nats.is_connected = True
    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)

    with patch("src.api.main.get_pool", new=_slow_pool), \
         patch("src.api.ws._get_shared_nats", new=AsyncMock(return_value=fake_nats)), \
         patch("src.storage.redis_client.get_client", return_value=fake_redis), \
         TestClient(app) as client:
        t0 = time.monotonic()
        resp = client.get("/ready")
        elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"ready took {elapsed:.1f}s — timeout missing"
    assert resp.status_code == 503
    assert resp.json()["checks"]["db"] is False
