from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.api.audit import log_action
from src.api.auth import (
    WS_TICKET_TTL_S,
    AuthError,
    create_access_token,
    create_refresh_token,
    create_ws_ticket,
    end_all_sessions,
    hash_password,
    load_session,
    revoke_token,
    verify_password,
)
from src.api.bootstrap import remove_bootstrap_file
from src.api.deps import CurrentUser, Pool
from src.api.metrics import auth_login_total
from src.api.rate_limit import check_login_rate, reset_login_rate
from src.api.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_MIN_PASSWORD_LEN = 12


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class WsTicketResponse(BaseModel):
    ticket: str
    expires_in: int


class LogoutRequest(BaseModel):
    # Optional so old clients that POST an empty body keep working; new
    # clients send it so logout also kills the long-lived refresh token.
    refresh_token: str | None = None


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, pool: Pool) -> TokenResponse:
    # Rate limit BEFORE the bcrypt verify — otherwise an attacker can
    # still burn server CPU on bcrypt rounds for every probe.
    if not await check_login_rate(request, body.username):
        auth_login_total.labels(result="rate_limited").inc()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts; try again in a few minutes",
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, username, password_hash, role, active,
                   must_change_password, token_version
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

    # Successful login — clear the rate-limit counter so a single typo
    # doesn't cost the user their next four attempts.
    await reset_login_rate(request, row["username"])

    access = create_access_token(row["username"], row["token_version"])
    refresh = create_refresh_token(row["username"], row["token_version"])
    auth_login_total.labels(result="ok").inc()
    await log_action(pool, row["username"], "auth.login", result="ok")
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        must_change_password=row["must_change_password"],
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(body: RefreshRequest, pool: Pool) -> TokenResponse:
    """Exchange a refresh token for a new token pair. The old refresh token
    is revoked (rotation); presenting it again means a copy leaked, so the
    user's whole session family is ended."""
    try:
        session = await load_session(pool, body.refresh_token, kind="refresh")
    except AuthError as exc:
        if exc.revoked_for:
            await _end_sessions_after_replay(pool, exc.revoked_for)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from None

    # Claim the token atomically: if a concurrent request already rotated
    # it, this is a replay too.
    if not await revoke_token(pool, session.jti, session.username, session.expires_at):
        await _end_sessions_after_replay(pool, session.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    return TokenResponse(
        access_token=create_access_token(session.username, session.token_version),
        refresh_token=create_refresh_token(session.username, session.token_version),
    )


async def _end_sessions_after_replay(pool: Pool, username: str) -> None:
    async with pool.acquire() as conn:
        await end_all_sessions(conn, username)
    await log_action(pool, username, "auth.refresh_replay", result="denied")


@router.post("/ws-ticket", response_model=WsTicketResponse)
async def ws_ticket(user: CurrentUser) -> WsTicketResponse:
    """Short-lived, single-use credential for opening a WebSocket. The
    socket it opens is bound to (and closed with) the caller's session."""
    return WsTicketResponse(
        ticket=create_ws_ticket(
            user["username"], user["token_version"], user["jti"], user["exp"],
        ),
        expires_in=WS_TICKET_TTL_S,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(pool: Pool, user: CurrentUser, body: LogoutRequest | None = None) -> None:
    """Revoke the caller's access token and, if supplied, the refresh token
    issued alongside it. Re-login afterwards is unaffected."""
    await revoke_token(pool, user["jti"], user["username"], user["expires_at"])

    if body and body.refresh_token:
        try:
            refresh = await load_session(pool, body.refresh_token, kind="refresh")
        except AuthError:
            refresh = None  # already dead — nothing to do
        if refresh and refresh.username == user["username"]:
            await revoke_token(pool, refresh.jti, refresh.username, refresh.expires_at)

    await log_action(pool, user["username"], "auth.logout")


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    body: ChangePasswordRequest,
    pool: Pool,
    user: CurrentUser,
) -> TokenResponse:
    """Change the caller's password. Every existing session (including the
    one making this call) ends; the response carries a fresh token pair so
    the current client stays logged in."""
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
        version = await conn.fetchval(
            """
            UPDATE users
               SET password_hash = $1,
                   must_change_password = FALSE,
                   token_version = token_version + 1
             WHERE username = $2
            RETURNING token_version
            """,
            new_hash, user["username"],
        )

    await log_action(pool, user["username"], "auth.password_change")
    if user["username"] == "admin":
        remove_bootstrap_file()
    return TokenResponse(
        access_token=create_access_token(user["username"], version),
        refresh_token=create_refresh_token(user["username"], version),
    )
