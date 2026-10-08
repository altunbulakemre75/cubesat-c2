"""A raw frame is only acknowledged once its canonical form is on the bus.

v0.1.0 acked the raw message in a `finally`, so a failed publish of the
canonical telemetry silently lost the frame."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.ingestion.service import IngestionService


def _frame() -> bytes:
    def cs(c: str, last: bool) -> bytes:
        return bytes(ord(x) << 1 for x in c.ljust(6)[:6]) + bytes([0x60 | (1 if last else 0)])
    payload = {
        "satellite_id": "CUBESAT1", "sequence": 1, "battery_voltage_v": 3.9,
        "temperature_obcs_c": 20.0, "temperature_eps_c": 18.0, "solar_power_w": 1.0,
        "rssi_dbm": -90.0, "uptime_s": 10, "mode": "nominal",
    }
    return cs("GROUND", False) + cs("CUBSAT", True) + b"\x03\xf0" + json.dumps(payload).encode()


def _msg() -> MagicMock:
    msg = MagicMock()
    msg.data = _frame()
    msg.subject = "telemetry.raw.CUBESAT1"
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    return msg


async def _handle(js: MagicMock, msg: MagicMock) -> None:
    with patch("src.ingestion.service.SatelliteRegistry.is_registered",
               new=AsyncMock(return_value=True)):
        service = IngestionService(js, pool=MagicMock())
        await service._handle(msg)


async def test_failed_canonical_publish_redelivers_the_raw_frame() -> None:
    js = MagicMock()
    js.publish = AsyncMock(side_effect=TimeoutError("no response from stream"))
    msg = _msg()

    await _handle(js, msg)

    msg.nak.assert_awaited_once()
    msg.ack.assert_not_called()


async def test_successful_publish_acks_the_raw_frame() -> None:
    js = MagicMock()
    js.publish = AsyncMock()
    msg = _msg()

    await _handle(js, msg)

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_called()
