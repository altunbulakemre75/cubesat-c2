from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query

from src.api.deps import CurrentUser, Pool
from src.api.schemas import TelemetryParamsOut, TelemetryPoint
from src.ingestion.models import SatelliteMode

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/{satellite_id}", response_model=list[TelemetryPoint])
async def get_telemetry(
    satellite_id: str,
    pool: Pool,
    user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=1000),
    from_time: datetime | None = Query(default=None),
    to_time: datetime | None = Query(default=None),
):
    if to_time is None:
        to_time = datetime.now(timezone.utc)
    if from_time is None:
        from_time = to_time - timedelta(hours=1)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT time, satellite_id, sequence,
                   battery_voltage_v, temperature_obcs_c, temperature_eps_c,
                   solar_power_w, rssi_dbm, uptime_s, mode
            FROM telemetry
            WHERE satellite_id = $1 AND time BETWEEN $2 AND $3
            ORDER BY time DESC
            LIMIT $4
            """,
            satellite_id, from_time, to_time, limit,
        )

    return [
        TelemetryPoint(
            timestamp=row["time"],
            satellite_id=row["satellite_id"],
            sequence=row["sequence"],
            params=TelemetryParamsOut(
                battery_voltage_v=row["battery_voltage_v"],
                temperature_obcs_c=row["temperature_obcs_c"],
                temperature_eps_c=row["temperature_eps_c"],
                solar_power_w=row["solar_power_w"],
                rssi_dbm=row["rssi_dbm"],
                uptime_s=row["uptime_s"],
                mode=SatelliteMode(row["mode"]),
            ),
        )
        for row in rows
    ]
