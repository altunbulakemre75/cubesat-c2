"""JWT authentication utilities — access + refresh tokens, blacklist via Redis."""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt

from src.config import settings
from src.storage import redis_client

# Refresh tokens live longer than access tokens so users don't have to log in
# every hour. Short-lived access + long-lived refresh is the standard pattern.
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7


def _truncate_for_bcrypt(password: str) -> bytes:
    """bcrypt only considers the first 72 bytes of the password. Modern
    bcrypt versions REJECT longer inputs with ValueError instead of silently
    truncating like the older C library did. We truncate explicitly so a
    user with a long passphrase doesn't get a 500 from the API."""
    return password.encode()[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_truncate_for_bcrypt(password), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(_truncate_for_bcrypt(plain), hashed.encode())


def _make_token(subject: str, role: str, *, ttl: timedelta, kind: str) -> str:
    """Encode a JWT with kind=access|refresh and a unique jti for blacklisting."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "kind": kind,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, role: str) -> str:
    return _make_token(
        subject, role,
        ttl=timedelta(minutes=settings.jwt_expire_minutes or ACCESS_TOKEN_EXPIRE_MINUTES),
        kind="access",
    )


def create_refresh_token(subject: str, role: str) -> str:
    return _make_token(
        subject, role,
        ttl=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        kind="refresh",
    )


def decode_token(token: str) -> dict:
    """Raises JWTError on invalid/expired token. Does NOT check blacklist."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


# ── Token revocation (logout) — backed by Redis ──────────────────────────────

_BLACKLIST_PREFIX = "auth:revoked:"


async def revoke_token(jti: str, ttl_seconds: int) -> None:
    """Add a jti to the revocation list. ttl_seconds should match the
    remaining lifetime of the token so Redis evicts it on its own."""
    r = redis_client.get_client()
    await r.set(f"{_BLACKLIST_PREFIX}{jti}", "1", ex=max(1, ttl_seconds))


async def is_token_revoked(jti: str) -> bool:
    """Returns True only when Redis is reachable AND the jti is in the
    revocation list. If Redis is down we fail OPEN (not revoked) — auth
    should keep working when the cache is unavailable. Logout/refresh-rotate
    paths still depend on Redis but they're best-effort either way."""
    try:
        r = redis_client.get_client()
        return await r.exists(f"{_BLACKLIST_PREFIX}{jti}") > 0
    except Exception:  # noqa: BLE001
        return False
