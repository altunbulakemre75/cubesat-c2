"""
SatNOGS integration endpoints.

POST /satnogs/sync/{satellite_id}?norad_id=25544
  → Fetches TLE from SatNOGS DB, stores it, triggers pass computation.

POST /satnogs/import-stations
  → Imports online SatNOGS stations into ground_stations table.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role
from src.api.routes.satellites import _compute_and_store_passes, _parse_tle_epoch
from src.config import settings
from src.ingestion.satnogs_client import SatNOGSClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/satnogs", tags=["satnogs"])


@router.post("/sync/{satellite_id}", status_code=status.HTTP_202_ACCEPTED)
async def sync_satellite(
    satellite_id: str,
    pool: Pool,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
    norad_id: int = Query(..., description="NORAD catalog number (e.g. 25544 for ISS)"),
):
    """
    Fetch latest TLE from SatNOGS DB for a NORAD ID, store it under satellite_id,
    and trigger pass computation for all active ground stations.
    """
    require_role(Role.OPERATOR, user["role"])

    client = SatNOGSClient(api_token=settings.satnogs_api_token if hasattr(settings, "satnogs_api_token") else None)
    try:
        tle = await client.get_tle(norad_id)
    finally:
        await client.close()

    if not tle or not tle.get("tle1") or not tle.get("tle2"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No TLE found in SatNOGS for NORAD ID {norad_id}",
        )

    tle1 = tle["tle1"].strip()
    tle2 = tle["tle2"].strip()
    epoch = _parse_tle_epoch(tle1)

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO satellites (id, name, norad_id) VALUES ($1, $1, $2) "
            "ON CONFLICT (id) DO UPDATE SET norad_id = $2",
            satellite_id, norad_id,
        )
        await conn.execute(
            "INSERT INTO tle_history (satellite_id, epoch, tle_line1, tle_line2) VALUES ($1,$2,$3,$4)",
            satellite_id, epoch, tle1, tle2,
        )

    background_tasks.add_task(_compute_and_store_passes, pool, satellite_id, tle1, tle2)
    logger.info("SatNOGS sync | sat=%s norad=%d epoch=%s", satellite_id, norad_id, epoch)

    return {
        "satellite_id": satellite_id,
        "norad_id": norad_id,
        "epoch": epoch.isoformat(),
        "tle1": tle1,
        "tle2": tle2,
        "status": "pass_computation_queued",
    }


@router.post("/import-stations", status_code=status.HTTP_202_ACCEPTED)
async def import_satnogs_stations(
    pool: Pool,
    user: CurrentUser,
    min_lat: float = Query(default=35.0),
    max_lat: float = Query(default=43.0),
    min_lon: float = Query(default=25.0),
    max_lon: float = Query(default=45.0),
):
    """
    Import SatNOGS online stations into ground_stations table.
    Default bounding box covers Turkey. Skips already-imported stations.
    """
    require_role(Role.ADMIN, user["role"])

    client = SatNOGSClient(api_token=getattr(settings, "satnogs_api_token", None))
    try:
        stations = await client.get_stations(status="Online")
    finally:
        await client.close()

    # Filter by bounding box
    filtered = [
        s for s in stations
        if s.get("lat") is not None and s.get("lng") is not None
        and min_lat <= float(s["lat"]) <= max_lat
        and min_lon <= float(s["lng"]) <= max_lon
    ]

    imported = 0
    async with pool.acquire() as conn:
        for s in filtered:
            result = await conn.execute(
                """
                INSERT INTO ground_stations (name, satnogs_id, latitude_deg, longitude_deg, elevation_m)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (satnogs_id) DO NOTHING
                """,
                s.get("name", f"SatNOGS-{s['id']}"),
                s["id"],
                float(s["lat"]),
                float(s["lng"]),
                float(s.get("altitude", 0) or 0),
            )
            if result != "INSERT 0 0":
                imported += 1

    return {"imported": imported, "found_in_bbox": len(filtered), "total_online": len(stations)}
