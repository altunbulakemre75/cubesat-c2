"""
WebSocket handlers for live telemetry and system events.

Clients subscribe to:
  /ws/telemetry/{satellite_id}?ticket=...  — streams TelemetryPoint JSON
  /ws/events?ticket=...                    — streams FDIR + anomaly events

Authentication: browsers can't set headers on a WebSocket handshake, so the
credential travels in the URL — and therefore in access logs. Clients first
POST /auth/ws-ticket (normal bearer auth) and connect with the returned
single-use, 30-second ticket. The handshake is REJECTED (close code 1008)
before accept() so an unauthenticated client never sees a 101 Upgrade.

Session guard: an open socket is re-validated against the database every
_GUARD_INTERVAL_S and closed when its parent session is revoked, disabled,
demoted below the endpoint's role, or expires. Close codes:
  1008  credentials are no longer acceptable — don't retry
  4001  session expired — fetch a new ticket (refreshing first) and reconnect

NATS Connection Sharing: All WebSocket connections share a single NATS
client via a module-level lazy singleton. This avoids creating one NATS
connection per WebSocket (which caused N connections for N clients).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import asyncpg
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from nats.aio.client import Client as NATSClient
from nats.js.api import ConsumerConfig, DeliverPolicy

from src.api.auth import (
    WS_TICKET_TTL_S,
    AuthError,
    Session,
    check_session_state,
    load_session,
    revoke_token,
)
from src.api.deps import Pool
from src.api.metrics import websocket_connections_active
from src.api.rbac import Role, has_role
from src.storage.nats_conn import connect_nats

logger = logging.getLogger(__name__)
router = APIRouter()

WS_CLOSE_SESSION_EXPIRED = 4001
_GUARD_INTERVAL_S = 30.0
# Consecutive DB errors tolerated by the guard before it gives up and makes
# the client reconnect (which re-authenticates from scratch).
_GUARD_MAX_ERRORS = 3

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
        _shared_nc = await connect_nats("cubesat-backend-ws")
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


# ── Auth ──────────────────────────────────────────────────────────────────────

async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except RuntimeError:
        pass  # already closed by the other side


async def _authenticate_ws(
    websocket: WebSocket, pool: asyncpg.Pool, ticket: str | None, minimum: Role,
) -> Session | None:
    """Validate and consume the ticket BEFORE accept(). Closes the handshake
    on failure so an unauthenticated client never gets a successful upgrade."""
    if not ticket:
        await _close(websocket, status.WS_1008_POLICY_VIOLATION, "Missing ticket")
        return None
    try:
        session = await load_session(pool, ticket, kind="ws")
    except AuthError as exc:
        await _close(websocket, status.WS_1008_POLICY_VIOLATION, exc.detail)
        return None

    if not has_role(minimum, session.role):
        await _close(
            websocket, status.WS_1008_POLICY_VIOLATION,
            f"{minimum.value.capitalize()} role required",
        )
        return None

    # Single use: a ticket replayed from a log line is already spent. The
    # ticket itself expires within WS_TICKET_TTL_S, so that's how long the
    # revocation row needs to live.
    spent_until = datetime.now(UTC) + timedelta(seconds=WS_TICKET_TTL_S)
    if not await revoke_token(pool, session.jti, session.username, spent_until):
        await _close(websocket, status.WS_1008_POLICY_VIOLATION, "Ticket already used")
        return None
    return session


async def _guard(
    websocket: WebSocket, pool: asyncpg.Pool, session: Session, minimum: Role,
) -> None:
    """Close the socket once its session stops being valid."""
    assert session.parent_jti is not None
    errors = 0
    while True:
        remaining = (session.expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            await _close(websocket, WS_CLOSE_SESSION_EXPIRED, "Session expired")
            return
        await asyncio.sleep(min(_GUARD_INTERVAL_S, remaining))
        if datetime.now(UTC) >= session.expires_at:
            continue
        try:
            role = await check_session_state(
                pool, session.username, session.token_version, [session.parent_jti],
            )
            errors = 0
        except AuthError as exc:
            await _close(websocket, status.WS_1008_POLICY_VIOLATION, exc.detail)
            return
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("WS session check failed (%d/%d): %s",
                           errors, _GUARD_MAX_ERRORS, exc)
            if errors >= _GUARD_MAX_ERRORS:
                await _close(websocket, WS_CLOSE_SESSION_EXPIRED, "Session check unavailable")
                return
            continue
        if not has_role(minimum, role):
            await _close(websocket, status.WS_1008_POLICY_VIOLATION, "Role no longer permitted")
            return


async def _pump(websocket: WebSocket, sub: object) -> None:
    async for msg in sub.messages:  # type: ignore[attr-defined]
        await msg.ack()
        await websocket.send_text(msg.data.decode())


async def _watch_client(websocket: WebSocket) -> None:
    """Return as soon as the client goes away, even if no data is flowing —
    otherwise an idle subscription would linger until the next message."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


async def _serve(
    websocket: WebSocket,
    pool: asyncpg.Pool,
    session: Session,
    minimum: Role,
    subject: str,
    channel: str,
) -> None:
    await websocket.accept()
    websocket_connections_active.labels(channel=channel).inc()
    sub = None
    try:
        nc = await _get_shared_nats()
        # Only deliver messages that arrive AFTER this socket connects. The
        # default DeliverAll replays the whole stream on every connect.
        sub = await nc.jetstream().subscribe(
            subject, config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW),
        )
        logger.info("WS %s | user=%s subject=%s", channel, session.username, subject)

        tasks = {
            asyncio.create_task(_pump(websocket, sub)),
            asyncio.create_task(_watch_client(websocket)),
            asyncio.create_task(_guard(websocket, pool, session, minimum)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.warning("WS %s error | user=%s: %s", channel, session.username, exc)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("WS %s error | user=%s: %s", channel, session.username, exc,
                       exc_info=True)
    finally:
        if sub is not None:
            try:
                await sub.unsubscribe()
            except Exception as exc:  # noqa: BLE001
                logger.debug("WS %s unsubscribe failed: %s", channel, exc)
        websocket_connections_active.labels(channel=channel).dec()
        logger.info("WS %s closed | user=%s", channel, session.username)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.websocket("/ws/telemetry/{satellite_id}")
async def ws_telemetry(
    websocket: WebSocket,
    satellite_id: str,
    pool: Pool,
    ticket: str | None = Query(default=None),
) -> None:
    session = await _authenticate_ws(websocket, pool, ticket, Role.VIEWER)
    if session:
        await _serve(websocket, pool, session, Role.VIEWER,
                     f"telemetry.canonical.{satellite_id}", "telemetry")


@router.websocket("/ws/events")
async def ws_events(
    websocket: WebSocket,
    pool: Pool,
    ticket: str | None = Query(default=None),
) -> None:
    session = await _authenticate_ws(websocket, pool, ticket, Role.OPERATOR)
    if session:
        await _serve(websocket, pool, session, Role.OPERATOR, "events.>", "events")
