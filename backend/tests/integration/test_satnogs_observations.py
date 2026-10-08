"""
SatNOGS observations round-trip through the real database.

The fetcher json.dumps'ed the metadata before handing it to asyncpg,
whose JSONB codec encodes again — so the column held a JSON *string*,
and GET /satnogs/observations failed with a 500 for every satellite that
had observations (found by adding the ISS in a browser smoke test).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
from fastapi.testclient import TestClient

from src.ingestion.satnogs_fetcher import SatnogsTelemetryFetcher
from src.storage.db import _init_connection
from tests.integration.conftest import Db, bearer, login

PW = "observations-password"


def _poll(dsn: str, observations: list[dict[str, Any]]) -> None:
    async def _run() -> None:
        pool = await asyncpg.create_pool(dsn, init=_init_connection, min_size=1, max_size=2)
        try:
            client = MagicMock()
            client.get_recent_observations = AsyncMock(return_value=observations)
            await SatnogsTelemetryFetcher(pool)._poll_one(client, "ISS", 25544)
        finally:
            await pool.close()
    asyncio.run(_run())


def _viewer(client: TestClient, db: Db) -> dict[str, str]:
    db.create_user("viewer", PW, "viewer")
    return bearer(login(client, "viewer", PW)["access_token"])


def test_fetched_observations_are_stored_as_objects_and_listed(
    client: TestClient, db: Db, test_dsn: str,
) -> None:
    db.execute("INSERT INTO satellites (id, name, norad_id) VALUES ('ISS', 'ISS', 25544)")
    _poll(test_dsn, [{
        "id": 15159, "start": "2026-10-08T10:00:00Z", "end": "2026-10-08T10:08:00Z",
        "ground_station": 425, "vetted_status": "good", "demoddata": [], "waterfall": None,
    }])

    assert db.fetchval("SELECT jsonb_typeof(decoded_json) FROM satnogs_observations") == "object"

    resp = client.get("/satnogs/observations", params={"limit": 6}, headers=_viewer(client, db))
    assert resp.status_code == 200
    assert resp.json()[0]["decoded_json"]["observation_id"] == 15159


def test_rows_written_as_json_strings_by_v010_still_list(client: TestClient, db: Db) -> None:
    db.execute("INSERT INTO satellites (id, name, norad_id) VALUES ('ISS', 'ISS', 25544)")
    db.execute(
        "INSERT INTO satnogs_observations "
        "(satellite_id, norad_cat_id, observer, timestamp_utc, decoded_json, app_source) "
        "VALUES ('ISS', 25544, 'GS-1', NOW(), to_jsonb($1::text), 'network')",
        json.dumps({"observation_id": 1}),
    )
    assert db.fetchval("SELECT jsonb_typeof(decoded_json) FROM satnogs_observations") == "string"

    resp = client.get("/satnogs/observations", headers=_viewer(client, db))
    assert resp.status_code == 200
    assert resp.json()[0]["decoded_json"] == {"observation_id": 1}


def test_migration_010_unwraps_string_rows(db: Db) -> None:
    from pathlib import Path

    db.execute("INSERT INTO satellites (id, name, norad_id) VALUES ('ISS', 'ISS', 25544)")
    db.execute(
        "INSERT INTO satnogs_observations "
        "(satellite_id, norad_cat_id, observer, timestamp_utc, decoded_json, app_source) "
        "VALUES ('ISS', 25544, 'GS-1', NOW(), to_jsonb($1::text), 'network')",
        json.dumps({"observation_id": 7}),
    )
    sql = (Path(__file__).parents[2] / "migrations" / "010_satnogs_decoded_json_objects.sql")
    db.execute(sql.read_text(encoding="utf-8"))

    assert db.fetchval("SELECT decoded_json->>'observation_id' FROM satnogs_observations") == "7"
