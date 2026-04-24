from fastapi import APIRouter, HTTPException, status

from src.api.auth import create_access_token, verify_password
from src.api.deps import Pool
from src.api.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, pool: Pool):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash, role, active FROM users WHERE username = $1",
            body.username,
        )

    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not row["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = create_access_token(subject=row["username"], role=row["role"])
    return TokenResponse(access_token=token)
