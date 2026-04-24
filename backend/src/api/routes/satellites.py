from fastapi import APIRouter, HTTPException, status

from src.api.deps import CurrentUser, Pool
from src.api.schemas import SatelliteDetail, SatelliteListItem, TLEResponse
from src.storage.redis_client import get_last_telemetry

router = APIRouter(prefix="/satellites", tags=["satellites"])


@router.get("", response_model=list[SatelliteListItem])
async def list_satellites(pool: Pool, user: CurrentUser):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, norad_id FROM satellites WHERE active = TRUE ORDER BY id"
        )

    result = []
    for row in rows:
        last = await get_last_telemetry(row["id"])
        result.append(SatelliteListItem(
            id=row["id"],
            name=row["name"],
            norad_id=row["norad_id"],
            mode=last["mode"] if last else None,
            last_seen=last["timestamp"] if last else None,
            battery_voltage_v=last.get("battery_voltage_v") if last else None,
        ))
    return result


@router.get("/{satellite_id}", response_model=SatelliteDetail)
async def get_satellite(satellite_id: str, pool: Pool, user: CurrentUser):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, norad_id, description, active, created_at FROM satellites WHERE id = $1",
            satellite_id,
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Satellite '{satellite_id}' not found")

    last = await get_last_telemetry(satellite_id)
    return SatelliteDetail(
        id=row["id"],
        name=row["name"],
        norad_id=row["norad_id"],
        description=row["description"] or "",
        active=row["active"],
        created_at=row["created_at"],
        mode=last["mode"] if last else None,
        last_seen=last["timestamp"] if last else None,
        battery_voltage_v=last.get("battery_voltage_v") if last else None,
    )


@router.get("/{satellite_id}/tle", response_model=TLEResponse)
async def get_latest_tle(satellite_id: str, pool: Pool, user: CurrentUser):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT satellite_id, epoch, tle_line1, tle_line2
            FROM tle_history WHERE satellite_id = $1
            ORDER BY epoch DESC LIMIT 1
            """,
            satellite_id,
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No TLE found for this satellite")
    return TLEResponse(**dict(row))
