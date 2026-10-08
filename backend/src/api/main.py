"""FastAPI application factory."""

import asyncio
import inspect
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from src.api.background import BackgroundServices
from src.api.bootstrap import ensure_admin_user, ensure_seed_satellites
from src.api.routes import (
    anomalies,
    auth,
    commands,
    fdir,
    passes,
    satellites,
    satnogs,
    stations,
    telemetry,
    users,
)
from src.api.ws import close_shared_nats
from src.api.ws import router as ws_router
from src.config import settings
from src.storage.db import close_pool, get_pool
from src.storage.leader import LeaderElector
from src.storage.migrations import run_migrations
from src.storage.redis_client import close_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # ── startup ──────────────────────────────────────────────────────────────
    pool = await get_pool()
    await run_migrations(pool)          # serialized across workers
    await ensure_admin_user(pool)
    await ensure_seed_satellites(pool, settings.seed_satellites)

    # Every worker serves HTTP/WebSockets; the background services run in
    # exactly one process across all workers and replicas.
    services = BackgroundServices(pool)
    elector = LeaderElector(settings.asyncpg_dsn, services.start, services.stop)
    election = asyncio.create_task(elector.run(), name="leader-election")
    logger.info("CubeSat C2 API started (background services run on the elected leader)")

    yield

    # ── shutdown ─────────────────────────────────────────────────────────────
    election.cancel()
    await asyncio.gather(election, return_exceptions=True)
    await close_shared_nats()
    await close_pool()
    await close_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CubeSat C2",
        description="Open-source CubeSat command & control system",
        version="0.1.1",
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
    async def health() -> dict[str, str]:
        """Liveness probe: returns ok as long as the process is running.
        Does not check downstreams — those belong on /ready."""
        return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    async def ready() -> JSONResponse:
        """Readiness probe: returns ok only when DB, NATS and Redis are
        all reachable. Used by k8s/docker to decide when to route traffic.
        Each downstream is timeboxed (2s wall clock) so a hung dependency
        can't pin the probe response."""
        _PROBE_TIMEOUT_S = 2.0

        async def _safe(coro_factory: Callable[[], Awaitable[bool]]) -> bool:
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
            # redis-py's stubs type ping() as bool | Awaitable[bool] in some
            # versions and Awaitable[bool] in others; handle both.
            result: object = client.ping()
            if inspect.isawaitable(result):
                result = await result
            return bool(result)

        db_ok, nats_ok, redis_ok = await asyncio.gather(
            _safe(_db), _safe(_nats), _safe(_redis),
        )
        body = {
            "status": "ok" if (db_ok and nats_ok and redis_ok) else "degraded",
            "checks": {"db": db_ok, "nats": nats_ok, "redis": redis_ok},
        }
        healthy = db_ok and nats_ok and redis_ok
        return JSONResponse(content=body, status_code=200 if healthy else 503)

    return app


app = create_app()
