"""FastAPI dependency injectors."""

from typing import Annotated, Any

import asyncpg
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.auth import AuthError, load_session
from src.storage.db import get_pool

_bearer = HTTPBearer()


async def db_pool() -> asyncpg.Pool:
    return await get_pool()


Pool = Annotated[asyncpg.Pool, Depends(db_pool)]


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    pool: Pool,
) -> dict[str, Any]:
    """Resolve the caller from an access token. Role comes from the DB, so
    a demotion or deactivation applies to the very next request."""
    try:
        session = await load_session(pool, creds.credentials, kind="access")
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from None

    return {
        "username": session.username,
        "role": session.role,
        "jti": session.jti,
        "exp": int(session.expires_at.timestamp()),
        "expires_at": session.expires_at,
    }


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]
