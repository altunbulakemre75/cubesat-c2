"""
Append-only audit log helper.
Writes to the audit_log table defined in migrations/001_initial_schema.sql.
Call after successful operations — never inside transactions that might roll back.
"""

import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def log_action(
    pool: asyncpg.Pool,
    username: str,
    action: str,
    target_id: str | None = None,
    target_type: str | None = None,
    details: dict[str, Any] | None = None,
    result: str = "ok",
) -> None:
    """Write one audit log entry. Silently swallows DB errors so a logging
    failure never breaks the calling endpoint."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (username, action, target_type, target_id, details, result)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                username,
                action,
                target_type,
                target_id,
                details,
                result,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("audit log write failed | action=%s user=%s: %s", action, username, exc)
