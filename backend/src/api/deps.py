"""FastAPI dependency injectors."""

from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from src.api.auth import decode_token
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
    return {"username": payload["sub"], "role": payload.get("role", "viewer")}


CurrentUser = Annotated[dict, Depends(current_user)]
