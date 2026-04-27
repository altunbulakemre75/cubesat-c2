"""
Pytest conftest — sets environment so tests can import src.config
without a real .env file. Must run before any src.* import.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

# Dev mode auto-generates an ephemeral JWT secret if none is set.
os.environ.setdefault("DEBUG", "true")

# Known-good 32+ char secret for deterministic token tests.
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-deterministic-for-pytest-only-32chars-min",
)


@pytest.fixture(autouse=True)
def _no_redis_revocation_check():
    """Stub out Redis-backed token revocation so tests don't pay the
    5-second connect timeout per request when no real Redis is reachable.
    Tests that need to assert revocation behaviour should override this."""
    with patch("src.api.auth.is_token_revoked", new=AsyncMock(return_value=False)):
        yield
