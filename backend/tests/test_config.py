"""
Settings validator tests — especially JWT secret policy.

Regression: an earlier version used @field_validator on jwt_secret_key
with info.data.get("debug"), but Pydantic v2 validates fields in declaration
order. Since `debug` is declared AFTER `jwt_secret_key`, info.data didn't
contain debug — the validator treated every run as production.
The fix uses @model_validator(mode="after").
"""

import importlib

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


# ── Secrets from files (docker-compose secrets volume) ───────────────────────

def _secrets(tmp_path, **values: str):
    for name, value in values.items():
        (tmp_path / name).write_text(value, encoding="utf-8")
    return tmp_path


def test_credentials_are_read_from_the_secrets_dir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """docker-compose generates random credentials into a shared volume on
    first start, so a fresh install has no default passwords at all."""
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("CUBESAT_SECRETS_DIR", str(_secrets(
        tmp_path,
        jwt_secret_key="j" * 43,
        postgres_password="pg-secret",
        redis_password="redis-secret",
        nats_password="nats-secret",
    )))
    s = _reload_settings()
    assert s.jwt_secret_key == "j" * 43
    assert s.postgres_password == "pg-secret"
    assert s.redis_password == "redis-secret"
    assert s.nats_password == "nats-secret"


def test_empty_env_var_does_not_mask_a_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # compose passes POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-} so an operator
    # can override; unset must fall through to the generated secret.
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    monkeypatch.setenv("CUBESAT_SECRETS_DIR", str(_secrets(tmp_path, postgres_password="from-file")))
    s = _reload_settings()
    assert s.postgres_password == "from-file"


def test_explicit_env_var_overrides_the_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Upgrade path: an existing database volume keeps its old password.
    monkeypatch.setenv("POSTGRES_PASSWORD", "legacy-password")
    monkeypatch.setenv("CUBESAT_SECRETS_DIR", str(_secrets(tmp_path, postgres_password="from-file")))
    s = _reload_settings()
    assert s.postgres_password == "legacy-password"


def test_restore_defaults_after_secrets_tests(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CUBESAT_SECRETS_DIR", raising=False)
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-deterministic-for-pytest-only-32chars-min")
    s = _reload_settings()
    assert s.redis_password is None
