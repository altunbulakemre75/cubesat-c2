import logging
import secrets

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Known-weak JWT secret values that must never be used (even in dev mode
# they will be replaced by a generated ephemeral secret).
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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "cubesat"
    postgres_password: str = "devpassword"
    postgres_db: str = "cubesat_c2"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # NATS
    nats_url: str = "nats://localhost:4222"

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

    @field_validator("jwt_secret_key")
    @classmethod
    def _validate_jwt_secret(cls, v: str, info) -> str:
        """
        Reject weak secrets. In DEBUG mode generate an ephemeral random
        secret (tokens invalidate on restart); in production hard-fail.
        """
        debug = bool(info.data.get("debug", False))

        if v.strip().lower() in _FORBIDDEN_JWT_SECRETS:
            if debug:
                generated = secrets.token_urlsafe(32)
                logger.warning(
                    "DEV MODE: generated ephemeral JWT secret. "
                    "Tokens will invalidate on restart. "
                    "Set JWT_SECRET_KEY in .env for persistence."
                )
                return generated
            raise ValueError(
                "JWT_SECRET_KEY must be set in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

        if len(v) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least 32 characters (got {len(v)})."
            )

        return v

    @property
    def asyncpg_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
