"""
Redis-backed rate limiter for /auth/login.

Sliding-window-ish: a single counter per (username + remote IP) with
TTL = window_seconds. Once the count exceeds max_attempts the key is
considered tripped until it expires.

Anonymous-friendly: if Redis is down we fail OPEN (allow login) — same
trade-off as the existing JWT revocation check. Locking everyone out
because the cache is unreachable is worse than a brief brute-force
exposure window.
"""

from __future__ import annotations

import logging

from fastapi import Request

from src.config import settings
from src.storage import redis_client

logger = logging.getLogger(__name__)

# Block bursts: 5 wrong tries per 5 minutes.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WINDOW_SECONDS = 300
_KEY_PREFIX = "auth:ratelimit:login:"


def _client_ip(request: Request) -> str:
    """Best-effort client IP. X-Forwarded-For if behind a proxy, else
    request.client.host."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _key(username: str, ip: str) -> str:
    # Username gets folded with IP so a single attacker can't get past the
    # cap by rotating usernames against one IP, AND a legitimate user
    # from a NAT'd network isn't blocked when a sibling fails.
    return f"{_KEY_PREFIX}{ip}:{username}"


async def check_login_rate(
    request: Request,
    username: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> bool:
    """Returns True if the request is allowed, False if rate-limited.

    Increments the counter on EVERY call (success or failure). The
    auth route is responsible for catching successful logins and
    calling reset_login_rate so a flurry of correct logins isn't
    falsely blocked."""
    # In DEBUG mode (local dev, Playwright E2E, conftest tests) the dev
    # auto-login fires admin/admin from every page open — that would lock
    # itself out within 5 attempts. Production/staging have DEBUG=false.
    if getattr(settings, "debug", False):
        return True

    ip = _client_ip(request)
    key = _key(username, ip)
    try:
        r = redis_client.get_client()
        current = await r.incr(key)
        if current == 1:
            await r.expire(key, window_seconds)
        if current > max_attempts:
            logger.warning(
                "Login rate limit hit | ip=%s user=%s attempts=%d",
                ip, username, current,
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        # Fail OPEN — same posture as JWT revocation when Redis is down.
        logger.warning("Rate limit check failed (allowing): %s", exc)
        return True


async def reset_login_rate(request: Request, username: str) -> None:
    """Drop the counter on successful auth so a legitimate user with a
    typo in their first attempt doesn't get throttled on subsequent
    correct logins."""
    ip = _client_ip(request)
    try:
        r = redis_client.get_client()
        await r.delete(_key(username, ip))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Rate limit reset failed (non-fatal): %s", exc)
