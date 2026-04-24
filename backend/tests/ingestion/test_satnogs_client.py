"""
Unit tests for SatNOGSClient — all HTTP calls are mocked.
"""

import pytest
import httpx
import respx

from src.ingestion.satnogs_client import SatNOGSClient

NETWORK_BASE = "https://network.satnogs.org/api"
DB_BASE = "https://db.satnogs.org/api"


@pytest.mark.asyncio
@respx.mock
async def test_get_stations_returns_list():
    respx.get(f"{NETWORK_BASE}/stations/").mock(return_value=httpx.Response(
        200,
        json=[
            {"id": 1, "name": "Ankara GS", "lat": 39.9, "lng": 32.8, "altitude": 938, "status": "Online"},
            {"id": 2, "name": "Istanbul GS", "lat": 41.0, "lng": 28.9, "altitude": 100, "status": "Online"},
        ],
    ))
    client = SatNOGSClient()
    stations = await client.get_stations()
    await client.close()
    assert len(stations) == 2
    assert stations[0]["name"] == "Ankara GS"


@pytest.mark.asyncio
@respx.mock
async def test_get_tle_returns_latest():
    respx.get(f"{DB_BASE}/tle/").mock(return_value=httpx.Response(
        200,
        json=[{
            "tle0": "ISS (ZARYA)",
            "tle1": "1 25544U 98067A   26024.50000000  .00016717  00000-0  30677-3 0  9993",
            "tle2": "2 25544  51.6400 337.6095 0001599  90.9526 269.1851 15.49815889 34066",
            "updated": "2026-01-24T12:00:00Z",
        }],
    ))
    client = SatNOGSClient()
    tle = await client.get_tle(25544)
    await client.close()
    assert tle is not None
    assert "25544" in tle["tle1"]


@pytest.mark.asyncio
@respx.mock
async def test_get_tle_returns_none_when_empty():
    respx.get(f"{DB_BASE}/tle/").mock(return_value=httpx.Response(200, json=[]))
    client = SatNOGSClient()
    tle = await client.get_tle(99999)
    await client.close()
    assert tle is None


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_429():
    route = respx.get(f"{NETWORK_BASE}/stations/")
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(200, json=[{"id": 1, "name": "Test", "lat": 0, "lng": 0}]),
    ]
    client = SatNOGSClient()
    # Should retry and succeed
    stations = await client.get_stations()
    await client.close()
    assert len(stations) == 1


@pytest.mark.asyncio
@respx.mock
async def test_single_page_response_parsed():
    """Verifies that a paginated dict response with 'results' key is handled."""
    respx.get(f"{NETWORK_BASE}/stations/").mock(return_value=httpx.Response(
        200,
        json={
            "count": 2,
            "next": None,   # no second page — tests result extraction
            "results": [
                {"id": 1, "name": "Station 1"},
                {"id": 2, "name": "Station 2"},
            ],
        },
    ))
    client = SatNOGSClient()
    stations = await client.get_stations()
    await client.close()
    assert len(stations) == 2
    assert stations[0]["id"] == 1
    assert stations[1]["id"] == 2
