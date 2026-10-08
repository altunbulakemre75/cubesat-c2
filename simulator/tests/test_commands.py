"""
The simulated satellite executes uplinked commands and acknowledges them.

Until now nothing consumed commands.<sat>: every command the scheduler
published timed out after 60 s and went 'dead', so the command lifecycle
was never exercised end to end.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.commands import CommandExecutor, run_command_listener
from src.satellite import CubeSat, SatelliteMode


def _sat() -> CubeSat:
    sat = CubeSat("CUBESAT1", fault_probability=0.0)
    sat.force_mode(SatelliteMode.NOMINAL)
    return sat


def _cmd(command_type: str, command_id: str = "c-1", **params: Any) -> dict[str, Any]:
    return {
        "command_id": command_id,
        "satellite_id": "CUBESAT1",
        "command_type": command_type,
        "params": params,
    }


def test_mode_change_switches_mode_and_acks() -> None:
    sat = _sat()
    ack = CommandExecutor(sat).execute(_cmd("mode_change", mode="science"))
    assert ack["ok"] is True
    assert ack["command_id"] == "c-1"
    assert sat.mode == SatelliteMode.SCIENCE


def test_invalid_mode_is_refused() -> None:
    sat = _sat()
    ack = CommandExecutor(sat).execute(_cmd("mode_change", mode="warp"))
    assert ack["ok"] is False
    assert "mode" in ack["error"]
    assert sat.mode == SatelliteMode.NOMINAL


def test_recovery_returns_to_nominal() -> None:
    sat = _sat()
    sat.force_mode(SatelliteMode.SAFE)
    assert CommandExecutor(sat).execute(_cmd("recovery"))["ok"] is True
    assert sat.mode == SatelliteMode.NOMINAL


def test_ping_changes_nothing() -> None:
    sat = _sat()
    assert CommandExecutor(sat).execute(_cmd("ping"))["ok"] is True
    assert sat.mode == SatelliteMode.NOMINAL


def test_unsupported_command_is_nacked() -> None:
    ack = CommandExecutor(_sat()).execute(_cmd("open_pod_bay_doors"))
    assert ack["ok"] is False
    assert "unsupported" in ack["error"]


def test_command_addressed_to_another_satellite_is_refused() -> None:
    cmd = _cmd("ping")
    cmd["satellite_id"] = "CUBESAT2"
    assert CommandExecutor(_sat()).execute(cmd)["ok"] is False


def test_duplicate_command_id_executes_once() -> None:
    # Retries after a lost ACK re-send the same command_id. The satellite
    # must answer again but not act again ("aynı komut iki kez gitse uydu
    # bir kez çalıştırır").
    sat = _sat()
    executor = CommandExecutor(sat)
    first = executor.execute(_cmd("mode_change", mode="safe"))
    sat.force_mode(SatelliteMode.NOMINAL)

    again = executor.execute(_cmd("mode_change", mode="safe"))

    assert again == first
    assert sat.mode == SatelliteMode.NOMINAL


def test_command_without_id_is_rejected() -> None:
    cmd = _cmd("ping")
    del cmd["command_id"]
    with pytest.raises(ValueError):
        CommandExecutor(_sat()).execute(cmd)


class _FakeMsg:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeNats:
    def __init__(self) -> None:
        self.subscriptions: dict[str, Any] = {}

    async def subscribe(self, subject: str, cb: Any) -> None:
        self.subscriptions[subject] = cb


class _FakeJetStream:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, json.loads(payload)))


async def test_listener_acks_on_the_ack_subject() -> None:
    nc, js, sat = _FakeNats(), _FakeJetStream(), _sat()
    await run_command_listener(nc, js, sat)  # type: ignore[arg-type]

    handler = nc.subscriptions["commands.CUBESAT1"]
    await handler(_FakeMsg(json.dumps(_cmd("ping")).encode()))

    assert js.published == [("commands.ack.CUBESAT1", {
        **js.published[0][1], "command_id": "c-1", "ok": True,
    })]


async def test_listener_ignores_garbage() -> None:
    nc, js, sat = _FakeNats(), _FakeJetStream(), _sat()
    await run_command_listener(nc, js, sat)  # type: ignore[arg-type]

    await nc.subscriptions["commands.CUBESAT1"](_FakeMsg(b"not json"))

    assert js.published == []
