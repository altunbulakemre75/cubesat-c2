"""
FDIR (Fault Detection, Isolation and Recovery) monitor.

Periodically assesses each active satellite's health from the database
and raises an alert (persisted in fdir_alerts, published on
events.fdir.<sat>) when something is wrong.

Design notes:
- FDIR raises alerts; it does NOT command the satellite into safe mode.
  Autonomous commanding over a lossy RF link could cause unrecoverable
  states if the trigger was a false positive — the operator decides.
- The latest telemetry is read from the database. v0.1.0 read a Redis key
  with a 1 h TTL, so after an hour every satellite reported "No telemetry
  received since startup".
- Staleness is pass-aware: a LEO satellite out of view is not a fault, a
  pass over one of our own stations that produced no telemetry is.
  Satellites without pass predictions (continuous link, e.g. the
  simulator) use a plain age threshold.
- One alert per fault: an open (unacknowledged) alert suppresses new ones,
  and after an ack the fault only re-alerts on newer evidence. The state
  lives in the database, so restarts don't re-alert either.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Thresholds — defaults assume single-cell Li-ion (3.0–4.2 V), matching the
# simulator and BatteryBar UI default. For 2S/3S/multi-cell missions these
# belong in per-mission configuration (see docs/ONERILER.md).
BATTERY_CRITICAL_V = 3.3     # 1S Li-ion typical safe-mode threshold
TEMP_OBCS_CRITICAL_C = 65.0
TEMP_EPS_CRITICAL_C = 55.0
STALE_TELEMETRY_MINUTES = 10  # continuous-link satellites only
# Telemetry from a pass may reach the database a little after LOS.
PASS_GRACE = timedelta(minutes=5)


@dataclass(frozen=True)
class Observation:
    time: datetime
    battery_voltage_v: float | None
    temperature_obcs_c: float | None
    temperature_eps_c: float | None


@dataclass(frozen=True)
class LastPass:
    station: str
    aos: datetime
    los: datetime


@dataclass
class Assessment:
    warnings: list[str] = field(default_factory=list)
    # The newest data point the warnings are based on; an alert is only
    # re-raised for evidence newer than the previous alert.
    evidence_at: datetime | None = None

    def add(self, warning: str, evidence_at: datetime) -> None:
        self.warnings.append(warning)
        if self.evidence_at is None or evidence_at > self.evidence_at:
            self.evidence_at = evidence_at


def assess(
    latest: Observation | None,
    last_pass: LastPass | None,
    has_passes: bool,
    now: datetime,
) -> Assessment:
    """Pure health assessment for one satellite."""
    result = Assessment()

    def _missed(p: LastPass) -> None:
        result.add(
            f"No telemetry during the pass over {p.station} "
            f"({p.aos:%H:%M}–{p.los:%H:%M} UTC)",
            p.los,
        )

    if latest is None:
        if last_pass is not None:
            _missed(last_pass)
        return result  # never heard and nothing expected yet: nothing to judge

    if has_passes:
        if last_pass is not None and latest.time < last_pass.aos:
            _missed(last_pass)
    else:
        age = now - latest.time
        if age > timedelta(minutes=STALE_TELEMETRY_MINUTES):
            result.add(f"Telemetry stale for {int(age.total_seconds() // 60)}m", latest.time)

    # Distinguish "field missing" from "value out of range": hiding a missing
    # value behind a sentinel would report broken telemetry as healthy.
    checks = (
        ("battery_voltage_v", latest.battery_voltage_v,
         lambda v: v < BATTERY_CRITICAL_V,
         lambda v: f"Battery critical: {v:.2f}V < {BATTERY_CRITICAL_V}V"),
        ("temperature_obcs_c", latest.temperature_obcs_c,
         lambda v: v > TEMP_OBCS_CRITICAL_C,
         lambda v: f"OBC temperature critical: {v:.1f}°C"),
        ("temperature_eps_c", latest.temperature_eps_c,
         lambda v: v > TEMP_EPS_CRITICAL_C,
         lambda v: f"EPS temperature critical: {v:.1f}°C"),
    )
    for name, value, is_bad, describe in checks:
        if value is None:
            result.add(f"Missing {name} in telemetry", latest.time)
        elif is_bad(value):
            result.add(describe(value), latest.time)
    return result


_LATEST_SQL = """
    SELECT time, battery_voltage_v, temperature_obcs_c, temperature_eps_c
      FROM telemetry
     WHERE satellite_id = $1
     ORDER BY time DESC
     LIMIT 1
"""

# Passes over our own (uplink-capable) stations are when we expect to hear
# the satellite; SatNOGS stations may or may not have been observing.
_LAST_PASS_SQL = """
    SELECT g.name, p.aos, p.los
      FROM pass_schedule p
      JOIN ground_stations g ON g.id = p.station_id
     WHERE p.satellite_id = $1 AND g.uplink_capable AND g.active
       AND p.los < NOW() - $2::interval
     ORDER BY p.los DESC
     LIMIT 1
"""

_HAS_PASSES_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM pass_schedule p
          JOIN ground_stations g ON g.id = p.station_id
         WHERE p.satellite_id = $1 AND g.uplink_capable AND g.active
    )
"""

_ALREADY_ALERTED_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM fdir_alerts
         WHERE satellite_id = $1
           AND (acknowledged = FALSE OR triggered_at >= $2)
    )
"""


class FDIRMonitor:
    def __init__(self, pool: asyncpg.Pool, js: Any, check_interval_s: float = 60.0) -> None:
        self._pool = pool
        self._js = js
        self._check_interval = check_interval_s

    async def run(self) -> None:
        logger.info("FDIR monitor started (check every %.0fs)", self._check_interval)
        while True:
            await asyncio.sleep(self._check_interval)
            try:
                await self._check_all_satellites()
            except Exception as exc:  # noqa: BLE001
                logger.error("FDIR check cycle error: %s", exc)

    async def _check_all_satellites(self) -> None:
        rows = await self._pool.fetch("SELECT id FROM satellites WHERE active = TRUE")
        for row in rows:
            try:
                await self._check_satellite(row["id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("FDIR check failed for %s: %s", row["id"], exc)

    async def _check_satellite(self, satellite_id: str) -> None:
        async with self._pool.acquire() as conn:
            latest = await conn.fetchrow(_LATEST_SQL, satellite_id)
            last_pass = await conn.fetchrow(_LAST_PASS_SQL, satellite_id, PASS_GRACE)
            has_passes = bool(await conn.fetchval(_HAS_PASSES_SQL, satellite_id))

        result = assess(
            Observation(
                time=latest["time"],
                battery_voltage_v=latest["battery_voltage_v"],
                temperature_obcs_c=latest["temperature_obcs_c"],
                temperature_eps_c=latest["temperature_eps_c"],
            ) if latest else None,
            LastPass(last_pass["name"], last_pass["aos"], last_pass["los"]) if last_pass else None,
            has_passes,
            datetime.now(timezone.utc),
        )
        if not result.warnings:
            return

        assert result.evidence_at is not None
        if await self._pool.fetchval(_ALREADY_ALERTED_SQL, satellite_id, result.evidence_at):
            return

        logger.warning("FDIR | sat=%s warnings=%s", satellite_id, result.warnings)
        await self._raise_alert(satellite_id, "; ".join(result.warnings))

    async def _raise_alert(self, satellite_id: str, reason: str) -> None:
        now = datetime.now(timezone.utc)

        # Persist FIRST so the alert survives a restart and operators can ack
        # it after the WS event is gone. The DB id is reused as the NATS event
        # id so frontend dedupe works end-to-end.
        try:
            alert_id = str(await self._pool.fetchval(
                """
                INSERT INTO fdir_alerts (satellite_id, reason, severity, triggered_at)
                VALUES ($1, $2, 'critical', $3)
                RETURNING id
                """,
                satellite_id, reason, now,
            ))
        except Exception as exc:  # noqa: BLE001
            # Don't drop the alert if persistence fails — fall back to a
            # client-side uuid so the WS event still reaches operators.
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
        try:
            await self._js.publish(f"events.fdir.{satellite_id}", json.dumps(payload).encode())
            logger.warning("FDIR alert published | sat=%s reason=%s", satellite_id, reason)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to publish FDIR event: %s", exc)
