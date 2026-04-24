import uuid

from fastapi import APIRouter, HTTPException, Query, status

from src.api.audit import log_action
from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role
from src.api.schemas import CommandCreate, CommandOut
from src.commands.policy import evaluate
from src.ingestion.models import SatelliteMode
from src.storage.redis_client import get_satellite_mode

router = APIRouter(prefix="/commands", tags=["commands"])


@router.post("", response_model=CommandOut, status_code=status.HTTP_201_CREATED)
async def create_command(body: CommandCreate, pool: Pool, user: CurrentUser):
    require_role(Role.OPERATOR, user["role"])

    # Policy check: is this command allowed for the satellite's current mode?
    mode_str = await get_satellite_mode(body.satellite_id)
    if mode_str:
        decision = evaluate(body.command_type, SatelliteMode(mode_str))
        if not decision:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=decision.reason)

    cmd_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        # Auto-register satellite if it doesn't exist yet
        await conn.execute(
            "INSERT INTO satellites (id, name) VALUES ($1, $1) ON CONFLICT DO NOTHING",
            body.satellite_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO commands (
                id, satellite_id, command_type, params, priority,
                safe_retry, idempotency_key, created_by, scheduled_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING *
            """,
            cmd_id, body.satellite_id, body.command_type,
            body.params if body.params else {},
            body.priority, body.safe_retry, body.idempotency_key,
            user["username"], body.scheduled_at,
        )

    await log_action(
        pool, user["username"], "command.create",
        target_id=cmd_id, target_type="command",
        details={"satellite_id": body.satellite_id, "command_type": body.command_type},
    )
    return _row_to_command(row)


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
            WHERE id = $1 AND status IN ('pending', 'scheduled')
            """,
            command_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Command not found or not cancellable")

    await log_action(
        pool, user["username"], "command.cancel",
        target_id=command_id, target_type="command",
    )


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
    )
