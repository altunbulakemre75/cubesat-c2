import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from src.api.audit import log_action
from src.api.deps import CurrentUser, Pool
from src.api.metrics import commands_denied_by_policy_total, commands_total
from src.api.rbac import Role, require_role
from src.api.schemas import CommandCreate, CommandOut
from src.commands.models import CommandStatus, MAX_RETRIES, TRANSITIONS, UNSAFE_RETRY_TYPES
from src.commands.policy import ADMIN_ONLY_COMMANDS, TWO_ADMIN_COMMANDS, evaluate
from src.ingestion.models import SatelliteMode
from src.storage.redis_client import get_satellite_mode

router = APIRouter(prefix="/commands", tags=["commands"])

# Valid transition targets via PATCH endpoint
_ALLOWED_TRANSITIONS = {
    "scheduled", "transmitting", "sent", "acked", "timeout", "retry",
}


# ── Request schemas ───────────────────────────────────────────────────────────

class TransitionRequest(BaseModel):
    target_status: str
    error_message: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=CommandOut, status_code=status.HTTP_201_CREATED)
async def create_command(body: CommandCreate, pool: Pool, user: CurrentUser):
    require_role(Role.OPERATOR, user["role"])

    # Admin-only commands require admin role
    if body.command_type in ADMIN_ONLY_COMMANDS:
        require_role(Role.ADMIN, user["role"])

    # Critical commands are held until a different admin approves them
    # (POST /commands/{id}/approve). The scheduler only reads 'pending'.
    needs_approval = body.command_type in TWO_ADMIN_COMMANDS
    if needs_approval:
        require_role(Role.ADMIN, user["role"])

    # Policy check: is this command allowed for the satellite's current mode?
    mode_str = await get_satellite_mode(body.satellite_id)
    if mode_str:
        decision = evaluate(body.command_type, SatelliteMode(mode_str))
        if not decision:
            commands_denied_by_policy_total.labels(satellite_mode=mode_str).inc()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=decision.reason)

    cmd_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        if not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM satellites WHERE id = $1)", body.satellite_id,
        ):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"Satellite '{body.satellite_id}' is not registered",
            )
        row = await conn.fetchrow(
            """
            INSERT INTO commands (
                id, satellite_id, command_type, params, priority,
                safe_retry, idempotency_key, created_by, scheduled_at, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            RETURNING *
            """,
            cmd_id, body.satellite_id, body.command_type,
            body.params if body.params else {},
            body.priority, body.safe_retry, body.idempotency_key,
            user["username"], body.scheduled_at,
            (CommandStatus.AWAITING_APPROVAL if needs_approval else CommandStatus.PENDING).value,
        )

    commands_total.inc()
    await log_action(
        pool, user["username"], "command.create",
        target_id=cmd_id, target_type="command",
        details={"satellite_id": body.satellite_id, "command_type": body.command_type},
    )
    return _row_to_command(row)


@router.post("/{command_id}/approve", response_model=CommandOut)
async def approve_command(command_id: str, pool: Pool, user: CurrentUser):
    """Second-admin approval for a critical command. The approver must be a
    different admin than the creator; the mode policy is re-checked because
    the satellite may have changed mode since the request was made."""
    require_role(Role.ADMIN, user["role"])

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM commands WHERE id = $1", command_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Command not found")
    if row["status"] != CommandStatus.AWAITING_APPROVAL.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Command is not awaiting approval")
    if row["created_by"] == user["username"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="A different admin must approve this command",
        )

    mode_str = await get_satellite_mode(row["satellite_id"])
    if mode_str:
        decision = evaluate(row["command_type"], SatelliteMode(mode_str))
        if not decision:
            commands_denied_by_policy_total.labels(satellite_mode=mode_str).inc()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=decision.reason)

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE commands
               SET status = 'pending', approved_by = $2, approved_at = NOW(),
                   updated_at = NOW()
             WHERE id = $1 AND status = 'awaiting_approval' AND created_by <> $2
            RETURNING *
            """,
            command_id, user["username"],
        )
    if updated is None:
        # Lost a race with another approver or a cancel.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Command is not awaiting approval")

    await log_action(
        pool, user["username"], "command.two_admin_approve",
        target_id=command_id, target_type="command",
        details={
            "original_admin": row["created_by"],
            "approving_admin": user["username"],
            "command_type": row["command_type"],
            "satellite_id": row["satellite_id"],
        },
    )
    return _row_to_command(updated)


@router.patch("/{command_id}/transition", response_model=CommandOut)
async def transition_command(
    command_id: str,
    body: TransitionRequest,
    pool: Pool,
    user: CurrentUser,
):
    """
    Advance a command through the state machine.

    Valid transitions are enforced by the Command model's transition table.
    Only operators and admins can transition commands.
    """
    require_role(Role.OPERATOR, user["role"])

    if body.target_status not in _ALLOWED_TRANSITIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid target status '{body.target_status}'. "
                   f"Allowed: {sorted(_ALLOWED_TRANSITIONS)}",
        )

    target = CommandStatus(body.target_status)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM commands WHERE id = $1", command_id)
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Command not found")

        current = CommandStatus(row["status"])

        # Validate state machine transition
        valid_next = TRANSITIONS.get(current, set())
        if target not in valid_next:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot transition from '{current.value}' to '{target.value}'. "
                       f"Valid targets: {sorted(s.value for s in valid_next)}",
            )

        # Retry-specific checks
        if target == CommandStatus.RETRY:
            if row["retry_count"] >= MAX_RETRIES:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Max retries ({MAX_RETRIES}) exhausted for command {command_id}",
                )
            if row["command_type"] in UNSAFE_RETRY_TYPES:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Command type '{row['command_type']}' is unsafe to retry",
                )
            if not row["safe_retry"]:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Command was not marked as safe_retry",
                )

        # Build the UPDATE
        updates = ["status = $2", "updated_at = NOW()"]
        args: list = [command_id, target.value]

        if target == CommandStatus.SENT:
            updates.append("sent_at = NOW()")
        elif target == CommandStatus.ACKED:
            updates.append("acked_at = NOW()")
        elif target == CommandStatus.RETRY:
            updates.append(f"retry_count = retry_count + 1")

        if body.error_message:
            args.append(body.error_message)
            updates.append(f"error_message = ${len(args)}")

        query = f"UPDATE commands SET {', '.join(updates)} WHERE id = $1 RETURNING *"
        updated = await conn.fetchrow(query, *args)

    await log_action(
        pool, user["username"], "command.transition",
        target_id=command_id, target_type="command",
        details={
            "from": current.value,
            "to": target.value,
            "error_message": body.error_message,
        },
    )
    return _row_to_command(updated)


@router.get("", response_model=list[CommandOut])
async def list_commands(
    pool: Pool,
    user: CurrentUser,
    satellite_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
):
    conditions = ["TRUE"]
    args: list = []
    if satellite_id:
        args.append(satellite_id)
        conditions.append(f"satellite_id = ${len(args)}")
    if status_filter:
        args.append(status_filter)
        conditions.append(f"status = ${len(args)}")
    args.append(limit)

    query = f"""
        SELECT * FROM commands
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC
        LIMIT ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [_row_to_command(r) for r in rows]


@router.delete("/{command_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_command(command_id: str, pool: Pool, user: CurrentUser):
    require_role(Role.OPERATOR, user["role"])
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE commands SET status = 'dead', updated_at = NOW()
            WHERE id = $1 AND status IN ('awaiting_approval', 'pending', 'scheduled')
            """,
            command_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Command not found or not cancellable")

    await log_action(
        pool, user["username"], "command.cancel",
        target_id=command_id, target_type="command",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_command(row) -> CommandOut:
    return CommandOut(
        id=str(row["id"]),
        satellite_id=row["satellite_id"],
        command_type=row["command_type"],
        params=dict(row["params"]) if row["params"] else {},
        priority=row["priority"],
        status=row["status"],
        safe_retry=row["safe_retry"],
        created_by=row["created_by"],
        retry_count=row["retry_count"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        scheduled_at=row["scheduled_at"],
        sent_at=row["sent_at"],
        acked_at=row["acked_at"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
    )
