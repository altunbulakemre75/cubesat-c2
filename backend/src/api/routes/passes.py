from datetime import datetime, timezone

from fastapi import APIRouter, Query

from src.api.deps import CurrentUser, Pool
from src.api.schemas import PassOut

router = APIRouter(prefix="/passes", tags=["passes"])


@router.get("", response_model=list[PassOut])
async def list_passes(
    pool: Pool,
    user: CurrentUser,
    satellite_id: str | None = Query(default=None),
    from_time: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    if from_time is None:
        from_time = datetime.now(timezone.utc)

    conditions = ["p.aos >= $1"]
    args: list = [from_time]

    if satellite_id:
        args.append(satellite_id)
        conditions.append(f"p.satellite_id = ${len(args)}")

    args.append(limit)
    query = f"""
        SELECT p.id, p.satellite_id, p.station_id, gs.name AS station_name,
               p.aos, p.los, p.max_elevation_deg, p.azimuth_at_aos_deg
        FROM pass_schedule p
        JOIN ground_stations gs ON gs.id = p.station_id
        WHERE {' AND '.join(conditions)}
        ORDER BY p.aos
        LIMIT ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    return [
        PassOut(
            id=row["id"],
            satellite_id=row["satellite_id"],
            station_id=row["station_id"],
            station_name=row["station_name"],
            aos=row["aos"],
            los=row["los"],
            max_elevation_deg=row["max_elevation_deg"],
            azimuth_at_aos_deg=row["azimuth_at_aos_deg"],
        )
        for row in rows
    ]
