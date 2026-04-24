"""
Settings validator tests — especially JWT secret policy.

Regression: an earlier version used @field_validator on jwt_secret_key
with info.data.get("debug"), but Pydantic v2 validates fields in declaration
order. Since `debug` is declared AFTER `jwt_secret_key`, info.data didn't
contain debug — the validator treated every run as production.
The fix uses @model_validator(mode="after").
"""

import importlib
import os

import pytest


def _reload_settings():
    """Reload src.config so it re-reads environment variables."""
    from src import config as config_module
    importlib.reload(config_module)
    return config_module.Settings()


def test_prod_mode_rejects_empty_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    with pytest.raises(ValueError, match="must be set in production"):
        _reload_settings()


def test_prod_mode_rejects_known_weak_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret-change-in-production")
    with pytest.raises(ValueError, match="must be set in production"):
        _reload_settings()


def test_prod_mode_rejects_short_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("JWT_SECRET_KEY", "a" * 16)
    with pytest.raises(ValueError, match="at least 32 characters"):
        _reload_settings()


def test_prod_mode_accepts_strong_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    s = _reload_settings()
    assert s.jwt_secret_key == "x" * 40


def test_dev_mode_generates_secret_when_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "")
    s = _reload_settings()
    # Empty string should have been replaced with a 32+ char random secret
    assert s.jwt_secret_key != ""
    assert len(s.jwt_secret_key) >= 32


def test_dev_mode_generates_secret_when_weak(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret")
    s = _reload_settings()
    assert s.jwt_secret_key != "dev-secret"
    assert len(s.jwt_secret_key) >= 32


def test_dev_mode_keeps_strong_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "y" * 40)
    s = _reload_settings()
    assert s.jwt_secret_key == "y" * 40


def test_restore_defaults(monkeypatch: pytest.MonkeyPatch):
    """Ensure other tests still see the default conftest settings."""
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-deterministic-for-pytest-only-32chars-min")
    s = _reload_settings()
    assert len(s.jwt_secret_key) >= 32
