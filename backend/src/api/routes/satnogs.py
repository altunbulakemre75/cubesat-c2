"""
SatNOGS integration endpoints.

POST /satnogs/sync/{satellite_id}?norad_id=25544
  → Fetches TLE from SatNOGS DB, stores it, triggers pass computation.

POST /satnogs/import-stations?scope=turkey|europe|world
  → Imports online SatNOGS stations into ground_stations table.

GET  /satnogs/observations?satellite_id=...&limit=20
  → Returns recent demodulated frames pulled from db.satnogs.org by the
    background fetcher. This is REAL satellite telemetry (amateur cubesats).
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel

from src.api.audit import log_action
from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role
from src.api.routes.satellites import _compute_and_store_passes, _parse_tle_epoch
from src.config import settings
from src.ingestion.satnogs_client import SatNOGSClient


class SatnogsObservationOut(BaseModel):
    id: int
    satellite_id: str | None
    norad_cat_id: int
    observer: str | None
    transmitter: str | None
    timestamp_utc: datetime
    frame_hex: str | None
    decoded_json: dict[str, Any] | None
    app_source: str | None

# Pre-defined bounding boxes (lat_min, lat_max, lon_min, lon_max)
_BBOX_SCOPES = {
    "turkey": (35.0, 43.0, 25.0, 45.0),
    "europe": (34.0, 72.0, -25.0, 45.0),
    "americas": (-56.0, 72.0, -170.0, -30.0),
    "asia_pacific": (-50.0, 55.0, 60.0, 180.0),
    "world": (-90.0, 90.0, -180.0, 180.0),
}

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
    background_tasks: BackgroundTasks,
    scope: str = Query(
        default="turkey",
        description=f"Region preset: {', '.join(_BBOX_SCOPES)}",
    ),
    limit: int = Query(default=200, ge=1, le=2000, description="Max stations to import"),
    recompute_passes: bool = Query(
        default=True,
        description="Recompute passes for all satellites with TLE after import",
    ),
    min_lat: float | None = Query(default=None, description="Override scope bbox"),
    max_lat: float | None = Query(default=None),
    min_lon: float | None = Query(default=None),
    max_lon: float | None = Query(default=None),
):
    """
    Import SatNOGS online stations into ground_stations table.
    Skips already-imported stations (ON CONFLICT DO NOTHING on satnogs_id).

    Use ?scope=world to pull the entire SatNOGS network (capped by limit).
    """
    require_role(Role.ADMIN, user["role"])

    # Resolve bbox: custom if all 4 provided, otherwise scope preset
    if all(x is not None for x in (min_lat, max_lat, min_lon, max_lon)):
        bbox = (min_lat, max_lat, min_lon, max_lon)
    else:
        if scope not in _BBOX_SCOPES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown scope '{scope}'. Options: {sorted(_BBOX_SCOPES)}",
            )
        bbox = _BBOX_SCOPES[scope]

    lat_min, lat_max, lon_min, lon_max = bbox

    client = SatNOGSClient(api_token=getattr(settings, "satnogs_api_token", None))
    try:
        stations = await client.get_stations(status="Online")
    finally:
        await client.close()

    filtered = [
        s for s in stations
        if s.get("lat") is not None and s.get("lng") is not None
        and lat_min <= float(s["lat"]) <= lat_max
        and lon_min <= float(s["lng"]) <= lon_max
    ][:limit]

    # SatNOGS sometimes returns name="" (empty), so coalesce to a fallback.
    rows_to_insert = [
        (
            (s.get("name") or "").strip() or f"SatNOGS-{s['id']}",
            s["id"],
            float(s["lat"]),
            float(s["lng"]),
            float(s.get("altitude", 0) or 0),
        )
        for s in filtered
    ]

    imported = 0
    async with pool.acquire() as conn:
        # One transaction + executemany — was 2000 sequential round-trips for
        # scope=world, now ~1 query.
        async with conn.transaction():
            before = await conn.fetchval("SELECT COUNT(*) FROM ground_stations")
            await conn.executemany(
                """
                INSERT INTO ground_stations
                  (name, satnogs_id, latitude_deg, longitude_deg, elevation_m)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (satnogs_id) DO NOTHING
                """,
                rows_to_insert,
            )
            after = await conn.fetchval("SELECT COUNT(*) FROM ground_stations")
            imported = after - before

    await log_action(
        pool, user["username"], "satnogs.import_stations",
        details={"scope": scope, "imported": imported, "filtered": len(filtered)},
    )

    # New stations don't show up in pass predictions until passes are recomputed
    # for each satellite that has a TLE. Otherwise the dashboard says "300
    # stations imported" but the Pass Schedule only shows the old stations.
    recomputed_for: list[str] = []
    if recompute_passes and imported > 0:
        async with pool.acquire() as conn:
            tle_rows = await conn.fetch(
                """
                SELECT DISTINCT ON (satellite_id) satellite_id, tle_line1, tle_line2
                FROM tle_history ORDER BY satellite_id, epoch DESC
                """
            )
        for r in tle_rows:
            background_tasks.add_task(
                _compute_and_store_passes, pool,
                r["satellite_id"], r["tle_line1"], r["tle_line2"],
            )
            recomputed_for.append(r["satellite_id"])

    return {
        "scope": scope,
        "bbox": {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon_min, "lon_max": lon_max},
        "imported": imported,
        "found_in_bbox": len(filtered),
        "total_online": len(stations),
        "passes_recomputing_for": recomputed_for,
    }


@router.get("/observations", response_model=list[SatnogsObservationOut])
async def list_observations(
    pool: Pool,
    user: CurrentUser,
    satellite_id: str | None = Query(default=None),
    norad_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """List recent SatNOGS observations persisted by the background fetcher.

    Returns the most recent first. Filter by satellite_id (our internal id)
    or by norad_id; both are optional."""
    require_role(Role.VIEWER, user["role"])

    conditions = ["TRUE"]
    args: list = []
    if satellite_id:
        args.append(satellite_id)
        conditions.append(f"satellite_id = ${len(args)}")
    if norad_id:
        args.append(norad_id)
        conditions.append(f"norad_cat_id = ${len(args)}")
    args.append(limit)

    query = f"""
        SELECT id, satellite_id, norad_cat_id, observer, transmitter,
               timestamp_utc, frame_hex, decoded_json, app_source
          FROM satnogs_observations
         WHERE {' AND '.join(conditions)}
         ORDER BY timestamp_utc DESC
         LIMIT ${len(args)}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    return [
        SatnogsObservationOut(
            id=r["id"],
            satellite_id=r["satellite_id"],
            norad_cat_id=r["norad_cat_id"],
            observer=r["observer"],
            transmitter=r["transmitter"],
            timestamp_utc=r["timestamp_utc"],
            frame_hex=r["frame_hex"],
            decoded_json=r["decoded_json"],
            app_source=r["app_source"],
        )
        for r in rows
    ]
