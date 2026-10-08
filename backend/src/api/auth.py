"""JWT authentication.

A JWT only proves *who* the caller is. Whether that session is still
allowed — and with which role — is decided by Postgres on every request:

- role and active flag are read from the users table, never from the token,
  so demotion and deactivation take effect immediately;
- every token carries the users.token_version it was minted under; bumping
  the column ends all of the user's sessions at once;
- logout and refresh rotation record the token's jti in revoked_tokens.

Postgres is already on every request's path, so this costs one indexed
query and removes Redis from the auth decision entirely: a cache outage can
neither resurrect revoked tokens nor lock operators out.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import bcrypt
from jose import JWTError, jwt

from src.config import settings

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


def _make_token(subject: str, *, version: int, ttl: timedelta, kind: str) -> str:
    """Encode a JWT with kind=access|refresh, the user's token_version and a
    unique jti for per-token revocation. Deliberately carries no role."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "kind": kind,
        "ver": version,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, version: int = 0) -> str:
    return _make_token(
        subject,
        version=version,
        ttl=timedelta(minutes=settings.jwt_expire_minutes or ACCESS_TOKEN_EXPIRE_MINUTES),
        kind="access",
    )


def create_refresh_token(subject: str, version: int = 0) -> str:
    return _make_token(
        subject,
        version=version,
        ttl=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        kind="refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    """Raises JWTError on invalid/expired token. Checks signature only —
    use load_session() to decide whether the session is still valid."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


# ── Session validation ───────────────────────────────────────────────────────

class AuthError(Exception):
    """Token is well-formed enough to reject with a reason, but not valid.

    `revoked_for` is set when the token's own jti is revoked — for refresh
    tokens that means a rotated token was replayed."""

    def __init__(self, detail: str, *, revoked_for: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.revoked_for = revoked_for


@dataclass(frozen=True)
class Session:
    username: str
    role: str
    jti: str
    expires_at: datetime
    token_version: int


_SESSION_SQL = """
    SELECT u.role, u.active, u.token_version,
           EXISTS (SELECT 1 FROM revoked_tokens r WHERE r.jti = $2) AS revoked
      FROM users u
     WHERE u.username = $1
"""


async def load_session(pool: asyncpg.Pool, token: str, *, kind: str = "access") -> Session:
    """Validate signature, token kind and the session's current DB state.
    Raises AuthError with a client-safe reason on any failure."""
    try:
        payload = decode_token(token)
    except JWTError:
        raise AuthError("Invalid or expired token") from None

    if payload.get("kind") != kind:
        raise AuthError(f"Expected a {kind} token")

    username, jti, exp = payload.get("sub"), payload.get("jti"), payload.get("exp")
    if not username or not jti or exp is None:
        raise AuthError("Malformed token")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SESSION_SQL, username, jti)

    if row is None:
        raise AuthError("User no longer exists")
    if not row["active"]:
        raise AuthError("Account disabled")
    if row["revoked"]:
        raise AuthError("Token has been revoked", revoked_for=username)
    # Tokens minted before token_version existed have no "ver" claim; they
    # map to version 0, which is the column default, so they stay valid
    # until the first event that bumps the version.
    if int(payload.get("ver", 0)) != row["token_version"]:
        raise AuthError("Session ended; please log in again")

    return Session(
        username=username,
        role=row["role"],
        jti=jti,
        expires_at=datetime.fromtimestamp(int(exp), tz=timezone.utc),
        token_version=row["token_version"],
    )


async def revoke_token(
    pool: asyncpg.Pool, jti: str, username: str, expires_at: datetime,
) -> bool:
    """Record a jti as revoked. Returns False if it already was, which lets
    refresh rotation detect two concurrent uses of the same token."""
    async with pool.acquire() as conn:
        # Opportunistic cleanup keeps the table at "tokens still alive" size
        # without a separate janitor task.
        await conn.execute("DELETE FROM revoked_tokens WHERE expires_at < NOW()")
        inserted = await conn.fetchval(
            """
            INSERT INTO revoked_tokens (jti, username, expires_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (jti) DO NOTHING
            RETURNING jti
            """,
            jti, username, expires_at,
        )
    return inserted is not None


async def end_all_sessions(conn: asyncpg.Connection, username: str) -> int | None:
    """Invalidate every token issued to `username`. Returns the new version,
    or None if the user doesn't exist."""
    version: int | None = await conn.fetchval(
        "UPDATE users SET token_version = token_version + 1 "
        "WHERE username = $1 RETURNING token_version",
        username,
    )
    return version
