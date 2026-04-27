"""
Celestrak TLE auto-refresh background task.

For every satellite in the DB that has a NORAD ID, periodically pull the
latest TLE from Celestrak and store it in tle_history. Pass schedules are
recomputed automatically because the TLE update endpoint already triggers
that flow on insert; here we just write the new TLE row.

Default refresh interval is 6 hours, which is well below the typical 1-day
freshness window for SGP4 propagation.
"""

import asyncio
import logging
from datetime import datetime

import asyncpg
import httpx

from src.api.routes.satellites import _compute_and_store_passes, _parse_tle_epoch

logger = logging.getLogger(__name__)

CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
DEFAULT_REFRESH_INTERVAL_S = 6 * 3600  # 6 hours


async def _fetch_tle(client: httpx.AsyncClient, norad_id: int) -> tuple[str, str] | None:
    """Return (tle_line1, tle_line2) or None if Celestrak has no data."""
    try:
        resp = await client.get(
            CELESTRAK_GP_URL,
            params={"CATNR": norad_id, "FORMAT": "tle"},
            timeout=20.0,
        )
        text = resp.text.strip()
        if not text or "No GP data" in text:
            return None
        lines = text.split("\n")
        if len(lines) < 3:
            return None
        return lines[1].strip(), lines[2].strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Celestrak fetch failed for NORAD %d: %s", norad_id, exc)
        return None


class CelestrakRefresher:
    """
    Periodic background task. Iterates over satellites with a norad_id and
    refreshes their TLE. Skips satellites whose latest TLE is fresh enough.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
        min_tle_age_hours: float = 6.0,
    ) -> None:
        self._pool = pool
        self._interval = interval_s
        self._min_age_hours = min_tle_age_hours

    async def run(self) -> None:
        logger.info(
            "Celestrak refresher started (every %.0fs, min TLE age %.0fh)",
            self._interval, self._min_age_hours,
        )
        # Wait one minute on startup so the rest of the system stabilises
        await asyncio.sleep(60.0)
        while True:
            try:
                await self._cycle()
            except Exception as exc:  # noqa: BLE001
                logger.error("Celestrak cycle error: %s", exc, exc_info=True)
            await asyncio.sleep(self._interval)

    async def _cycle(self) -> None:
        async with self._pool.acquire() as conn:
            satellites = await conn.fetch(
                """
                SELECT s.id, s.norad_id,
                       (SELECT MAX(epoch) FROM tle_history h WHERE h.satellite_id = s.id) AS latest_epoch
                FROM satellites s
                WHERE s.active = TRUE AND s.norad_id IS NOT NULL
                """
            )

        if not satellites:
            return

        async with httpx.AsyncClient() as client:
            updated = 0
            for sat in satellites:
                age_h = self._tle_age_hours(sat["latest_epoch"])
                if age_h is not None and age_h < self._min_age_hours:
                    continue

                tle = await _fetch_tle(client, sat["norad_id"])
                if not tle:
                    continue
                tle1, tle2 = tle
                try:
                    epoch = _parse_tle_epoch(tle1)
                except ValueError as exc:
                    logger.warning("Bad TLE from Celestrak for %s: %s", sat["id"], exc)
                    continue

                async with self._pool.acquire() as conn:
                    # Don't insert duplicates of the same epoch
                    existing = await conn.fetchval(
                        "SELECT 1 FROM tle_history WHERE satellite_id = $1 AND epoch = $2",
                        sat["id"], epoch,
                    )
                    if existing:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO tle_history (satellite_id, epoch, tle_line1, tle_line2)
                        VALUES ($1, $2, $3, $4)
                        """,
                        sat["id"], epoch, tle1, tle2,
                    )

                # Recompute passes with the new TLE
                try:
                    await _compute_and_store_passes(self._pool, sat["id"], tle1, tle2)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Pass recompute failed for %s: %s", sat["id"], exc)

                updated += 1
                logger.info("Celestrak: updated %s (NORAD %d, epoch %s)",
                            sat["id"], sat["norad_id"], epoch)

        if updated:
            logger.info("Celestrak cycle: refreshed %d satellite(s)", updated)

    @staticmethod
    def _tle_age_hours(latest_epoch: datetime | None) -> float | None:
        if latest_epoch is None:
            return None
        from datetime import timezone
        delta = datetime.now(timezone.utc) - latest_epoch
        return delta.total_seconds() / 3600.0
