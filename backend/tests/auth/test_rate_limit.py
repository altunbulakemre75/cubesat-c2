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
async def test_debug_mode_does_not_disable_rate_limit():
    """The default docker-compose runs with DEBUG=true, so tying the bypass
    to DEBUG left every out-of-the-box install without brute-force
    protection. Only the explicit flag may disable it."""
    fake = MagicMock()
    fake.incr = AsyncMock(return_value=999)
    fake.expire = AsyncMock()
    with patch("src.api.rate_limit.settings.debug", True),          patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        ok = await check_login_rate(_request(), "alice")
    assert ok is False


@pytest.mark.asyncio
async def test_explicit_flag_disables_rate_limit_without_touching_redis():
    fake = MagicMock()
    fake.incr = AsyncMock(return_value=999)
    fake.delete = AsyncMock()
    with patch("src.api.rate_limit.settings.login_rate_limit_enabled", False),          patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        assert await check_login_rate(_request(), "alice") is True
        await reset_login_rate(_request(), "alice")
    fake.incr.assert_not_called()
    fake.delete.assert_not_called()


async def _key_for(req: MagicMock, trusted: list[str]) -> str:
    fake = MagicMock()
    captured: dict[str, str] = {}

    async def fake_incr(key: str) -> int:
        captured["key"] = key
        return 1
    fake.incr = fake_incr
    fake.expire = AsyncMock()
    with patch("src.api.rate_limit.settings.trusted_proxies", trusted),          patch("src.api.rate_limit.redis_client.get_client", return_value=fake):
        await check_login_rate(req, "alice")
    return captured["key"]


@pytest.mark.asyncio
async def test_x_forwarded_for_ignored_when_peer_is_not_a_trusted_proxy():
    """A client talking to the backend directly can put anything in XFF.
    Trusting it let an attacker rotate the header to get unlimited tries."""
    req = _request("198.51.100.20")
    req.headers = {"x-forwarded-for": "203.0.113.7"}
    key = await _key_for(req, trusted=[])
    assert "198.51.100.20" in key
    assert "203.0.113.7" not in key


@pytest.mark.asyncio
async def test_x_forwarded_for_used_when_peer_is_a_trusted_proxy():
    req = _request("10.0.0.1")
    req.headers = {"x-forwarded-for": "203.0.113.7"}
    key = await _key_for(req, trusted=["10.0.0.0/8"])
    assert "203.0.113.7" in key


@pytest.mark.asyncio
async def test_spoofed_prefix_in_x_forwarded_for_is_ignored():
    """nginx appends the real peer to whatever XFF the client sent, so the
    client controls every entry left of the proxy's own. The rightmost
    untrusted entry is the only one we can believe."""
    req = _request("10.0.0.1")
    req.headers = {"x-forwarded-for": "6.6.6.6, 203.0.113.7"}
    key = await _key_for(req, trusted=["10.0.0.1"])
    assert "203.0.113.7" in key
    assert "6.6.6.6" not in key


@pytest.mark.asyncio
async def test_trusted_proxy_given_by_hostname():
    """In docker-compose the proxy's IP is assigned at runtime, so the
    setting accepts a service name and resolves it."""
    req = _request("172.20.0.5")
    req.headers = {"x-forwarded-for": "203.0.113.7"}
    with patch("src.api.rate_limit._resolve_host", new=AsyncMock(return_value={"172.20.0.5"})):
        key = await _key_for(req, trusted=["frontend"])
    assert "203.0.113.7" in key
