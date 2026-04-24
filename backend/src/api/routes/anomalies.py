from fastapi import APIRouter, Query

from src.api.deps import CurrentUser, Pool
from src.api.schemas import AnomalyOut

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.get("", response_model=list[AnomalyOut])
async def list_anomalies(
    pool: Pool,
    user: CurrentUser,
    satellite_id: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    acknowledged: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    conditions = ["TRUE"]
    args: list = []

    if satellite_id:
        args.append(satellite_id)
        conditions.append(f"satellite_id = ${len(args)}")
    if severity:
        args.append(severity)
        conditions.append(f"severity = ${len(args)}")
    if acknowledged is not None:
        args.append(acknowledged)
        conditions.append(f"acknowledged = ${len(args)}")

    args.append(limit)
    query = f"""
        SELECT id, satellite_id, parameter, value, z_score,
               severity, detected_at, acknowledged
        FROM anomalies
        WHERE {' AND '.join(conditions)}
        ORDER BY detected_at DESC
        LIMIT ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    return [AnomalyOut(**dict(row)) for row in rows]
