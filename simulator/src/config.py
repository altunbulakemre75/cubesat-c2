from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    nats_url: str = "nats://localhost:4222"
    satellites: str = "CUBESAT1,CUBESAT2"
    interval_s: float = 1.0
    fault_probability: float = 0.001
    safe_recovery_s: float = 120.0

    @property
    def satellite_ids(self) -> list[str]:
        return [s.strip() for s in self.satellites.split(",") if s.strip()]
