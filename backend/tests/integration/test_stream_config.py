"""The JetStream stream has a retention limit.

v0.1.0 created it with subjects only — no max_age — so the NATS volume
grew without bound (telemetry at 1 Hz per satellite, forever). The
roadmap specifies 7 days.
"""

from __future__ import annotations

import os

import nats
import pytest
from nats.js.api import StreamConfig

from src.ingestion.service import STREAM_MAX_AGE_S, ensure_stream

URL = os.environ.get("TEST_NATS_URL")
BACKEND_PW = os.environ.get("TEST_NATS_BACKEND_PASSWORD", "")

pytestmark = pytest.mark.skipif(not URL, reason="TEST_NATS_URL not set")


async def test_new_stream_expires_messages_after_seven_days() -> None:
    nc = await nats.connect(URL, user="backend", password=BACKEND_PW)
    try:
        js = nc.jetstream()
        try:
            await js.delete_stream("cubesat")
        except Exception:  # noqa: BLE001
            pass
        await ensure_stream(js)
        info = await js.stream_info("cubesat")
        assert info.config.max_age == STREAM_MAX_AGE_S == 7 * 24 * 3600
    finally:
        await nc.close()


async def test_existing_unbounded_stream_gets_the_limit() -> None:
    nc = await nats.connect(URL, user="backend", password=BACKEND_PW)
    try:
        js = nc.jetstream()
        try:
            await js.delete_stream("cubesat")
        except Exception:  # noqa: BLE001
            pass
        # As created by v0.1.0: subjects only.
        await js.add_stream(StreamConfig(name="cubesat", subjects=[
            "telemetry.raw.*", "telemetry.canonical.*", "commands.>", "events.>",
        ]))
        await ensure_stream(js)
        info = await js.stream_info("cubesat")
        assert info.config.max_age == STREAM_MAX_AGE_S
    finally:
        await nc.close()
