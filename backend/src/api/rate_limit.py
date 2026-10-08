"""
Redis-backed rate limiter for /auth/login.

Sliding-window-ish: a single counter per (username + remote IP) with
TTL = window_seconds. Once the count exceeds max_attempts the key is
considered tripped until it expires.

Anonymous-friendly: if Redis is down we fail OPEN (allow login). Unlike
token revocation (which lives in Postgres), the worst case here is a brief
window of unthrottled guessing against bcrypt, while failing closed would
lock every operator out of the console exactly when infrastructure is
already degraded.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time

from fastapi import Request

from src.config import settings
from src.storage import redis_client

logger = logging.getLogger(__name__)

# Block bursts: 5 wrong tries per 5 minutes.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WINDOW_SECONDS = 300
_KEY_PREFIX = "auth:ratelimit:login:"

# Hostname entries in TRUSTED_PROXIES are re-resolved at most this often —
# container IPs change on restart, but DNS on every login would be wasteful.
_RESOLVE_TTL_S = 60.0
_resolve_cache: dict[str, tuple[float, set[str]]] = {}


async def _resolve_host(host: str) -> set[str]:
    cached = _resolve_cache.get(host)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        ips = {str(info[4][0]) for info in infos}
    except OSError as exc:
        logger.warning("Could not resolve trusted proxy %r: %s", host, exc)
        ips = set()
    _resolve_cache[host] = (now + _RESOLVE_TTL_S, ips)
    return ips


async def _is_trusted_proxy(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for entry in settings.trusted_proxies:
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
            continue
        except ValueError:
            pass  # not an IP/CIDR — treat as a hostname
        if addr in await _resolve_host(entry):
            return True
    return False


async def client_ip(request: Request) -> str:
    """Best-effort client IP that a client cannot forge.

    X-Forwarded-For is only honoured when the TCP peer is a configured
    trusted proxy. Proxies append the address they saw, so everything left
    of the proxy's own entry is client-controlled: walk from the right and
    return the first address that isn't one of our proxies."""
    peer = request.client.host if request.client else "unknown"
    if not await _is_trusted_proxy(peer):
        return peer

    xff = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        if not await _is_trusted_proxy(hop):
            return hop
    return hops[0] if hops else peer


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
    if not settings.login_rate_limit_enabled:
        return True

    ip = await client_ip(request)
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
        logger.warning("Rate limit check failed (allowing): %s", exc)
        return True


async def reset_login_rate(request: Request, username: str) -> None:
    """Drop the counter on successful auth so a legitimate user with a
    typo in their first attempt doesn't get throttled on subsequent
    correct logins."""
    if not settings.login_rate_limit_enabled:
        return
    ip = await client_ip(request)
    try:
        r = redis_client.get_client()
        await r.delete(_key(username, ip))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Rate limit reset failed (non-fatal): %s", exc)
