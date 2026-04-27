"""
WebSocket handlers for live telemetry and system events.

Clients subscribe to:
  /ws/telemetry/{satellite_id}?token=...  — streams TelemetryPoint JSON
  /ws/events?token=...                    — streams FDIR + anomaly events

Authentication: JWT passed as query string (browsers can't set custom
WebSocket headers). The handshake is REJECTED (close code 1008) before
accept() so an unauthenticated client never sees a 101 Upgrade.

NATS Connection Sharing: All WebSocket connections share a single NATS
client via a module-level lazy singleton. This avoids creating one NATS
connection per WebSocket (which caused N connections for N clients).
"""

import asyncio
import logging

import nats
from nats.aio.client import Client as NATSClient
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError

from src.api.auth import decode_token
from src.api.metrics import websocket_connections_active
from src.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Shared NATS connection ────────────────────────────────────────────────────

_shared_nc: NATSClient | None = None
_nc_lock = asyncio.Lock()


async def _get_shared_nats() -> NATSClient:
    """Lazy singleton NATS client shared by all WebSocket handlers."""
    global _shared_nc
    if _shared_nc is not None and _shared_nc.is_connected:
        return _shared_nc
    async with _nc_lock:
        # Double-check after acquiring the lock
        if _shared_nc is not None and _shared_nc.is_connected:
            return _shared_nc
        _shared_nc = await nats.connect(settings.nats_url)
        logger.info("WS shared NATS connection established")
        return _shared_nc


async def close_shared_nats() -> None:
    """Call during shutdown to cleanly close the shared WS NATS connection."""
    global _shared_nc
    if _shared_nc is not None:
        try:
            await _shared_nc.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("WS shared NATS close failed: %s", exc)
        _shared_nc = None


# ── Auth helper ───────────────────────────────────────────────────────────────

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


# ── Telemetry WebSocket ───────────────────────────────────────────────────────

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

    sub = None
    try:
        nc = await _get_shared_nats()
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
        websocket_connections_active.labels(channel="telemetry").dec()
        logger.info("WS telemetry closed | sat=%s", satellite_id)


# ── Events WebSocket ─────────────────────────────────────────────────────────

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

    sub = None
    try:
        nc = await _get_shared_nats()
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
        websocket_connections_active.labels(channel="events").dec()
        logger.info("WS events closed | user=%s", user["username"])
