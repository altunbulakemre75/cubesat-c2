"""
SatNOGS observations fetcher.

Background coroutine that periodically polls SatNOGS for the most recent
ground-station observation sessions of every satellite in our catalog
that has a NORAD ID, and persists them to satnogs_observations.

This is how the system surfaces REAL satellite activity without owning a
radio: amateurs around the world schedule receive sessions with their
RTL-SDRs, and SatNOGS Network records when each session ran, who ran it,
and what came out of the demodulator.

Why network/observations and not db/telemetry:
- network/observations works anonymously (no token needed).
- db/telemetry returns 401 without an API token, which most self-hosters
  do not have. If you set SATNOGS_API_TOKEN in .env we will additionally
  attempt db/telemetry to enrich frames with their decoded JSON.

Design notes:
- Per-satellite poll interval defaults to 15 minutes; aggregate rate stays
  well under SatNOGS's 100 req/min limit even for hundreds of satellites.
- Dedupe is done at the DB (UNIQUE constraint on
  (norad_cat_id, timestamp_utc, observer)) — we always INSERT
  ON CONFLICT DO NOTHING, so re-fetching the same window is cheap.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import asyncpg

from src.config import settings
from src.ingestion.satnogs_client import SatNOGSClient

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 15 * 60          # 15 minutes per satellite
INITIAL_DELAY_S = 30                # let the rest of the app settle first
MAX_FRAMES_PER_POLL = 25            # cap per satellite per poll


def _parse_iso(value: str | None) -> datetime | None:
    """SatNOGS sends timestamps with a trailing 'Z'. asyncpg's TIMESTAMPTZ
    binding requires a real datetime object (not an ISO string), so parse
    it ourselves. Returning None lets the caller skip this row."""
    if not value:
        return None
    s = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _coerce_decoded(decoded: object) -> dict | None:
    """SatNOGS DB sends 'decoded' as either a JSON string or plain text.
    Always store something useful — never silently drop the field."""
    if decoded is None or decoded == "":
        return None
    if isinstance(decoded, dict):
        return decoded
    if isinstance(decoded, str):
        try:
            parsed = json.loads(decoded)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except ValueError:
            return {"raw": decoded}
    return {"value": str(decoded)}


class SatnogsTelemetryFetcher:
    def __init__(
        self,
        pool: asyncpg.Pool,
        poll_interval_s: float = POLL_INTERVAL_S,
        max_frames_per_poll: int = MAX_FRAMES_PER_POLL,
    ) -> None:
        self._pool = pool
        self._poll_interval = poll_interval_s
        self._max_frames = max_frames_per_poll
        self.persisted_total = 0
        self.errors_total = 0

    async def run(self) -> None:
        await asyncio.sleep(INITIAL_DELAY_S)
        logger.info(
            "SatNOGS telemetry fetcher started (interval=%.0fs, max=%d frames/poll)",
            self._poll_interval, self._max_frames,
        )
        while True:
            try:
                await self._poll_all_satellites()
            except Exception as exc:  # noqa: BLE001
                self.errors_total += 1
                logger.error("SatNOGS fetcher cycle error: %s", exc, exc_info=True)
            await asyncio.sleep(self._poll_interval)

    async def _poll_all_satellites(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, norad_id FROM satellites "
                "WHERE norad_id IS NOT NULL AND active = TRUE"
            )
        if not rows:
            logger.debug("SatNOGS fetcher: no satellites with NORAD ID — skipping")
            return

        token = getattr(settings, "satnogs_api_token", None)
        client = SatNOGSClient(api_token=token)
        try:
            for row in rows:
                try:
                    await self._poll_one(client, row["id"], row["norad_id"])
                except Exception as exc:  # noqa: BLE001
                    self.errors_total += 1
                    logger.warning(
                        "SatNOGS poll failed | sat=%s norad=%s: %s",
                        row["id"], row["norad_id"], exc,
                    )
        finally:
            await client.close()

    async def _poll_one(
        self,
        client: SatNOGSClient,
        satellite_id: str,
        norad_id: int,
    ) -> None:
        # Anonymous-friendly: pull observation metadata, not raw frames.
        observations = await client.get_recent_observations(
            norad_id, limit=self._max_frames,
        )
        if not observations:
            return

        rows_to_insert = []
        for o in observations:
            try:
                ts = _parse_iso(o.get("start") or o.get("timestamp"))
                if ts is None:
                    continue
                gs = o.get("ground_station")
                observer = f"GS-{gs}" if gs else (o.get("observer") or "")
                # Carry the whole observation record under decoded_json so
                # the UI / future decoders can use end time, vetted_status,
                # demoddata file URLs, etc.
                meta = {
                    "observation_id": o.get("id"),
                    "ground_station": gs,
                    "vetted_status": o.get("vetted_status"),
                    "end": o.get("end"),
                    "demoddata": o.get("demoddata"),
                    "waterfall": o.get("waterfall"),
                }
                rows_to_insert.append((
                    satellite_id,
                    norad_id,
                    observer,
                    o.get("transmitter"),
                    ts,
                    None,                     # network endpoint has no raw frame
                    json.dumps(meta),
                    "network",
                ))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping malformed SatNOGS observation for %s: %s", satellite_id, exc)
                continue

        if not rows_to_insert:
            return

        async with self._pool.acquire() as conn:
            inserted_before = await conn.fetchval(
                "SELECT COUNT(*) FROM satnogs_observations WHERE norad_cat_id = $1",
                norad_id,
            )
            await conn.executemany(
                """
                INSERT INTO satnogs_observations
                  (satellite_id, norad_cat_id, observer, transmitter,
                   timestamp_utc, frame_hex, decoded_json, app_source)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                ON CONFLICT (norad_cat_id, timestamp_utc, observer) DO NOTHING
                """,
                rows_to_insert,
            )
            inserted_after = await conn.fetchval(
                "SELECT COUNT(*) FROM satnogs_observations WHERE norad_cat_id = $1",
                norad_id,
            )
        added = inserted_after - inserted_before
        if added:
            self.persisted_total += added
            logger.info(
                "SatNOGS | sat=%s norad=%s ingested=%d (deduped %d)",
                satellite_id, norad_id, added, len(rows_to_insert) - added,
            )
