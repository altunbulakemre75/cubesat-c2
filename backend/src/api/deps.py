"""FastAPI dependency injectors."""

from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from src.api.auth import decode_token, is_token_revoked
from src.storage.db import get_pool

_bearer = HTTPBearer()


async def db_pool() -> asyncpg.Pool:
    return await get_pool()


Pool = Annotated[asyncpg.Pool, Depends(db_pool)]


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    try:
        payload = decode_token(creds.credentials)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    # Refresh tokens must not be accepted on regular API endpoints
    if payload.get("kind") == "refresh":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Refresh tokens cannot be used on protected endpoints",
        )

    jti = payload.get("jti")
    if jti and await is_token_revoked(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    return {
        "username": payload["sub"],
        "role": payload.get("role", "viewer"),
        "jti": jti,
        "exp": payload.get("exp"),
    }


CurrentUser = Annotated[dict, Depends(current_user)]
