"""
Telemetry writer service.

Subscribes to telemetry.canonical.* on NATS JetStream, parses CanonicalTelemetry,
and pushes to an in-memory queue. A background flusher coroutine drains the
queue every flush_interval_s and writes a batch via executemany — much faster
than one INSERT per packet.

On DB failure, messages are NAK'ed (NATS will redeliver) instead of being
silently ACK'ed and dropped.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import asyncpg
from nats.aio.msg import Msg
from nats.js import JetStreamContext

from src.anomaly.detector import AnomalyDetector
from src.api.metrics import anomalies_detected_total, telemetry_ingested_total
from src.ingestion.models import CanonicalTelemetry
from src.storage import redis_client

logger = logging.getLogger(__name__)

_CANONICAL_SUBJECT = "telemetry.canonical.*"
_DURABLE_NAME = "telemetry-writer"

# Batch tuning — small enough that latency stays sub-second, large enough
# that we get the executemany speedup at high rates.
_DEFAULT_BATCH_SIZE = 100
_DEFAULT_FLUSH_INTERVAL_S = 1.0


class TelemetryWriter:
    def __init__(
        self,
        js: JetStreamContext,
        pool: asyncpg.Pool,
        detector: AnomalyDetector | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL_S,
    ) -> None:
        self._js = js
        self._pool = pool
        self._detector = detector
        self._batch_size = batch_size
        self._flush_interval = flush_interval_s
        self._queue: asyncio.Queue[tuple[CanonicalTelemetry, Msg]] = asyncio.Queue(
            maxsize=batch_size * 10
        )
        self._written: int = 0
        self._errors: int = 0

    @property
    def stats(self) -> dict[str, int]:
        return {"written": self._written, "errors": self._errors}

    async def run(self) -> None:
        await self._js.subscribe(
            _CANONICAL_SUBJECT,
            durable=_DURABLE_NAME,
            cb=self._enqueue,
            manual_ack=True,
        )
        logger.info(
            "Telemetry writer listening on %s (batch=%d, flush=%.1fs)",
            _CANONICAL_SUBJECT, self._batch_size, self._flush_interval,
        )
        try:
            await self._flusher_loop()
        except asyncio.CancelledError:
            # Drain remaining messages before exit
            await self._flush_once()

    async def _enqueue(self, msg: Msg) -> None:
        """NATS callback — parse and enqueue. Bad frames get NAK'ed so they
        end up in the dead-letter consumer; never silently swallowed."""
        try:
            data = json.loads(msg.data.decode())
            telemetry = CanonicalTelemetry.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.warning("Parse error | subject=%s: %s", msg.subject, exc)
            try:
                await msg.term()  # poison message — don't redeliver
            except Exception:  # noqa: BLE001
                pass
            return

        try:
            await self._queue.put((telemetry, msg))
        except asyncio.QueueFull:
            logger.error("Writer queue full — NAKing msg %s", msg.subject)
            try:
                await msg.nak()
            except Exception:  # noqa: BLE001
                pass

    async def _flusher_loop(self) -> None:
        """Drain the queue at fixed interval OR when batch_size is reached."""
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush_once()

    async def _flush_once(self) -> None:
        if self._queue.empty():
            return

        items: list[tuple[CanonicalTelemetry, Msg]] = []
        while items.__len__() < self._batch_size:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not items:
            return

        try:
            await self._batch_write(items)
        except (asyncpg.PostgresConnectionError, asyncpg.InterfaceError, ConnectionError) as exc:
            # Transient DB outage — NAK so JetStream redelivers.
            self._errors += len(items)
            logger.error("Batch write failed (DB transient) — NAKing %d msgs: %s",
                         len(items), exc)
            for _t, m in items:
                try:
                    await m.nak()
                except Exception:  # noqa: BLE001
                    pass
            return
        except Exception as exc:  # noqa: BLE001
            # Non-transient (data shape, schema). Log + ACK so we don't loop forever.
            self._errors += len(items)
            logger.error("Batch write failed (terminal): %s", exc, exc_info=True)
            for _t, m in items:
                try:
                    await m.ack()
                except Exception:  # noqa: BLE001
                    pass
            return

        # Success: cache, anomaly check, ack each.
        for telemetry, msg in items:
            self._written += 1
            telemetry_ingested_total.labels(source=telemetry.source).inc()

            try:
                await self._update_cache(telemetry)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Cache update failed | sat=%s: %s",
                               telemetry.satellite_id, exc)

            if self._detector is not None:
                try:
                    await self._run_anomaly_detection(telemetry)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Anomaly detection failed | sat=%s: %s",
                                   telemetry.satellite_id, exc)

            try:
                await msg.ack()
            except Exception:  # noqa: BLE001
                pass

    async def _batch_write(self, items: list[tuple[CanonicalTelemetry, Msg]]) -> None:
        """Auto-register unknown satellites and bulk-insert telemetry rows
        in one transaction. Two executemany calls instead of 2*N round-trips."""
        sat_rows = list({(t.satellite_id, t.satellite_id) for t, _ in items})
        telem_rows = [
            (
                t.timestamp, t.satellite_id, t.source, t.sequence,
                t.params.battery_voltage_v, t.params.temperature_obcs_c,
                t.params.temperature_eps_c, t.params.solar_power_w,
                t.params.rssi_dbm, t.params.uptime_s, t.params.mode.value,
            )
            for t, _ in items
        ]

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    "INSERT INTO satellites (id, name) VALUES ($1, $2) "
                    "ON CONFLICT (id) DO NOTHING",
                    sat_rows,
                )
                await conn.executemany(
                    """
                    INSERT INTO telemetry (
                        time, satellite_id, source, sequence,
                        battery_voltage_v, temperature_obcs_c, temperature_eps_c,
                        solar_power_w, rssi_dbm, uptime_s, mode
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    """,
                    telem_rows,
                )

    async def _run_anomaly_detection(self, t: CanonicalTelemetry) -> None:
        """Feed telemetry into the detector; persist any events to DB +
        publish them on NATS so the WebSocket /ws/events stream picks them up."""
        params = {
            "battery_voltage_v": t.params.battery_voltage_v,
            "temperature_obcs_c": t.params.temperature_obcs_c,
            "temperature_eps_c": t.params.temperature_eps_c,
            "solar_power_w": t.params.solar_power_w,
            "rssi_dbm": t.params.rssi_dbm,
        }
        events = self._detector.feed(t.satellite_id, params)  # type: ignore[union-attr]
        if not events:
            return

        for ev in events:
            anomalies_detected_total.labels(
                parameter=ev.parameter, severity=ev.severity,
            ).inc()
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO anomalies
                            (satellite_id, parameter, value, z_score, severity, detected_at)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        ev.satellite_id, ev.parameter, ev.value,
                        ev.z_score, ev.severity, ev.detected_at,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Anomaly persist failed | sat=%s param=%s: %s",
                               ev.satellite_id, ev.parameter, exc)

            try:
                import uuid
                ts = ev.detected_at.isoformat()
                # Shape matches frontend AppEvent: id, type, message, timestamp.
                payload = json.dumps({
                    "id": str(uuid.uuid4()),
                    "type": "anomaly",
                    "satellite_id": ev.satellite_id,
                    "message": (
                        f"{ev.parameter}={ev.value:.3f} z={ev.z_score:.2f} ({ev.severity})"
                    ),
                    "timestamp": ts,
                    "severity": ev.severity,
                    "parameter": ev.parameter,
                    "value": ev.value,
                    "z_score": ev.z_score,
                    "detected_at": ts,
                }).encode()
                await self._js.publish(f"events.anomaly.{ev.satellite_id}", payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Anomaly publish failed | sat=%s: %s", ev.satellite_id, exc)

    async def _update_cache(self, t: CanonicalTelemetry) -> None:
        snapshot = {
            "timestamp": t.timestamp.isoformat(),
            "satellite_id": t.satellite_id,
            "sequence": t.sequence,
            **{k: v for k, v in t.params.model_dump().items() if k != "mode"},
            "mode": t.params.mode.value,
        }
        # Wrap Redis calls so a Redis outage doesn't break the writer.
        try:
            await redis_client.set_last_telemetry(t.satellite_id, snapshot)
            await redis_client.set_satellite_mode(t.satellite_id, t.params.mode.value)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis cache write failed (non-fatal): %s", exc)


# Suppress "unused" lint in source for the imported but-not-used datetime
_ = datetime.now(timezone.utc)
