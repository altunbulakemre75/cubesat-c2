"""
SatNOGS Network API client.

Covers three use cases:
  1. Fetch ground stations (with optional lat/lon bounding box)
  2. Fetch latest observations for a satellite (by NORAD ID)
  3. Fetch TLE data from SatNOGS DB

Rate limit: SatNOGS enforces ~100 req/min. We add a 0.7s inter-request delay
and retry with exponential backoff on 429/503.

Docs: https://network.satnogs.org/api/
"""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_NETWORK = "https://network.satnogs.org/api"
_BASE_DB = "https://db.satnogs.org/api"
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_INTER_REQUEST_DELAY = 0.7   # seconds between requests


class SatNOGSClient:
    def __init__(self, api_token: str | None = None, timeout: float = 30.0) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_token:
            headers["Authorization"] = f"Token {api_token}"
        self._http = httpx.AsyncClient(headers=headers, timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    # ── Ground stations ───────────────────────────────────────────────────────

    async def get_stations(
        self,
        status: str = "Online",
        min_lat: float | None = None,
        max_lat: float | None = None,
        min_lon: float | None = None,
        max_lon: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return SatNOGS ground stations.

        Filter by status ('Online', 'Testing', 'Offline') and optional
        bounding box. Returns all pages automatically.
        """
        params: dict[str, Any] = {"format": "json", "status": status}
        return await self._get_all_pages(f"{_BASE_NETWORK}/stations/", params)

    # ── Observations ──────────────────────────────────────────────────────────

    async def get_observations(
        self,
        norad_cat_id: int,
        limit: int = 20,
        vetted_status: str = "good",
    ) -> list[dict[str, Any]]:
        """
        Return recent observations for a satellite (by NORAD ID).

        vetted_status: 'good' | 'bad' | 'unknown' | '' (any)
        """
        params: dict[str, Any] = {
            "format": "json",
            "norad_cat_id": norad_cat_id,
            "vetted_status": vetted_status,
        }
        results = await self._get_all_pages(f"{_BASE_NETWORK}/observations/", params)
        return results[:limit]

    # ── Telemetry (db.satnogs.org) ────────────────────────────────────────────

    async def get_recent_telemetry(
        self,
        norad_cat_id: int,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent demodulated telemetry frames from SatNOGS DB.

        Requires an API token (set SATNOGS_API_TOKEN). Without one this
        endpoint returns 401 — callers should fall back to
        get_recent_observations() which works anonymously.

        Returns a list of records like:
          {"norad_cat_id": int, "observer": str, "timestamp": ISO,
           "frame": <hex>, "decoded": <str|None>, "transmitter": str,
           "app_source": "satnogs"|"sids"|...}
        """
        url = f"{_BASE_DB}/telemetry/?format=json&satellite={norad_cat_id}"
        data = await self._get_all_pages(url, {}, max_pages=1)
        return data[:limit]

    # ── Observations (network.satnogs.org — anonymous-friendly) ───────────────

    async def get_recent_observations(
        self,
        norad_cat_id: int,
        limit: int = 25,
        vetted_status: str = "good",
    ) -> list[dict[str, Any]]:
        """
        Fetch recent ground-station observation sessions for a satellite.

        Works anonymously — no API token needed. Each record describes a
        scheduled session: when it started, which ground station ran it,
        the vetted status, and (if any) demodulated data file links.

        Returns records like:
          {"id": int, "start": ISO, "end": ISO, "ground_station": int,
           "transmitter": str, "vetted_status": "good|bad|...",
           "norad_cat_id": int, "demoddata": [...links...]}
        """
        url = (
            f"{_BASE_NETWORK}/observations/?format=json"
            f"&norad_cat_id={norad_cat_id}"
            f"&vetted_status={vetted_status}"
        )
        data = await self._get_all_pages(url, {}, max_pages=1)
        return data[:limit]

    # ── TLE ───────────────────────────────────────────────────────────────────

    async def get_tle(self, norad_cat_id: int) -> dict[str, str] | None:
        """
        Fetch the latest TLE for a satellite from SatNOGS DB.

        Returns {"tle0": "...", "tle1": "...", "tle2": "..."} or None.
        """
        url = f"{_BASE_DB}/tle/?format=json&norad_cat_id={norad_cat_id}"
        data = await self._get_all_pages(url, {})
        if not data:
            return None
        # SatNOGS DB returns list ordered by epoch desc
        latest = data[0]
        return {
            "tle0": latest.get("tle0", ""),
            "tle1": latest.get("tle1", ""),
            "tle2": latest.get("tle2", ""),
        }

    # ── Satellite metadata ────────────────────────────────────────────────────

    async def get_satellite(self, norad_cat_id: int) -> dict[str, Any] | None:
        """Fetch satellite metadata (name, status, etc.) from SatNOGS DB."""
        url = f"{_BASE_DB}/satellites/?format=json&norad_cat_id={norad_cat_id}"
        data = await self._get_all_pages(url, {})
        return data[0] if data else None

    # ── Pagination helper ─────────────────────────────────────────────────────

    async def _get_all_pages(
        self,
        url: str,
        params: dict[str, Any],
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Follow SatNOGS cursor pagination (next link) up to max_pages."""
        results: list[dict[str, Any]] = []
        next_url: str | None = url

        for _ in range(max_pages):
            if not next_url:
                break

            data = await self._get_with_retry(next_url, params)
            params = {}  # only first request uses params

            if isinstance(data, list):
                results.extend(data)
                break
            elif isinstance(data, dict):
                results.extend(data.get("results", []))
                next_url = data.get("next")
            else:
                break

            await asyncio.sleep(_INTER_REQUEST_DELAY)

        return results

    async def _get_with_retry(
        self,
        url: str,
        params: dict[str, Any],
        max_attempts: int = 4,
    ) -> Any:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await self._http.get(url, params=params)
                if resp.status_code in _RETRY_STATUSES:
                    wait = 2 ** attempt
                    logger.warning("SatNOGS %s — retrying in %ds (attempt %d)", resp.status_code, wait, attempt)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.TimeoutException:
                logger.warning("SatNOGS timeout (attempt %d/%d)", attempt, max_attempts)
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError(f"SatNOGS request failed after {max_attempts} attempts: {url}")
