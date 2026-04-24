import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sgp4.api import Satrec, WGS84

from src.api.audit import log_action
from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role
from src.api.schemas import SatelliteDetail, SatelliteListItem, TLEResponse
from src.orbit.passes import GroundStation, predict_passes
from src.storage.redis_client import get_last_telemetry

router = APIRouter(prefix="/satellites", tags=["satellites"])


class TLECreate(BaseModel):
    tle_line1: str
    tle_line2: str
    epoch: datetime | None = None  # auto-parsed from TLE if omitted


class SatelliteCreate(BaseModel):
    id: str
    name: str | None = None
    norad_id: int | None = None
    description: str | None = None


# ── List / Detail ─────────────────────────────────────────────────────────────

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


@router.post("", response_model=SatelliteDetail, status_code=status.HTTP_201_CREATED)
async def create_satellite(body: SatelliteCreate, pool: Pool, user: CurrentUser):
    require_role(Role.OPERATOR, user["role"])
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO satellites (id, name, norad_id, description)
                VALUES ($1, $2, $3, $4)
                RETURNING id, name, norad_id, description, active, created_at
                """,
                body.id, body.name or body.id, body.norad_id, body.description or "",
            )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    detail=f"Satellite '{body.id}' already exists")
            raise

    return SatelliteDetail(
        id=row["id"],
        name=row["name"],
        norad_id=row["norad_id"],
        description=row["description"] or "",
        active=row["active"],
        created_at=row["created_at"],
        mode=None, last_seen=None, battery_voltage_v=None,
    )


@router.delete("/{satellite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_satellite(satellite_id: str, pool: Pool, user: CurrentUser):
    require_role(Role.ADMIN, user["role"])
    # Sorun 4: tüm DELETE'ler tek transaction — biri başarısız olursa rollback
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM telemetry WHERE satellite_id = $1", satellite_id)
            await conn.execute("DELETE FROM pass_schedule WHERE satellite_id = $1", satellite_id)
            result = await conn.execute("DELETE FROM satellites WHERE id = $1", satellite_id)

    if result == "DELETE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Satellite '{satellite_id}' not found")

    await log_action(pool, user["username"], "satellite.delete",
                     target_id=satellite_id, target_type="satellite")


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


# ── TLE ───────────────────────────────────────────────────────────────────────

@router.get("/{satellite_id}/tle", response_model=TLEResponse)
async def get_latest_tle(satellite_id: str, pool: Pool, user: CurrentUser):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT satellite_id, epoch, tle_line1, tle_line2 FROM tle_history "
            "WHERE satellite_id = $1 ORDER BY epoch DESC LIMIT 1",
            satellite_id,
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No TLE found for this satellite")
    return TLEResponse(**dict(row))


@router.post("/{satellite_id}/tle", response_model=TLEResponse, status_code=status.HTTP_201_CREATED)
async def set_tle(
    satellite_id: str,
    body: TLECreate,
    pool: Pool,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    require_role(Role.OPERATOR, user["role"])

    # Sorun 3: TLE format + checksum validation via sgp4
    tle1 = body.tle_line1.strip()
    tle2 = body.tle_line2.strip()

    if len(tle1) != 69 or len(tle2) != 69:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"TLE lines must be exactly 69 characters (got {len(tle1)}, {len(tle2)})",
        )
    if not tle1.startswith("1 ") or not tle2.startswith("2 "):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="TLE line 1 must start with '1 ', line 2 must start with '2 '",
        )
    try:
        sat = Satrec.twoline2rv(tle1, tle2, WGS84)
        e, _r, _v = sat.sgp4(sat.jdsatepoch, sat.jdsatepochF)
        if e != 0:
            raise ValueError(f"SGP4 propagation error code {e}")
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid TLE: {exc}",
        ) from exc

    # Auto-register satellite if it doesn't exist
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO satellites (id, name) VALUES ($1, $1) ON CONFLICT DO NOTHING",
            satellite_id,
        )

    # Parse epoch from TLE line 1 if not provided
    epoch = body.epoch or _parse_tle_epoch(body.tle_line1)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tle_history (satellite_id, epoch, tle_line1, tle_line2)
            VALUES ($1,$2,$3,$4)
            RETURNING satellite_id, epoch, tle_line1, tle_line2
            """,
            satellite_id, epoch, body.tle_line1.strip(), body.tle_line2.strip(),
        )

    # Compute passes in background so the response returns immediately
    background_tasks.add_task(_compute_and_store_passes, pool, satellite_id, body.tle_line1.strip(), body.tle_line2.strip())

    return TLEResponse(**dict(row))


# ── Pass computation ──────────────────────────────────────────────────────────

async def _compute_and_store_passes(pool, satellite_id: str, tle1: str, tle2: str) -> None:
    """Compute 48-hour passes for all active stations and store in pass_schedule."""
    async with pool.acquire() as conn:
        station_rows = await conn.fetch(
            "SELECT id, name, latitude_deg, longitude_deg, elevation_m, min_elevation_deg "
            "FROM ground_stations WHERE active = TRUE"
        )

    if not station_rows:
        return

    now = datetime.now(timezone.utc)
    all_passes = []
    for sr in station_rows:
        station = GroundStation(
            id=sr["id"],
            name=sr["name"],
            lat_deg=sr["latitude_deg"],
            lon_deg=sr["longitude_deg"],
            elevation_m=sr["elevation_m"],
            min_elevation_deg=sr["min_elevation_deg"],
        )
        try:
            passes = predict_passes(satellite_id, tle1, tle2, station, start=now, horizon_hours=48)
            all_passes.extend(passes)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Pass prediction failed | sat=%s station=%s: %s",
                satellite_id, station.name, exc,
                exc_info=True,
            )
            continue

    async with pool.acquire() as conn:
        # Atomic: always DELETE old passes first (so stale predictions don't
        # survive a TLE update that produces zero passes), then INSERT new
        # ones. Both in one transaction for rollback safety.
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM pass_schedule WHERE satellite_id = $1", satellite_id
            )
            if not all_passes:
                return
            await conn.executemany(
                """
                INSERT INTO pass_schedule
                  (satellite_id, station_id, aos, los, max_elevation_deg, azimuth_at_aos_deg)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                [
                    (p.satellite_id, p.station.id, p.aos, p.los,
                     p.max_elevation_deg, p.azimuth_at_aos_deg)
                    for p in all_passes
                ],
            )


def _parse_tle_epoch(tle_line1: str) -> datetime:
    """
    Parse epoch from TLE line 1 (columns 19-32: YYDDD.DDDDDDDD).
    Raises ValueError on malformed input — callers should map to HTTP 422.
    """
    from datetime import timedelta

    if len(tle_line1) < 32:
        raise ValueError(f"TLE line 1 too short to contain epoch (got {len(tle_line1)} chars)")

    try:
        epoch_str = tle_line1[18:32].strip()
        year_2d = int(epoch_str[:2])
        year = 2000 + year_2d if year_2d < 57 else 1900 + year_2d
        day_of_year = float(epoch_str[2:])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Malformed TLE epoch field '{tle_line1[18:32]}': {exc}") from exc

    if not (1 <= day_of_year < 367):
        raise ValueError(f"TLE day-of-year out of range: {day_of_year}")

    day = int(day_of_year)
    frac = day_of_year - day
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1, seconds=frac * 86400)
