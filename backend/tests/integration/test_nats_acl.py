"""
NATS permissions — runs against a real nats-server using the repo's
deployment/nats/nats-server.conf.

Regression tests for Dream Security report #4 (unauthenticated NATS):
injection, exfiltration and command-ACK forgery straight on the bus.

    docker run -d --name cubesat-testnats -p 127.0.0.1:54222:4222 \
        -v $PWD/deployment/nats:/etc/nats:ro \
        -e NATS_BACKEND_PASSWORD=backend-test \
        -e NATS_GROUNDSTATION_PASSWORD=gs-test \
        nats:alpine -c /etc/nats/nats-server.conf
    TEST_NATS_URL=nats://127.0.0.1:54222 \
    TEST_NATS_BACKEND_PASSWORD=backend-test \
    TEST_NATS_GROUNDSTATION_PASSWORD=gs-test pytest tests/integration/test_nats_acl.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import nats
import pytest
from nats.aio.client import Client
from nats.errors import NoServersError

from src.ingestion.service import ensure_stream

URL = os.environ.get("TEST_NATS_URL")
BACKEND_PW = os.environ.get("TEST_NATS_BACKEND_PASSWORD", "")
GS_PW = os.environ.get("TEST_NATS_GROUNDSTATION_PASSWORD", "")

pytestmark = pytest.mark.skipif(not URL, reason="TEST_NATS_URL not set")

QUIET_S = 0.5  # how long "nothing arrives" has to hold


async def _connect(user: str, password: str, errors: list[str] | None = None) -> Client:
    async def _on_error(exc: Exception) -> None:
        if errors is not None:
            errors.append(str(exc))

    return await nats.connect(
        URL, user=user, password=password, error_cb=_on_error,
        inbox_prefix="_INBOX_gs" if user == "groundstation" else "_INBOX",
        allow_reconnect=False, connect_timeout=2,
    )


@pytest.fixture
async def backend() -> AsyncIterator[Client]:
    nc = await _connect("backend", BACKEND_PW)
    yield nc
    await nc.close()


@pytest.fixture
async def gs_errors() -> list[str]:
    return []


@pytest.fixture
async def groundstation(gs_errors: list[str]) -> AsyncIterator[Client]:
    nc = await _connect("groundstation", GS_PW, gs_errors)
    yield nc
    await nc.close()


async def _received(nc: Client, subject: str) -> list[bytes]:
    got: list[bytes] = []

    async def _cb(msg: nats.aio.msg.Msg) -> None:
        got.append(msg.data)

    await nc.subscribe(subject, cb=_cb)
    await nc.flush()
    return got


@pytest.fixture
async def stream(backend: Client) -> AsyncIterator[str]:
    """The production stream, created the way the backend creates it."""
    await ensure_stream(backend.jetstream())
    yield "cubesat"


async def test_anonymous_clients_are_refused() -> None:
    with pytest.raises((NoServersError, nats.errors.Error, OSError)):
        await nats.connect(URL, allow_reconnect=False, connect_timeout=2, max_reconnect_attempts=0)


async def test_wrong_password_is_refused() -> None:
    with pytest.raises((NoServersError, nats.errors.Error, OSError)):
        await nats.connect(URL, user="groundstation", password="guess",
                           allow_reconnect=False, connect_timeout=2, max_reconnect_attempts=0)


async def test_groundstation_can_hand_in_raw_frames_via_jetstream(
    groundstation: Client, stream: str,
) -> None:
    ack = await groundstation.jetstream().publish("telemetry.raw.acltest", b"frame")
    assert ack.stream == stream


async def test_groundstation_can_send_command_acks(
    groundstation: Client, stream: str,
) -> None:
    ack = await groundstation.jetstream().publish("commands.ack.acltest", b"{}")
    assert ack.stream == stream


async def test_groundstation_receives_the_uplink_queue(
    backend: Client, groundstation: Client,
) -> None:
    got = await _received(groundstation, "commands.SAT1")
    await backend.publish("commands.SAT1", b"cmd")
    await backend.flush()
    await asyncio.sleep(QUIET_S)
    assert got == [b"cmd"]


@pytest.mark.parametrize("subject", [
    "commands.SAT1",               # forged uplink command
    "telemetry.canonical.SAT1",    # bypass ingestion validation
    "events.fdir.SAT1",            # fake FDIR alert
])
async def test_groundstation_cannot_publish_outside_its_lane(
    backend: Client, groundstation: Client, gs_errors: list[str], subject: str,
) -> None:
    got = await _received(backend, subject)
    await groundstation.publish(subject, b"forged")
    await groundstation.flush()
    await asyncio.sleep(QUIET_S)
    assert got == []
    assert any("permissions violation" in e.lower() for e in gs_errors)


@pytest.mark.parametrize("subject", ["telemetry.canonical.>", "events.>", "commands.ack.>"])
async def test_groundstation_cannot_read_telemetry_or_events(
    backend: Client, groundstation: Client, gs_errors: list[str], subject: str,
) -> None:
    got = await _received(groundstation, subject)
    await backend.publish(subject.replace(">", "SAT1"), b"secret")
    await backend.flush()
    await asyncio.sleep(QUIET_S)
    assert got == []
    assert any("permissions violation" in e.lower() for e in gs_errors)


async def test_groundstation_cannot_use_the_jetstream_api(groundstation: Client) -> None:
    with pytest.raises(Exception):  # noqa: B017 — permission denial surfaces as a timeout/error
        await groundstation.jetstream(timeout=1).stream_info("cubesat")
