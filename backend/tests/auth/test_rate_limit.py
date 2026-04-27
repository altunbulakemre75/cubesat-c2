"""
/auth/login rate limit behaviour.

Mocks Redis so the test doesn't hit a real instance. Behaviour:
  - first N attempts allowed (counter < max)
  - N+1 returns False (rate-limited)
  - successful login resets the counter
  - Redis down → fail OPEN (allow login)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.rate_limit import (
    DEFAULT_MAX_ATTEMPTS,
    check_login_rate,
    reset_login_rate,
)


def _request(ip: str = "1.2.3.4") -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock()
    req.client.host = ip
    return req


@pytest.mark.asyncio
async def test_first_attempt_allowed():
    fake = MagicMock()
    fake.incr = AsyncMock(return_value=1)
    fake.expire = AsyncMock()
    with patch("src.api.rate_limit.settings.debug", False), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        ok = await check_login_rate(_request(), "alice")
    assert ok is True
    fake.expire.assert_awaited_once()  # TTL set on the first hit only


@pytest.mark.asyncio
async def test_max_attempts_still_allowed():
    """The Nth attempt (== max) is still allowed; only N+1 trips."""
    fake = MagicMock()
    fake.incr = AsyncMock(return_value=DEFAULT_MAX_ATTEMPTS)
    fake.expire = AsyncMock()
    with patch("src.api.rate_limit.settings.debug", False), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        ok = await check_login_rate(_request(), "alice")
    assert ok is True


@pytest.mark.asyncio
async def test_one_over_max_attempts_blocked():
    fake = MagicMock()
    fake.incr = AsyncMock(return_value=DEFAULT_MAX_ATTEMPTS + 1)
    fake.expire = AsyncMock()
    with patch("src.api.rate_limit.settings.debug", False), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        ok = await check_login_rate(_request(), "alice")
    assert ok is False


@pytest.mark.asyncio
async def test_redis_down_fails_open():
    """If Redis is unavailable we'd rather let login through than lock
    out every user — same posture as the JWT revocation check."""
    fake = MagicMock()
    fake.incr = AsyncMock(side_effect=ConnectionError("redis unreachable"))
    with patch("src.api.rate_limit.settings.debug", False), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        ok = await check_login_rate(_request(), "alice")
    assert ok is True


@pytest.mark.asyncio
async def test_reset_drops_the_counter():
    fake = MagicMock()
    fake.delete = AsyncMock()
    with patch("src.api.rate_limit.settings.debug", False), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        await reset_login_rate(_request(), "alice")
    fake.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_distinct_ip_users_get_separate_counters():
    """Two attackers from different IPs against the same username should
    be tracked separately — assert distinct Redis keys."""
    fake = MagicMock()
    seen_keys = []

    async def fake_incr(key):
        seen_keys.append(key)
        return 1
    fake.incr = fake_incr
    fake.expire = AsyncMock()
    with patch("src.api.rate_limit.settings.debug", False), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        await check_login_rate(_request("1.1.1.1"), "alice")
        await check_login_rate(_request("2.2.2.2"), "alice")
    assert len(seen_keys) == 2
    assert seen_keys[0] != seen_keys[1]


@pytest.mark.asyncio
async def test_debug_mode_bypasses_rate_limit():
    """In dev/test the auto-login fires constantly — bypassing rate limit
    when DEBUG=true keeps the dev workflow usable. Production must NOT set
    DEBUG=true (config.py validates this)."""
    fake = MagicMock()
    fake.incr = AsyncMock(return_value=999)  # would normally trigger
    fake.expire = AsyncMock()
    with patch("src.api.rate_limit.settings.debug", True), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        ok = await check_login_rate(_request(), "alice")
    assert ok is True
    fake.incr.assert_not_called()  # we should short-circuit BEFORE Redis


@pytest.mark.asyncio
async def test_x_forwarded_for_used_when_present():
    """Behind a reverse proxy, request.client.host is the proxy. The real
    client IP comes from X-Forwarded-For. Our key MUST reflect that."""
    fake = MagicMock()
    captured = {}

    async def fake_incr(key):
        captured["key"] = key
        return 1
    fake.incr = fake_incr
    fake.expire = AsyncMock()

    req = _request("10.0.0.1")  # the proxy
    req.headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}

    with patch("src.api.rate_limit.settings.debug", False), \
         patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        await check_login_rate(req, "alice")

    assert "203.0.113.7" in captured["key"]
    assert "10.0.0.1" not in captured["key"]
