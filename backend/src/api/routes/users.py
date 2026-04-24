from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status

from src.api.audit import log_action
from src.api.auth import hash_password
from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role

router = APIRouter(prefix="/users", tags=["users"])

_MIN_PASSWORD_LEN = 12


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


class RoleChange(BaseModel):
    role: str


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

    # Sorun 1: password policy backend'de de kontrol edilmeli (frontend bypass'ı önler)
    if len(body.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
        )

    if body.role not in ("viewer", "operator", "admin"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role must be viewer, operator, or admin",
        )

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
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="Username or email already exists",
                )
            raise
    await log_action(pool, user["username"], "user.create",
                     target_id=body.username, target_type="user",
                     details={"role": body.role})
    return UserOut(**dict(row))


@router.patch("/{username}/role")
async def change_role(username: str, body: RoleChange, pool: Pool, user: CurrentUser):
    require_role(Role.ADMIN, user["role"])
    role = body.role

    if role not in ("viewer", "operator", "admin"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid role"
        )

    # Sorun 2a: admin kendi rolünü düşüremesin
    if username == user["username"] and role != "admin":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own admin role. Ask another admin.",
        )

    # Sorun 2b: sistemdeki son admin silinemez
    async with pool.acquire() as conn:
        target = await conn.fetchrow(
            "SELECT role FROM users WHERE username = $1", username
        )
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

        if target["role"] == "admin" and role != "admin":
            admin_count = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = TRUE"
            )
            if admin_count <= 1:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="Cannot demote the last admin. Create another admin first.",
                )

        await conn.execute(
            "UPDATE users SET role = $1 WHERE username = $2", role, username
        )

    await log_action(pool, user["username"], "user.role_change",
                     target_id=username, target_type="user",
                     details={"new_role": role})
    return {"username": username, "role": role}
