"""
Pytest conftest — sets environment so tests can import src.config
without a real .env file. Must run before any src.* import.
"""

import os

# Dev mode auto-generates an ephemeral JWT secret if none is set.
os.environ.setdefault("DEBUG", "true")

# Known-good 32+ char secret for deterministic token tests.
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-deterministic-for-pytest-only-32chars-min",
)
