"""
WebSocket handlers for live telemetry and system events.

Clients subscribe to:
  /ws/telemetry/{satellite_id}  — streams TelemetryPoint JSON
  /ws/events                     — streams FDIR + anomaly events
"""

import asyncio
import json
import logging

import nats
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/telemetry/{satellite_id}")
async def ws_telemetry(websocket: WebSocket, satellite_id: str):
    await websocket.accept()
    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()

    subject = f"telemetry.canonical.{satellite_id}"
    sub = await js.subscribe(subject)
    logger.info("WS telemetry | client=%s sat=%s", websocket.client, satellite_id)

    try:
        async for msg in sub.messages:
            await msg.ack()
            try:
                await websocket.send_text(msg.data.decode())
            except WebSocketDisconnect:
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        await sub.unsubscribe()
        await nc.close()
        logger.info("WS telemetry closed | sat=%s", satellite_id)


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()

    # Subscribe to all FDIR and anomaly events
    sub = await js.subscribe("events.>")
    logger.info("WS events | client=%s", websocket.client)

    try:
        async for msg in sub.messages:
            await msg.ack()
            try:
                await websocket.send_text(msg.data.decode())
            except WebSocketDisconnect:
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        await sub.unsubscribe()
        await nc.close()
        logger.info("WS events closed | client=%s", websocket.client)
