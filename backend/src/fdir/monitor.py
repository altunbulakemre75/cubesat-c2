"""
FDIR (Fault Detection, Isolation and Recovery) monitor.

Watches telemetry health and triggers safe mode when anomalies persist.
Runs as a background asyncio task alongside the telemetry writer.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

import asyncpg

from src.ingestion.models import SatelliteMode
from src.storage import redis_client

logger = logging.getLogger(__name__)

# Thresholds — override via config if needed
BATTERY_CRITICAL_V = 3.5     # below this → FDIR warning
TEMP_OBCS_CRITICAL_C = 65.0  # above this → FDIR warning
TEMP_EPS_CRITICAL_C = 55.0
STALE_TELEMETRY_MINUTES = 10  # no telemetry for this long → FDIR trigger


@dataclass
class FDIRState:
    satellite_id: str
    warnings: list[str] = field(default_factory=list)
    safe_mode_triggered: bool = False
    last_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FDIRMonitor:
    """
    Periodically checks each satellite's last telemetry and triggers safe mode events.

    Safe mode NATS event format:
      subject: events.fdir.{satellite_id}
      payload: {"satellite_id": ..., "reason": ..., "triggered_at": ...}
    """

    def __init__(self, pool: asyncpg.Pool, js, check_interval_s: float = 60.0) -> None:
        self._pool = pool
        self._js = js
        self._check_interval = check_interval_s
        self._states: dict[str, FDIRState] = {}

    async def run(self) -> None:
        logger.info("FDIR monitor started (check every %.0fs)", self._check_interval)
        while True:
            await asyncio.sleep(self._check_interval)
            try:
                await self._check_all_satellites()
            except Exception as exc:
                logger.error("FDIR check cycle error: %s", exc)

    async def _check_all_satellites(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM satellites WHERE active = TRUE")

        for row in rows:
            sat_id = row["id"]
            try:
                await self._check_satellite(sat_id)
            except Exception as exc:
                logger.warning("FDIR check failed for %s: %s", sat_id, exc)

    async def _check_satellite(self, satellite_id: str) -> None:
        last = await redis_client.get_last_telemetry(satellite_id)
        state = self._states.setdefault(satellite_id, FDIRState(satellite_id))
        warnings: list[str] = []

        if last is None:
            warnings.append("No telemetry received since startup")
        else:
            ts = datetime.fromisoformat(last["timestamp"])
            age = datetime.now(timezone.utc) - ts
            if age > timedelta(minutes=STALE_TELEMETRY_MINUTES):
                warnings.append(f"Telemetry stale for {age.seconds // 60}m")

            bat = last.get("battery_voltage_v", 99)
            if bat < BATTERY_CRITICAL_V:
                warnings.append(f"Battery critical: {bat:.2f}V < {BATTERY_CRITICAL_V}V")

            t_obcs = last.get("temperature_obcs_c", 0)
            if t_obcs > TEMP_OBCS_CRITICAL_C:
                warnings.append(f"OBC temperature critical: {t_obcs:.1f}°C")

            t_eps = last.get("temperature_eps_c", 0)
            if t_eps > TEMP_EPS_CRITICAL_C:
                warnings.append(f"EPS temperature critical: {t_eps:.1f}°C")

        state.warnings = warnings
        state.last_check = datetime.now(timezone.utc)

        if warnings and not state.safe_mode_triggered:
            logger.warning("FDIR | sat=%s warnings=%s", satellite_id, warnings)
            await self._trigger_safe_mode(satellite_id, "; ".join(warnings))
            state.safe_mode_triggered = True
        elif not warnings and state.safe_mode_triggered:
            state.safe_mode_triggered = False
            logger.info("FDIR | sat=%s recovered", satellite_id)

    async def _trigger_safe_mode(self, satellite_id: str, reason: str) -> None:
        import json
        payload = {
            "satellite_id": satellite_id,
            "reason": reason,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        }
        subject = f"events.fdir.{satellite_id}"
        try:
            await self._js.publish(subject, json.dumps(payload).encode())
            logger.warning("FDIR safe mode event published | sat=%s reason=%s", satellite_id, reason)
        except Exception as exc:
            logger.error("Failed to publish FDIR event: %s", exc)
