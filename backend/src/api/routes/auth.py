from datetime import datetime, timezone

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status
from jose import JWTError

from src.api.audit import log_action
from src.api.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_token_revoked,
    revoke_token,
    verify_password,
)
from src.api.deps import CurrentUser, Pool
from src.api.metrics import auth_login_total
from src.api.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_MIN_PASSWORD_LEN = 12


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, pool: Pool):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, password_hash, role, active, must_change_password
            FROM users WHERE username = $1
            """,
            body.username,
        )

    if not row or not verify_password(body.password, row["password_hash"]):
        auth_login_total.labels(result="failed").inc()
        await log_action(pool, body.username, "auth.login", result="failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not row["active"]:
        auth_login_total.labels(result="disabled").inc()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account disabled")

    access = create_access_token(subject=row["username"], role=row["role"])
    refresh = create_refresh_token(subject=row["username"], role=row["role"])
    auth_login_total.labels(result="ok").inc()
    await log_action(pool, row["username"], "auth.login", result="ok")
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        must_change_password=row["must_change_password"],
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(body: RefreshRequest):
    """Exchange a valid refresh token for a fresh access token (and a fresh
    refresh token). Old refresh token is revoked so it can't be reused."""
    try:
        payload = decode_token(body.refresh_token)
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid refresh token: {exc}")

    if payload.get("kind") != "refresh":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Token is not a refresh token",
        )

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing jti")

    if await is_token_revoked(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")

    # Rotate: revoke the old refresh token, issue a new pair
    exp = int(payload.get("exp", 0))
    remaining = max(1, exp - int(datetime.now(timezone.utc).timestamp()))
    await revoke_token(jti, remaining)

    username = payload["sub"]
    role = payload.get("role", "viewer")
    return TokenResponse(
        access_token=create_access_token(subject=username, role=role),
        refresh_token=create_refresh_token(subject=username, role=role),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(pool: Pool, user: CurrentUser):
    """Revoke the caller's current access-token jti. The frontend should also
    drop its in-memory refresh token; nothing prevents re-login afterwards."""
    jti = user.get("jti")
    exp = user.get("exp")
    if jti and exp:
        remaining = max(1, int(exp) - int(datetime.now(timezone.utc).timestamp()))
        await revoke_token(jti, remaining)
    await log_action(pool, user["username"], "auth.logout")


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    pool: Pool,
    user: CurrentUser,
):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE username = $1",
            user["username"],
        )

    if not row or not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

    if len(body.new_password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"New password must be at least {_MIN_PASSWORD_LEN} characters",
        )

    if body.new_password == body.old_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must differ from the old password",
        )

    new_hash = hash_password(body.new_password)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
               SET password_hash = $1, must_change_password = FALSE
             WHERE username = $2
            """,
            new_hash, user["username"],
        )

    await log_action(pool, user["username"], "auth.password_change")
