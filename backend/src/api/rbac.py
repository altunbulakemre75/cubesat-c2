"""Role-based access control."""

from enum import Enum
from fastapi import HTTPException, status


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


_ROLE_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


def require_role(minimum: Role, user_role: str) -> None:
    """Raise 403 if user_role is below minimum required role."""
    try:
        rank = _ROLE_RANK[Role(user_role)]
    except (ValueError, KeyError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Unknown role: {user_role}")

    if rank < _ROLE_RANK[minimum]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user_role}' insufficient. Required: '{minimum.value}'",
        )
