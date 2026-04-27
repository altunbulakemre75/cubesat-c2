"""
WebSocket handlers for live telemetry and system events.

Clients subscribe to:
  /ws/telemetry/{satellite_id}?token=...  — streams TelemetryPoint JSON
  /ws/events?token=...                    — streams FDIR + anomaly events

Authentication: JWT passed as query string (browsers can't set custom
WebSocket headers). The handshake is REJECTED (close code 1008) before
accept() so an unauthenticated client never sees a 101 Upgrade.
"""

import asyncio
import logging

import nats
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError

from src.api.auth import decode_token
from src.api.metrics import websocket_connections_active
from src.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


async def _authenticate_ws(websocket: WebSocket, token: str | None) -> dict | None:
    """Validate JWT BEFORE accept(). Closes the handshake on failure so an
    unauthenticated client never gets a successful upgrade."""
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return None
    try:
        payload = decode_token(token)
        if not payload.get("sub"):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
            return None
        return {"username": payload["sub"], "role": payload.get("role", "viewer")}
    except JWTError as exc:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=f"Invalid token: {exc}",
        )
        return None


@router.websocket("/ws/telemetry/{satellite_id}")
async def ws_telemetry(
    websocket: WebSocket,
    satellite_id: str,
    token: str | None = Query(default=None),
):
    # Auth FIRST — closing without accept() means the client never sees 101
    user = await _authenticate_ws(websocket, token)
    if not user:
        return

    await websocket.accept()
    websocket_connections_active.labels(channel="telemetry").inc()

    nc = None
    sub = None
    try:
        nc = await nats.connect(settings.nats_url)
        js = nc.jetstream()
        subject = f"telemetry.canonical.{satellite_id}"
        sub = await js.subscribe(subject)
        logger.info("WS telemetry | user=%s sat=%s", user["username"], satellite_id)

        async for msg in sub.messages:
            await msg.ack()
            try:
                await websocket.send_text(msg.data.decode())
            except WebSocketDisconnect:
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("WS telemetry error | sat=%s: %s", satellite_id, exc, exc_info=True)
    finally:
        if sub is not None:
            try:
                await sub.unsubscribe()
            except Exception as exc:  # noqa: BLE001
                logger.debug("WS telemetry sub.unsubscribe failed: %s", exc)
        if nc is not None:
            try:
                await nc.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("WS telemetry nc.close failed: %s", exc)
        websocket_connections_active.labels(channel="telemetry").dec()
        logger.info("WS telemetry closed | sat=%s", satellite_id)


@router.websocket("/ws/events")
async def ws_events(
    websocket: WebSocket,
    token: str | None = Query(default=None),
):
    # Auth + role check BEFORE accept()
    user = await _authenticate_ws(websocket, token)
    if not user:
        return
    if user["role"] not in ("operator", "admin"):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Operator or admin role required",
        )
        return

    await websocket.accept()
    websocket_connections_active.labels(channel="events").inc()

    nc = None
    sub = None
    try:
        nc = await nats.connect(settings.nats_url)
        js = nc.jetstream()
        sub = await js.subscribe("events.>")
        logger.info("WS events | user=%s", user["username"])

        async for msg in sub.messages:
            await msg.ack()
            try:
                await websocket.send_text(msg.data.decode())
            except WebSocketDisconnect:
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("WS events error: %s", exc, exc_info=True)
    finally:
        if sub is not None:
            try:
                await sub.unsubscribe()
            except Exception as exc:  # noqa: BLE001
                logger.debug("WS events sub.unsubscribe failed: %s", exc)
        if nc is not None:
            try:
                await nc.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("WS events nc.close failed: %s", exc)
        websocket_connections_active.labels(channel="events").dec()
        logger.info("WS events closed | user=%s", user["username"])
