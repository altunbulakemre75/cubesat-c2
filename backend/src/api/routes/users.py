from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, HTTPException, status

from src.api.auth import hash_password
from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    role: str = "viewer"


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    role: str
    active: bool


@router.get("", response_model=list[UserOut])
async def list_users(pool: Pool, user: CurrentUser):
    require_role(Role.ADMIN, user["role"])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text, username, email, role, active FROM users ORDER BY username"
        )
    return [UserOut(**dict(r)) for r in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, pool: Pool, user: CurrentUser):
    require_role(Role.ADMIN, user["role"])

    if body.role not in ("viewer", "operator", "admin"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Role must be viewer, operator, or admin")

    hashed = hash_password(body.password)
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO users (username, email, password_hash, role)
                VALUES ($1, $2, $3, $4)
                RETURNING id::text, username, email, role, active
                """,
                body.username, body.email, hashed, body.role,
            )
        except Exception as exc:
            if "unique" in str(exc).lower():
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    detail="Username or email already exists")
            raise
    return UserOut(**dict(row))


@router.patch("/{username}/role")
async def change_role(username: str, role: str, pool: Pool, user: CurrentUser):
    require_role(Role.ADMIN, user["role"])
    if role not in ("viewer", "operator", "admin"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Invalid role")
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET role = $1 WHERE username = $2", role, username
        )
    if result == "UPDATE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"username": username, "role": role}
