"""Single place that opens NATS connections, so every client carries the
credentials from settings."""

from nats.aio.client import Client as NATSClient

import nats
from src.config import settings


async def connect_nats(name: str = "cubesat-backend") -> NATSClient:
    return await nats.connect(
        settings.nats_url,
        user=settings.nats_user,
        password=settings.nats_password,
        name=name,
    )
