from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status

from src.api.audit import log_action
from src.api.auth import create_access_token, hash_password, verify_password
from src.api.deps import CurrentUser, Pool
from src.api.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_MIN_PASSWORD_LEN = 12


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


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
        await log_action(pool, body.username, "auth.login", result="failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not row["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = create_access_token(subject=row["username"], role=row["role"])
    await log_action(pool, row["username"], "auth.login", result="ok")
    return TokenResponse(
        access_token=token,
        must_change_password=row["must_change_password"],
    )


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
