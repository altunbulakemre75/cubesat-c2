"""
FDIR alert log + acknowledgement endpoints.

GET  /fdir/alerts                    — list alerts, default unack first
POST /fdir/alerts/{alert_id}/ack     — operator acknowledges an alert

Both require operator role; no admin-only paths.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from src.api.audit import log_action
from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role

router = APIRouter(prefix="/fdir", tags=["fdir"])


class FDIRAlertOut(BaseModel):
    id: str
    satellite_id: str
    reason: str
    severity: str
    triggered_at: datetime
    acknowledged: bool
    acknowledged_by: str | None
    acknowledged_at: datetime | None


@router.get("/alerts", response_model=list[FDIRAlertOut])
async def list_alerts(
    pool: Pool,
    user: CurrentUser,
    satellite_id: str | None = Query(default=None),
    unacknowledged_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
):
    require_role(Role.VIEWER, user["role"])

    conditions = ["TRUE"]
    args: list = []
    if satellite_id:
        args.append(satellite_id)
        conditions.append(f"satellite_id = ${len(args)}")
    if unacknowledged_only:
        conditions.append("acknowledged = FALSE")
    args.append(limit)

    query = f"""
        SELECT id, satellite_id, reason, severity, triggered_at,
               acknowledged, acknowledged_by, acknowledged_at
          FROM fdir_alerts
         WHERE {' AND '.join(conditions)}
         ORDER BY acknowledged ASC, triggered_at DESC
         LIMIT ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [
        FDIRAlertOut(
            id=str(r["id"]),
            satellite_id=r["satellite_id"],
            reason=r["reason"],
            severity=r["severity"],
            triggered_at=r["triggered_at"],
            acknowledged=r["acknowledged"],
            acknowledged_by=r["acknowledged_by"],
            acknowledged_at=r["acknowledged_at"],
        )
        for r in rows
    ]


@router.post("/alerts/{alert_id}/ack", response_model=FDIRAlertOut)
async def acknowledge_alert(alert_id: str, pool: Pool, user: CurrentUser):
    require_role(Role.OPERATOR, user["role"])

    try:
        UUID(alert_id)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="alert_id must be a UUID")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE fdir_alerts
               SET acknowledged = TRUE,
                   acknowledged_by = $2,
                   acknowledged_at = NOW()
             WHERE id = $1 AND acknowledged = FALSE
            RETURNING id, satellite_id, reason, severity, triggered_at,
                      acknowledged, acknowledged_by, acknowledged_at
            """,
            alert_id, user["username"],
        )

    if row is None:
        # Either the id doesn't exist or it was already acknowledged.
        # Distinguish so the operator gets a useful 404 vs. 409.
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT acknowledged FROM fdir_alerts WHERE id = $1",
                alert_id,
            )
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alert not found")
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Alert already acknowledged")

    await log_action(
        pool, user["username"], "fdir.alert_ack",
        target_id=alert_id, target_type="fdir_alert",
        details={"satellite_id": row["satellite_id"]},
    )
    return FDIRAlertOut(
        id=str(row["id"]),
        satellite_id=row["satellite_id"],
        reason=row["reason"],
        severity=row["severity"],
        triggered_at=row["triggered_at"],
        acknowledged=row["acknowledged"],
        acknowledged_by=row["acknowledged_by"],
        acknowledged_at=row["acknowledged_at"],
    )
