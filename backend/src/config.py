import logging
import os
import secrets

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Known-weak JWT secret values that must never be used in production.
# In DEBUG=true mode these are silently replaced with a generated random secret.
_FORBIDDEN_JWT_SECRETS = frozenset({
    "",
    "secret",
    "changeme",
    "change-me",
    "test",
    "development",
    "dev-secret",
    "dev-secret-change-in-production",
    "dev-secret-change-in-production-min-32-chars",
})


class Settings(BaseSettings):
    # Credentials resolve as: explicit env var > file in CUBESAT_SECRETS_DIR
    # (one file per field, e.g. postgres_password) > default. docker-compose
    # generates random secrets into that directory on first start, so a
    # fresh install ships with no default passwords. Empty env vars are
    # ignored so compose can pass ${VAR:-} placeholders for overrides.
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        secrets_dir=os.environ.get("CUBESAT_SECRETS_DIR") or None,
    )

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "cubesat"
    postgres_password: str = "devpassword"
    postgres_db: str = "cubesat_c2"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str | None = None

    # NATS — the backend connects as the privileged "backend" user (see
    # deployment/nats/nats-server.conf).
    nats_url: str = "nats://localhost:4222"
    nats_user: str | None = None
    nats_password: str | None = None

    # Auth — default empty means "generate random in dev, fail in prod"
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # SatNOGS (optional — public endpoints work without token)
    satnogs_api_token: str | None = None

    # App
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Login brute-force protection. Independent of DEBUG on purpose: the
    # default docker-compose runs with DEBUG=true, and tying the two together
    # left every out-of-the-box install unprotected. Only E2E suites that log
    # in dozens of times per minute should turn this off.
    login_rate_limit_enabled: bool = True

    # Reverse proxies allowed to set X-Forwarded-For (IPs, CIDRs or hostnames
    # such as the compose service name "frontend"). Empty = never trust XFF.
    trusted_proxies: list[str] = []

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> "Settings":
        """
        Validate JWT secret AFTER all fields are populated so `self.debug` is
        reliably available. A field_validator on jwt_secret_key alone can't see
        `debug` because fields are validated in declaration order and debug
        comes later.
        """
        v = self.jwt_secret_key

        if v.strip().lower() in _FORBIDDEN_JWT_SECRETS:
            if self.debug:
                generated = secrets.token_urlsafe(32)
                logger.warning(
                    "DEV MODE: generated ephemeral JWT secret. "
                    "Tokens will invalidate on restart. "
                    "Set JWT_SECRET_KEY in .env for persistence."
                )
                # Replace in-place (model is mutable at this stage)
                object.__setattr__(self, "jwt_secret_key", generated)
                return self
            raise ValueError(
                "JWT_SECRET_KEY must be set in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

        if len(v) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least 32 characters (got {len(v)})."
            )

        return self

    @property
    def asyncpg_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
