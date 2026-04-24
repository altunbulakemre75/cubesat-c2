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

import nats.js.errors
from nats.js import JetStreamContext
from nats.aio.msg import Msg

from src.ingestion.adapters import get_adapter
from src.ingestion.adapters.base import ProtocolAdapter

logger = logging.getLogger(__name__)

_STREAM_NAME = "cubesat"
_STREAM_SUBJECTS = [
    "telemetry.raw.*",
    "telemetry.canonical.*",
    "commands.*",
    "events.*",
]
_RAW_SUBJECT = "telemetry.raw.*"
_CANONICAL_PREFIX = "telemetry.canonical"
_DURABLE_NAME = "ingestion"


async def ensure_stream(js: JetStreamContext) -> None:
    """Create the cubesat JetStream stream if it doesn't already exist."""
    try:
        await js.stream_info(_STREAM_NAME)
        logger.debug("NATS stream '%s' already exists", _STREAM_NAME)
    except nats.js.errors.NotFoundError:
        await js.add_stream(name=_STREAM_NAME, subjects=_STREAM_SUBJECTS)
        logger.info("Created NATS stream '%s' with subjects: %s", _STREAM_NAME, _STREAM_SUBJECTS)


class IngestionService:
    """
    Routes raw protocol frames from NATS into validated CanonicalTelemetry.

    One instance handles all satellites for a given protocol.
    Run multiple instances for multi-protocol setups.
    """

    def __init__(self, js: JetStreamContext, protocol: str = "ax25") -> None:
        self._js = js
        self._adapter: ProtocolAdapter = get_adapter(protocol)
        self._received: int = 0
        self._errors: int = 0

    @property
    def stats(self) -> dict[str, int]:
        return {"received": self._received, "errors": self._errors}

    async def run(self) -> None:
        await ensure_stream(self._js)

        sub = await self._js.subscribe(
            _RAW_SUBJECT,
            durable=_DURABLE_NAME,
            manual_ack=True,
        )
        logger.info(
            "Ingestion service running | protocol=%s subject=%s",
            self._adapter.source_name,
            _RAW_SUBJECT,
        )

        async def _dispatch(msg: Msg) -> None:
            await self._handle(msg)

        # nats-py push subscription delivers via callback; block here
        sub.set_handler(_dispatch)  # type: ignore[attr-defined]
        await asyncio.Event().wait()

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

        out_subject = f"{_CANONICAL_PREFIX}.{canonical.satellite_id}"
        try:
            await self._js.publish(out_subject, canonical.model_dump_json().encode())
            logger.debug(
                "Canonical published | sat=%s seq=%d mode=%s",
                canonical.satellite_id,
                canonical.sequence,
                canonical.params.mode.value,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to publish canonical | subject=%s: %s", out_subject, exc)
        finally:
            await msg.ack()
