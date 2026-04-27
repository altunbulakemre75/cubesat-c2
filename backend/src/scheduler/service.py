"""
Command scheduler & executor.

Three coroutines run in parallel:

1. _scheduler_loop  — pulls PENDING commands, finds the next pass window
   from `pass_schedule`, transitions them to SCHEDULED with
   scheduled_at = pass.aos (or NOW() if the user supplied a manual
   scheduled_at in the past — operator override).

2. _executor_loop  — pulls SCHEDULED commands whose scheduled_at <= NOW(),
   transitions to TRANSMITTING, publishes the command payload to
   `commands.{satellite_id}` on NATS, then transitions to SENT.

3. _ack_listener   — subscribes to `commands.ack.>` for satellite-side
   ack messages. Matches by command_id, transitions SENT → ACKED.
   Also runs a timeout sweep: SENT commands older than ACK_TIMEOUT_S
   move to TIMEOUT, then RETRY (if safe_retry) or DEAD.

Design notes:
- All three loops are crash-isolated; one cycle's exception logs and
  continues, no exception kills the scheduler.
- We DO NOT auto-cancel commands when a satellite has no upcoming pass —
  the operator can either wait (TLE update will refresh pass_schedule)
  or DELETE the command. This avoids losing operator intent on stale TLEs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from nats.js import JetStreamContext

from src.commands.models import CommandStatus, MAX_RETRIES, UNSAFE_RETRY_TYPES

logger = logging.getLogger(__name__)

_SCHEDULE_INTERVAL_S = 5.0    # how often to poll PENDING commands
_EXECUTE_INTERVAL_S = 1.0     # how often to poll SCHEDULED commands ready to fire
_TIMEOUT_INTERVAL_S = 10.0    # how often to scan SENT commands for ACK timeout
ACK_TIMEOUT_S = 60.0          # how long to wait for an ACK after publishing


def _cmd_subject(satellite_id: str) -> str:
    return f"commands.{satellite_id}"


def _ack_subject_pattern() -> str:
    return "commands.ack.>"


class CommandScheduler:
    """Owns the three coroutines and the shared connection pool / NATS."""

    def __init__(self, pool: asyncpg.Pool, js: JetStreamContext) -> None:
        self._pool = pool
        self._js = js
        # Counters for /metrics + tests
        self.scheduled_count = 0
        self.transmitted_count = 0
        self.acked_count = 0
        self.timed_out_count = 0
        self.dead_count = 0

    async def run(self) -> None:
        logger.info(
            "CommandScheduler started (schedule=%.1fs, execute=%.1fs, timeout=%.1fs, ack_timeout=%.0fs)",
            _SCHEDULE_INTERVAL_S, _EXECUTE_INTERVAL_S, _TIMEOUT_INTERVAL_S, ACK_TIMEOUT_S,
        )
        await asyncio.gather(
            self._scheduler_loop(),
            self._executor_loop(),
            self._ack_listener(),
            self._timeout_loop(),
            return_exceptions=False,
        )

    # ─────────────────────────────────────────────────────────────────────
    # PENDING → SCHEDULED
    # ─────────────────────────────────────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        while True:
            await asyncio.sleep(_SCHEDULE_INTERVAL_S)
            try:
                await self._schedule_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("Scheduler cycle error: %s", exc, exc_info=True)

    async def _schedule_once(self) -> None:
        async with self._pool.acquire() as conn:
            # Operator-supplied scheduled_at takes precedence. Otherwise we
            # look up the next AOS in pass_schedule. If the satellite has no
            # upcoming pass we leave the command PENDING and try again next
            # cycle (TLE refresh may add a pass later).
            rows = await conn.fetch(
                """
                SELECT id, satellite_id, scheduled_at, command_type, priority
                FROM commands
                WHERE status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT 50
                """
            )
            for row in rows:
                target_at = row["scheduled_at"]
                if target_at is None:
                    target_at = await self._next_pass_aos(conn, row["satellite_id"])
                    if target_at is None:
                        # No pass available yet — leave it pending.
                        continue

                result = await conn.execute(
                    """
                    UPDATE commands
                       SET status = 'scheduled',
                           scheduled_at = $2,
                           updated_at = NOW()
                     WHERE id = $1 AND status = 'pending'
                    """,
                    row["id"], target_at,
                )
                if result == "UPDATE 1":
                    self.scheduled_count += 1
                    logger.info(
                        "SCHEDULED | cmd=%s sat=%s type=%s at=%s",
                        row["id"], row["satellite_id"], row["command_type"],
                        target_at.isoformat(),
                    )

    async def _next_pass_aos(self, conn: asyncpg.Connection, satellite_id: str) -> datetime | None:
        return await conn.fetchval(
            """
            SELECT aos FROM pass_schedule
             WHERE satellite_id = $1 AND aos > NOW()
             ORDER BY aos ASC
             LIMIT 1
            """,
            satellite_id,
        )

    # ─────────────────────────────────────────────────────────────────────
    # SCHEDULED → TRANSMITTING → SENT
    # ─────────────────────────────────────────────────────────────────────

    async def _executor_loop(self) -> None:
        while True:
            await asyncio.sleep(_EXECUTE_INTERVAL_S)
            try:
                await self._execute_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("Executor cycle error: %s", exc, exc_info=True)

    async def _execute_once(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, satellite_id, command_type, params, retry_count
                FROM commands
                WHERE status = 'scheduled'
                  AND scheduled_at <= NOW()
                ORDER BY scheduled_at ASC
                LIMIT 50
                """
            )

        for row in rows:
            cmd_id = row["id"]
            sat_id = row["satellite_id"]
            # Move to TRANSMITTING — guard against double-pickup
            async with self._pool.acquire() as conn:
                claimed = await conn.execute(
                    """
                    UPDATE commands
                       SET status = 'transmitting', updated_at = NOW()
                     WHERE id = $1 AND status = 'scheduled'
                    """,
                    cmd_id,
                )
            if claimed != "UPDATE 1":
                # Lost the race — another scheduler picked this up
                continue

            payload = json.dumps({
                "command_id": str(cmd_id),
                "satellite_id": sat_id,
                "command_type": row["command_type"],
                "params": dict(row["params"]) if row["params"] else {},
                "retry_count": row["retry_count"],
                "issued_at": datetime.now(timezone.utc).isoformat(),
            }).encode()

            try:
                await self._js.publish(_cmd_subject(sat_id), payload)
            except Exception as exc:  # noqa: BLE001
                # Publish failed — bounce back to SCHEDULED so we retry next tick.
                logger.error("Command publish failed | cmd=%s sat=%s: %s",
                             cmd_id, sat_id, exc)
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE commands
                           SET status = 'scheduled',
                               error_message = $2,
                               updated_at = NOW()
                         WHERE id = $1 AND status = 'transmitting'
                        """,
                        cmd_id, f"publish failed: {exc}",
                    )
                continue

            # Successful publish → SENT
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE commands
                       SET status = 'sent',
                           sent_at = NOW(),
                           updated_at = NOW()
                     WHERE id = $1 AND status = 'transmitting'
                    """,
                    cmd_id,
                )
            self.transmitted_count += 1
            logger.info("SENT | cmd=%s sat=%s type=%s",
                        cmd_id, sat_id, row["command_type"])

    # ─────────────────────────────────────────────────────────────────────
    # SENT → ACKED  (via NATS commands.ack.>)
    # ─────────────────────────────────────────────────────────────────────

    async def _ack_listener(self) -> None:
        """
        Subscribe to commands.ack.> and mark commands ACKED.

        Expected payload: {"command_id": "<uuid>", "ok": true|false, "error": "..."}
        Subject: commands.ack.{satellite_id}
        """
        durable = "scheduler-ack-" + uuid.uuid4().hex[:8]
        try:
            sub = await self._js.subscribe(
                _ack_subject_pattern(),
                durable=durable,
                manual_ack=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Ack listener subscribe failed: %s", exc)
            return

        logger.info("Ack listener subscribed to %s (durable=%s)",
                    _ack_subject_pattern(), durable)

        async for msg in sub.messages:
            try:
                data = json.loads(msg.data.decode())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ack parse error: %s", exc)
                try:
                    await msg.term()
                except Exception:  # noqa: BLE001
                    pass
                continue

            cmd_id = data.get("command_id")
            ok = bool(data.get("ok", True))
            error = data.get("error")
            if not cmd_id:
                try:
                    await msg.term()
                except Exception:  # noqa: BLE001
                    pass
                continue

            try:
                if ok:
                    await self._mark_acked(cmd_id)
                else:
                    await self._mark_timed_out(cmd_id, error or "satellite returned error")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ack apply failed | cmd=%s: %s", cmd_id, exc)
                try:
                    await msg.nak()
                except Exception:  # noqa: BLE001
                    pass
                continue

            try:
                await msg.ack()
            except Exception:  # noqa: BLE001
                pass

    async def _mark_acked(self, cmd_id: str) -> None:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE commands
                   SET status = 'acked',
                       acked_at = NOW(),
                       updated_at = NOW()
                 WHERE id = $1 AND status = 'sent'
                """,
                cmd_id,
            )
        if result == "UPDATE 1":
            self.acked_count += 1
            logger.info("ACKED | cmd=%s", cmd_id)

    async def _mark_timed_out(self, cmd_id: str, reason: str) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, command_type, safe_retry, retry_count FROM commands WHERE id = $1",
                cmd_id,
            )
            if not row or row["status"] != "sent":
                return
            await conn.execute(
                """
                UPDATE commands
                   SET status = 'timeout',
                       error_message = $2,
                       updated_at = NOW()
                 WHERE id = $1 AND status = 'sent'
                """,
                cmd_id, reason,
            )
        self.timed_out_count += 1

        can_retry = (
            row["safe_retry"]
            and row["command_type"] not in UNSAFE_RETRY_TYPES
            and row["retry_count"] < MAX_RETRIES
        )
        async with self._pool.acquire() as conn:
            if can_retry:
                await conn.execute(
                    """
                    UPDATE commands
                       SET status = 'retry',
                           retry_count = retry_count + 1,
                           updated_at = NOW()
                     WHERE id = $1 AND status = 'timeout'
                    """,
                    cmd_id,
                )
                # Move retry → scheduled (next pass) so executor picks it up
                await conn.execute(
                    """
                    UPDATE commands
                       SET status = 'scheduled',
                           updated_at = NOW()
                     WHERE id = $1 AND status = 'retry'
                    """,
                    cmd_id,
                )
                logger.warning("TIMEOUT→RETRY | cmd=%s reason=%s", cmd_id, reason)
            else:
                await conn.execute(
                    """
                    UPDATE commands
                       SET status = 'dead',
                           updated_at = NOW()
                     WHERE id = $1 AND status = 'timeout'
                    """,
                    cmd_id,
                )
                self.dead_count += 1
                logger.warning("TIMEOUT→DEAD | cmd=%s reason=%s", cmd_id, reason)

    # ─────────────────────────────────────────────────────────────────────
    # SENT timeout sweep (in case satellite never replies)
    # ─────────────────────────────────────────────────────────────────────

    async def _timeout_loop(self) -> None:
        while True:
            await asyncio.sleep(_TIMEOUT_INTERVAL_S)
            try:
                await self._timeout_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("Timeout sweep error: %s", exc, exc_info=True)

    async def _timeout_once(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ACK_TIMEOUT_S)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id FROM commands
                 WHERE status = 'sent' AND sent_at < $1
                 LIMIT 50
                """,
                cutoff,
            )
        for row in rows:
            await self._mark_timed_out(str(row["id"]), f"no ACK within {ACK_TIMEOUT_S:.0f}s")
