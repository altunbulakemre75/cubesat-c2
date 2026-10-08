from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SIM_", env_file=".env", extra="ignore", env_ignore_empty=True,
    )

    nats_url: str = "nats://localhost:4222"
    # The simulator stands in for a ground station, so it connects as the
    # least-privileged "groundstation" NATS user (deployment/nats/).
    nats_user: str | None = None
    nats_password: str | None = None
    nats_password_file: str | None = None
    nats_inbox_prefix: str = "_INBOX_gs"
    satellites: str = "CUBESAT1,CUBESAT2"
    interval_s: float = 1.0
    fault_probability: float = 0.001
    safe_recovery_s: float = 120.0

    @property
    def satellite_ids(self) -> list[str]:
        return [s.strip() for s in self.satellites.split(",") if s.strip()]

    @property
    def resolved_nats_password(self) -> str | None:
        """Explicit password, else the docker-compose generated secret file."""
        if self.nats_password:
            return self.nats_password
        if self.nats_password_file:
            return Path(self.nats_password_file).read_text(encoding="utf-8").strip()
        return None
