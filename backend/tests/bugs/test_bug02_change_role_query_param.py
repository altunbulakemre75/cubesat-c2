"""
Bug #2: change_role accepted `role` as a QUERY parameter, not request body.
Fix: introduce RoleChange Pydantic model and take it as JSON body.
"""

from inspect import signature
from pydantic import BaseModel

from src.api.routes.users import change_role, RoleChange


def test_change_role_uses_body_model():
    """The endpoint must accept a Pydantic body model — not a bare str query param."""
    params = signature(change_role).parameters
    # The bare `role: str` query param must be gone
    assert "role" not in params, (
        "Bare `role: str` parameter is still present — must be wrapped in a body model"
    )
    # A `body` parameter of type RoleChange must exist
    assert "body" in params, "change_role must accept a `body` parameter"
    assert params["body"].annotation is RoleChange, (
        "body parameter must be annotated with RoleChange"
    )


def test_role_change_model_has_role_field():
    """RoleChange must be a Pydantic model with a `role` string field."""
    assert issubclass(RoleChange, BaseModel)
    fields = RoleChange.model_fields
    assert "role" in fields
    assert fields["role"].annotation is str
