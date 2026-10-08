"""
Background services of the C2 core, started and stopped as one unit.

Only the elected leader process runs them (src/storage/leader.py). They
get their own NATS connection so that stopping them also tears down the
callback-based subscriptions (ingestion, writer) — cancelling the tasks
alone would leave those callbacks firing in a process that is no longer
the leader.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg
from nats.aio.client import Client as NATSClient

from src.anomaly.detector import AnomalyDetector
from src.fdir.monitor import FDIRMonitor
from src.ingestion.celestrak import CelestrakRefresher
from src.ingestion.satnogs_fetcher import SatnogsTelemetryFetcher
from src.ingestion.service import IngestionService, ensure_stream
from src.ingestion.writer import TelemetryWriter
from src.scheduler import CommandScheduler
from src.storage.nats_conn import connect_nats

logger = logging.getLogger(__name__)


class BackgroundServices:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._nc: NATSClient | None = None
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        pool = self._pool
        self._nc = await connect_nats("cubesat-backend-services")
        js = self._nc.jetstream()
        await ensure_stream(js)

        # Anomaly detector is shared between writer (per-packet feed) and any
        # future API endpoint that wants to query its state.
        detector = AnomalyDetector()
        services = {
            "ingestion": IngestionService(js, protocol="ax25", pool=pool).run(),
            "writer": TelemetryWriter(js, pool, detector=detector).run(),
            # Periodic scan of cached telemetry; publishes events.fdir.*
            "fdir": FDIRMonitor(pool, js, check_interval_s=60.0).run(),
            # TLE refresh every 6 h for satellites with a NORAD id
            "celestrak": CelestrakRefresher(pool).run(),
            # PENDING → SCHEDULED → TRANSMITTING → SENT → ACKED
            "scheduler": CommandScheduler(pool, js).run(),
            # Real demodulated frames from the SatNOGS network
            "satnogs_fetcher": SatnogsTelemetryFetcher(pool).run(),
        }
        self._tasks = [asyncio.create_task(coro, name=name) for name, coro in services.items()]
        logger.info("Background services running: %s", ", ".join(services))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Services NATS close failed: %s", exc)
            self._nc = None
