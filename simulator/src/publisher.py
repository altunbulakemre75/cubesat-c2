import asyncio
import logging

import nats
import nats.errors
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext

from src.ax25_framer import build_frame
from src.satellite import CubeSat

logger = logging.getLogger(__name__)


async def run_satellite(
    js: JetStreamContext,
    satellite: CubeSat,
    interval_s: float = 1.0,
) -> None:
    """Tick the satellite every interval_s and publish its AX.25 frame to NATS."""
    subject = f"telemetry.raw.{satellite.satellite_id}"
    logger.info("Simulator started | satellite=%s subject=%s", satellite.satellite_id, subject)

    while True:
        telemetry = satellite.tick()
        frame = build_frame(telemetry)

        try:
            await js.publish(subject, frame)
            logger.debug(
                "Published | sat=%s seq=%d mode=%s bat=%.3fV",
                telemetry.satellite_id,
                telemetry.sequence,
                telemetry.mode.value,
                telemetry.battery_voltage_v,
            )
        except nats.errors.TimeoutError:
            logger.warning("NATS publish timeout | sat=%s seq=%d", telemetry.satellite_id, telemetry.sequence)
        except Exception as exc:  # noqa: BLE001
            logger.error("NATS publish error | sat=%s: %s", telemetry.satellite_id, exc)

        await asyncio.sleep(interval_s)


async def connect_with_retry(nats_url: str, max_attempts: int = 10) -> NATSClient:
    """Connect to NATS, retrying on failure (useful when containers start together)."""
    for attempt in range(1, max_attempts + 1):
        try:
            nc = await nats.connect(nats_url)
            logger.info("Connected to NATS at %s", nats_url)
            return nc
        except Exception as exc:  # noqa: BLE001
            wait = min(2 ** attempt, 30)
            logger.warning("NATS connection failed (attempt %d/%d): %s — retrying in %ds", attempt, max_attempts, exc, wait)
            await asyncio.sleep(wait)

    raise RuntimeError(f"Could not connect to NATS at {nats_url} after {max_attempts} attempts")
