from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status

from src.api.deps import CurrentUser, Pool
from src.api.rbac import Role, require_role

router = APIRouter(prefix="/stations", tags=["stations"])


class StationCreate(BaseModel):
    name: str
    satnogs_id: int | None = None
    latitude_deg: float = Field(..., ge=-90, le=90)
    longitude_deg: float = Field(..., ge=-180, le=180)
    elevation_m: float = Field(default=0, ge=0)
    min_elevation_deg: float = Field(default=10.0, ge=0, le=90)


class StationOut(BaseModel):
    id: int
    name: str
    satnogs_id: int | None
    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    min_elevation_deg: float
    active: bool


@router.get("", response_model=list[StationOut])
async def list_stations(pool: Pool, user: CurrentUser):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM ground_stations WHERE active = TRUE ORDER BY name"
        )
    return [StationOut(**dict(r)) for r in rows]


@router.post("", response_model=StationOut, status_code=status.HTTP_201_CREATED)
async def create_station(body: StationCreate, pool: Pool, user: CurrentUser):
    require_role(Role.ADMIN, user["role"])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ground_stations
              (name, satnogs_id, latitude_deg, longitude_deg, elevation_m, min_elevation_deg)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING *
            """,
            body.name, body.satnogs_id, body.latitude_deg,
            body.longitude_deg, body.elevation_m, body.min_elevation_deg,
        )
    return StationOut(**dict(row))


@router.delete("/{station_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_station(station_id: int, pool: Pool, user: CurrentUser):
    require_role(Role.ADMIN, user["role"])
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE ground_stations SET active = FALSE WHERE id = $1", station_id
        )
    if result == "UPDATE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Station not found")
