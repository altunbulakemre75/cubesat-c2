"""FastAPI application factory."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import nats
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from src.anomaly.detector import AnomalyDetector
from src.api.bootstrap import ensure_admin_user
from src.api.routes import anomalies, auth, commands, fdir, passes, satnogs, satellites, stations, telemetry, users
from src.api.ws import close_shared_nats, router as ws_router
from src.config import settings
from src.fdir.monitor import FDIRMonitor
from src.ingestion.celestrak import CelestrakRefresher
from src.ingestion.satnogs_fetcher import SatnogsTelemetryFetcher
from src.ingestion.service import IngestionService, ensure_stream
from src.ingestion.writer import TelemetryWriter
from src.scheduler import CommandScheduler
from src.storage.db import close_pool, get_pool
from src.storage.migrations import run_migrations
from src.storage.redis_client import close_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # ── startup ──────────────────────────────────────────────────────────────
    pool = await get_pool()
    await run_migrations(pool)
    await ensure_admin_user(pool)

    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()

    await ensure_stream(js)

    ingestion = IngestionService(js, protocol="ax25")
    _background_tasks.append(asyncio.create_task(ingestion.run(), name="ingestion"))

    # Anomaly detector is shared between writer (per-packet feed) and any
    # future API endpoint that wants to query its state.
    detector = AnomalyDetector()

    writer = TelemetryWriter(js, pool, detector=detector)
    _background_tasks.append(asyncio.create_task(writer.run(), name="writer"))

    # FDIR monitor: periodic background task that scans Redis cache for stale
    # telemetry / out-of-bounds values and publishes events.fdir.* on NATS.
    fdir = FDIRMonitor(pool, js, check_interval_s=60.0)
    _background_tasks.append(asyncio.create_task(fdir.run(), name="fdir"))

    # Celestrak TLE auto-refresh: every 6h, pull fresh TLEs for any satellite
    # that has a NORAD ID. Was manual-only; now self-healing.
    celestrak = CelestrakRefresher(pool)
    _background_tasks.append(asyncio.create_task(celestrak.run(), name="celestrak"))

    # Command scheduler: drives PENDING→SCHEDULED→TRANSMITTING→SENT→ACKED
    # via pass_schedule + NATS commands.* + commands.ack.* subjects.
    scheduler = CommandScheduler(pool, js)
    _background_tasks.append(asyncio.create_task(scheduler.run(), name="scheduler"))

    # SatNOGS DB telemetry fetcher: pulls real demodulated frames from
    # amateurs around the world for any satellite that has a NORAD ID.
    satnogs_fetcher = SatnogsTelemetryFetcher(pool)
    _background_tasks.append(asyncio.create_task(satnogs_fetcher.run(), name="satnogs_fetcher"))

    logger.info(
        "CubeSat C2 API started — ingestion + writer + anomaly + FDIR + "
        "Celestrak + scheduler + SatNOGS fetcher running"
    )

    yield

    # ── shutdown ─────────────────────────────────────────────────────────────
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    await nc.close()
    await close_shared_nats()
    await close_pool()
    await close_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CubeSat C2",
        description="Open-source CubeSat command & control system",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    Instrumentator().instrument(app).expose(app)

    app.include_router(auth.router)
    app.include_router(satellites.router)
    app.include_router(telemetry.router)
    app.include_router(commands.router)
    app.include_router(passes.router)
    app.include_router(stations.router)
    app.include_router(satnogs.router)
    app.include_router(users.router)
    app.include_router(anomalies.router)
    app.include_router(fdir.router)
    app.include_router(ws_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        """Liveness probe: returns ok as long as the process is running.
        Does not check downstreams — those belong on /ready."""
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    async def ready() -> dict | tuple:
        """Readiness probe: returns ok only when DB, NATS and Redis are
        all reachable. Used by k8s/docker to decide when to route traffic.
        Each downstream is timeboxed (2s wall clock) so a hung dependency
        can't pin the probe response."""
        from fastapi.responses import JSONResponse
        import asyncio

        _PROBE_TIMEOUT_S = 2.0

        async def _safe(coro_factory) -> bool:
            """Run the check; any exception OR timeout = unhealthy."""
            try:
                return await asyncio.wait_for(coro_factory(), timeout=_PROBE_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                return False

        async def _db() -> bool:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True

        async def _nats() -> bool:
            from src.api.ws import _get_shared_nats
            nc = await _get_shared_nats()
            return bool(nc.is_connected)

        async def _redis() -> bool:
            from src.storage import redis_client
            client = redis_client.get_client()
            return bool(await client.ping())

        db_ok, nats_ok, redis_ok = await asyncio.gather(
            _safe(_db), _safe(_nats), _safe(_redis),
        )
        body = {
            "status": "ok" if (db_ok and nats_ok and redis_ok) else "degraded",
            "checks": {"db": db_ok, "nats": nats_ok, "redis": redis_ok},
        }
        if not (db_ok and nats_ok and redis_ok):
            return JSONResponse(content=body, status_code=503)
        return body

    return app


app = create_app()
