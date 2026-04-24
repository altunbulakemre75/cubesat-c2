"""
Telemetry writer service.

Subscribes to telemetry.canonical.* on NATS JetStream, parses CanonicalTelemetry,
inserts into TimescaleDB, and caches last-known values in Redis.

Satellites are auto-registered on first telemetry receipt.
"""

import asyncio
import json
import logging

import asyncpg
from nats.aio.msg import Msg
from nats.js import JetStreamContext

from src.api.metrics import telemetry_ingested_total
from src.ingestion.models import CanonicalTelemetry
from src.storage import redis_client

logger = logging.getLogger(__name__)

_CANONICAL_SUBJECT = "telemetry.canonical.*"
_DURABLE_NAME = "telemetry-writer"


class TelemetryWriter:
    def __init__(self, js: JetStreamContext, pool: asyncpg.Pool) -> None:
        self._js = js
        self._pool = pool
        self._written: int = 0
        self._errors: int = 0

    @property
    def stats(self) -> dict[str, int]:
        return {"written": self._written, "errors": self._errors}

    async def run(self) -> None:
        await self._js.subscribe(
            _CANONICAL_SUBJECT,
            durable=_DURABLE_NAME,
            cb=self._handle,
            manual_ack=True,
        )
        logger.info("Telemetry writer listening on %s", _CANONICAL_SUBJECT)
        try:
            await asyncio.sleep(float("inf"))
        except asyncio.CancelledError:
            pass

    async def _handle(self, msg: Msg) -> None:
        try:
            data = json.loads(msg.data.decode())
            telemetry = CanonicalTelemetry.model_validate(data)
        except Exception as exc:
            self._errors += 1
            logger.warning("Parse error | subject=%s: %s", msg.subject, exc)
            await msg.ack()
            return

        try:
            await self._write_to_db(telemetry)
            await self._update_cache(telemetry)
            self._written += 1
            telemetry_ingested_total.labels(
                satellite_id=telemetry.satellite_id,
                source=telemetry.source,
            ).inc()
            logger.debug(
                "Written | sat=%s seq=%d mode=%s",
                telemetry.satellite_id,
                telemetry.sequence,
                telemetry.params.mode.value,
            )
        except Exception as exc:
            self._errors += 1
            logger.error("Write failed | sat=%s: %s", telemetry.satellite_id, exc)
        finally:
            await msg.ack()

    async def _write_to_db(self, t: CanonicalTelemetry) -> None:
        async with self._pool.acquire() as conn:
            # Auto-register satellite if unknown
            await conn.execute(
                """
                INSERT INTO satellites (id, name) VALUES ($1, $1)
                ON CONFLICT (id) DO NOTHING
                """,
                t.satellite_id,
            )
            await conn.execute(
                """
                INSERT INTO telemetry (
                    time, satellite_id, source, sequence,
                    battery_voltage_v, temperature_obcs_c, temperature_eps_c,
                    solar_power_w, rssi_dbm, uptime_s, mode
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                t.timestamp,
                t.satellite_id,
                t.source,
                t.sequence,
                t.params.battery_voltage_v,
                t.params.temperature_obcs_c,
                t.params.temperature_eps_c,
                t.params.solar_power_w,
                t.params.rssi_dbm,
                t.params.uptime_s,
                t.params.mode.value,
            )

    async def _update_cache(self, t: CanonicalTelemetry) -> None:
        snapshot = {
            "timestamp": t.timestamp.isoformat(),
            "satellite_id": t.satellite_id,
            "sequence": t.sequence,
            **{k: v for k, v in t.params.model_dump().items()
               if k != "mode"},
            "mode": t.params.mode.value,
        }
        await redis_client.set_last_telemetry(t.satellite_id, snapshot)
        await redis_client.set_satellite_mode(t.satellite_id, t.params.mode.value)
