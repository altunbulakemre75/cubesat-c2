"""FastAPI application factory."""

import asyncio
import logging

import nats
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from src.api.routes import anomalies, auth, commands, passes, satellites, telemetry
from src.api.ws import router as ws_router
from src.config import settings
from src.ingestion.service import IngestionService, ensure_stream
from src.ingestion.writer import TelemetryWriter
from src.storage.db import close_pool, get_pool
from src.storage.migrations import run_migrations
from src.storage.redis_client import close_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

_background_tasks: list[asyncio.Task] = []


def create_app() -> FastAPI:
    app = FastAPI(
        title="CubeSat C2",
        description="Open-source CubeSat command & control system",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
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
    app.include_router(anomalies.router)
    app.include_router(ws_router)

    @app.on_event("startup")
    async def startup() -> None:
        pool = await get_pool()
        await run_migrations(pool)

        nc = await nats.connect(settings.nats_url)
        js = nc.jetstream()

        # Create NATS stream so simulator can publish immediately
        await ensure_stream(js)

        # Ingestion: raw → canonical
        ingestion = IngestionService(js, protocol="ax25")
        _background_tasks.append(
            asyncio.create_task(ingestion.run(), name="ingestion")
        )

        # Writer: canonical → TimescaleDB + Redis
        writer = TelemetryWriter(js, pool)
        _background_tasks.append(
            asyncio.create_task(writer.run(), name="writer")
        )

        logger.info("CubeSat C2 API started — ingestion and writer running")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        for task in _background_tasks:
            task.cancel()
        await asyncio.gather(*_background_tasks, return_exceptions=True)
        await close_pool()
        await close_client()

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
