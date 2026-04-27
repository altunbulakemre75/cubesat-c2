"""
FDIR (Fault Detection, Isolation and Recovery) monitor.

Watches telemetry health and triggers safe mode when anomalies persist.
Runs as a background asyncio task alongside the telemetry writer.
"""

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

import asyncpg

from src.storage import redis_client

logger = logging.getLogger(__name__)

# Thresholds — defaults assume single-cell Li-ion (3.0–4.2 V), matching the
# simulator and BatteryBar UI default. For 2S/3S/multi-cell missions, override
# via mission-specific config in a follow-up. Documented in docs/MIMARI.md.
BATTERY_CRITICAL_V = 3.3     # 1S Li-ion typical safe-mode threshold
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

    # LRU cap so deleted/decommissioned satellites don't accumulate state forever
    _MAX_STATES = 1000

    def __init__(self, pool: asyncpg.Pool, js, check_interval_s: float = 60.0) -> None:
        self._pool = pool
        self._js = js
        self._check_interval = check_interval_s
        # OrderedDict + LRU eviction (same pattern as AnomalyDetector)
        self._states: OrderedDict[str, FDIRState] = OrderedDict()

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

        active_ids = {row["id"] for row in rows}

        # Evict states for satellites that are no longer active
        for sat_id in list(self._states):
            if sat_id not in active_ids:
                del self._states[sat_id]

        for row in rows:
            sat_id = row["id"]
            try:
                await self._check_satellite(sat_id)
            except Exception as exc:
                logger.warning("FDIR check failed for %s: %s", sat_id, exc)

    async def _check_satellite(self, satellite_id: str) -> None:
        last = await redis_client.get_last_telemetry(satellite_id)

        if satellite_id in self._states:
            self._states.move_to_end(satellite_id)  # bump freshness
        else:
            self._states[satellite_id] = FDIRState(satellite_id)
            while len(self._states) > self._MAX_STATES:
                self._states.popitem(last=False)
        state = self._states[satellite_id]
        warnings: list[str] = []

        # Defensive: Redis decoded value should be a dict; if it's anything
        # else (cache corruption, wrong serializer in a sibling service),
        # surface a warning instead of crashing.
        if last is not None and not isinstance(last, dict):
            warnings.append(f"Cached telemetry has wrong type: {type(last).__name__}")
            last = None

        if last is None:
            warnings.append("No telemetry received since startup")
        else:
            try:
                ts = datetime.fromisoformat(last["timestamp"])
                age = datetime.now(timezone.utc) - ts
                if age > timedelta(minutes=STALE_TELEMETRY_MINUTES):
                    warnings.append(f"Telemetry stale for {age.seconds // 60}m")
            except (KeyError, ValueError) as exc:
                warnings.append(f"Bad timestamp in cached telemetry: {exc}")

            # Distinguish "field missing" from "value below threshold". Hiding
            # missing values behind a sentinel (e.g. 99 V) silently masks
            # broken telemetry as healthy.
            bat = last.get("battery_voltage_v")
            if bat is None:
                warnings.append("Missing battery_voltage_v in telemetry")
            elif bat < BATTERY_CRITICAL_V:
                warnings.append(f"Battery critical: {bat:.2f}V < {BATTERY_CRITICAL_V}V")

            t_obcs = last.get("temperature_obcs_c")
            if t_obcs is None:
                warnings.append("Missing temperature_obcs_c")
            elif t_obcs > TEMP_OBCS_CRITICAL_C:
                warnings.append(f"OBC temperature critical: {t_obcs:.1f}°C")

            t_eps = last.get("temperature_eps_c")
            if t_eps is None:
                warnings.append("Missing temperature_eps_c")
            elif t_eps > TEMP_EPS_CRITICAL_C:
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
        # Design note: FDIR publishes an alert event to NATS (events.fdir.*) but does NOT
        # automatically send a safe mode command to the satellite. This is intentional —
        # the actual mode_change command requires operator approval via POST /commands.
        # Rationale: autonomous safe mode commanding over a lossy RF link could cause
        # unrecoverable states if the trigger was a false positive (e.g. stale telemetry
        # due to LOS rather than a real fault).
        import json
        now = datetime.now(timezone.utc)

        # Persist FIRST so the alert survives a backend restart and operators
        # can ack it after the WS event is gone. The DB-generated UUID is
        # reused as the NATS event id so frontend dedupe works end-to-end.
        alert_id: str | None = None
        try:
            async with self._pool.acquire() as conn:
                alert_id = await conn.fetchval(
                    """
                    INSERT INTO fdir_alerts (satellite_id, reason, severity, triggered_at)
                    VALUES ($1, $2, 'critical', $3)
                    RETURNING id
                    """,
                    satellite_id, reason, now,
                )
                alert_id = str(alert_id) if alert_id is not None else None
        except Exception as exc:  # noqa: BLE001
            # Don't drop the alert if persistence fails — fall back to a
            # client-side uuid so the WS event still reaches operators.
            import uuid
            alert_id = str(uuid.uuid4())
            logger.error("FDIR alert persist failed | sat=%s: %s", satellite_id, exc)

        iso_now = now.isoformat()
        payload = {
            "id": alert_id,
            "type": "fdir_alert",
            "satellite_id": satellite_id,
            "message": f"FDIR alert: {reason}",
            "timestamp": iso_now,
            "severity": "critical",
            "reason": reason,
            "triggered_at": iso_now,
        }
        subject = f"events.fdir.{satellite_id}"
        try:
            await self._js.publish(subject, json.dumps(payload).encode())
            logger.warning("FDIR safe mode event published | sat=%s reason=%s", satellite_id, reason)
        except Exception as exc:
            logger.error("Failed to publish FDIR event: %s", exc)
