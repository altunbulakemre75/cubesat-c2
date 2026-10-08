"""
Ingestion service: NATS raw telemetry → protocol adapter → canonical telemetry.

Subscribes to telemetry.raw.* (JetStream), decodes each frame with the
configured protocol adapter, and publishes CanonicalTelemetry JSON to
telemetry.canonical.{satellite_id}.

The stream is created automatically on first startup if it does not exist.
Stream configuration is deliberately minimal here; full retention/consumer
settings are handled in Faz 1.6 (NATS JetStream setup).
"""

import asyncio
import logging

import asyncpg
import nats.js.errors
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import StreamConfig

from src.ingestion.adapters import get_adapter
from src.ingestion.adapters.base import ProtocolAdapter
from src.ingestion.registry import SatelliteRegistry

logger = logging.getLogger(__name__)

_STREAM_NAME = "cubesat"
_STREAM_SUBJECTS = [
    "telemetry.raw.*",
    "telemetry.canonical.*",
    # NATS '*' is single-token. We need to cover commands.{sat} (2 tokens) AND
    # commands.ack.{sat} (3 tokens), so use '>' to match any depth.
    "commands.>",
    "events.>",
]
# docs/YOL_HARITASI.md: 7-day retention. Long-term history lives in
# TimescaleDB; the stream only needs to cover outages and replays.
STREAM_MAX_AGE_S = 7 * 24 * 3600
_RAW_SUBJECT = "telemetry.raw.*"
_CANONICAL_PREFIX = "telemetry.canonical"
_DURABLE_NAME = "ingestion"


async def ensure_stream(js: JetStreamContext) -> None:
    """Create or update the cubesat JetStream stream so it always carries the
    full subject list and retention limit. If either drifts over deploys
    (e.g. a new events.* pattern, or a stream created by v0.1.0 without a
    max age), update the existing stream rather than silently publishing
    into the void or growing the disk without bound."""
    wanted = StreamConfig(
        name=_STREAM_NAME, subjects=_STREAM_SUBJECTS, max_age=STREAM_MAX_AGE_S,
    )
    try:
        info = await js.stream_info(_STREAM_NAME)
    except nats.js.errors.NotFoundError:
        await js.add_stream(wanted)
        logger.info("Created NATS stream '%s' with subjects: %s",
                    _STREAM_NAME, _STREAM_SUBJECTS)
        return

    same_subjects = set(info.config.subjects or []) == set(_STREAM_SUBJECTS)
    same_age = (info.config.max_age or 0) == STREAM_MAX_AGE_S
    if same_subjects and same_age:
        logger.debug("NATS stream '%s' already up to date", _STREAM_NAME)
        return
    await js.update_stream(wanted)
    logger.info("Updated NATS stream '%s' (subjects %s, max_age %ss)",
                _STREAM_NAME, sorted(_STREAM_SUBJECTS), STREAM_MAX_AGE_S)


class IngestionService:
    """
    Routes raw protocol frames from NATS into validated CanonicalTelemetry.

    One instance handles all satellites for a given protocol.
    Run multiple instances for multi-protocol setups.
    """

    def __init__(
        self, js: JetStreamContext, protocol: str = "ax25", *, pool: asyncpg.Pool,
    ) -> None:
        self._js = js
        self._adapter: ProtocolAdapter = get_adapter(protocol)
        # Frames name their own satellite; only registered ones get through.
        self._registry = SatelliteRegistry(pool)
        self._received: int = 0
        self._errors: int = 0
        self._rejected: int = 0

    @property
    def stats(self) -> dict[str, int]:
        return {"received": self._received, "errors": self._errors, "rejected": self._rejected}

    async def run(self) -> None:
        await ensure_stream(self._js)

        await self._js.subscribe(
            _RAW_SUBJECT,
            durable=_DURABLE_NAME,
            cb=self._handle,
            manual_ack=True,
        )
        logger.info(
            "Ingestion service running | protocol=%s subject=%s",
            self._adapter.source_name,
            _RAW_SUBJECT,
        )
        try:
            await asyncio.sleep(float("inf"))
        except asyncio.CancelledError:
            pass

    async def _handle(self, msg: Msg) -> None:
        try:
            canonical = self._adapter.decode(msg.data)
            self._received += 1
        except ValueError as exc:
            self._errors += 1
            logger.warning(
                "Decode failed | subject=%s error=%s",
                msg.subject,
                exc,
            )
            await msg.ack()
            return

        if not await self._registry.is_registered(canonical.satellite_id):
            self._rejected += 1
            logger.warning(
                "Frame for unregistered satellite dropped | sat=%s subject=%s",
                canonical.satellite_id, msg.subject,
            )
            await msg.ack()
            return

        out_subject = f"{_CANONICAL_PREFIX}.{canonical.satellite_id}"
        try:
            await self._js.publish(out_subject, canonical.model_dump_json().encode())
        except Exception as exc:  # noqa: BLE001
            # Leave the raw frame on the stream for redelivery; acking here
            # (as v0.1.0 did in a `finally`) silently lost the frame.
            logger.error("Failed to publish canonical | subject=%s: %s", out_subject, exc)
            await msg.nak()
            return
        logger.debug(
            "Canonical published | sat=%s seq=%d mode=%s",
            canonical.satellite_id,
            canonical.sequence,
            canonical.params.mode.value,
        )
        await msg.ack()
