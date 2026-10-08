"""
Uplink command execution for the simulated satellite.

The simulator subscribes to commands.<satellite_id> (the C2 uplink queue),
executes each command against the CubeSat model and publishes an ACK on
commands.ack.<satellite_id>:

    {"command_id": "...", "ok": true|false, "error": "...", "executed_at": "..."}

Execution is idempotent per command_id: a retry after a lost ACK gets the
original answer again without the command running twice — the property
the C2 retry logic relies on.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from src.satellite import CubeSat, SatelliteMode

logger = logging.getLogger(__name__)

_HISTORY = 1000  # remembered command ids per satellite


class _Subscriber(Protocol):
    async def subscribe(self, subject: str, cb: Callable[[Any], Awaitable[None]]) -> Any: ...


class _Publisher(Protocol):
    async def publish(self, subject: str, payload: bytes) -> Any: ...


class CommandExecutor:
    def __init__(self, satellite: CubeSat) -> None:
        self._sat = satellite
        self._done: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        command_id = command.get("command_id")
        if not command_id:
            raise ValueError("command without command_id cannot be acknowledged")

        if command_id in self._done:
            logger.info("Duplicate command %s — re-sending ACK, not re-executing", command_id)
            return self._done[command_id]

        error = self._run(command)
        ack: dict[str, Any] = {
            "command_id": command_id,
            "ok": error is None,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
        if error is not None:
            ack["error"] = error

        self._done[command_id] = ack
        while len(self._done) > _HISTORY:
            self._done.popitem(last=False)
        return ack

    def _run(self, command: dict[str, Any]) -> str | None:
        """Apply the command; return an error string or None on success."""
        if command.get("satellite_id") != self._sat.satellite_id:
            return f"addressed to {command.get('satellite_id')}, not {self._sat.satellite_id}"

        ctype = command.get("command_type")
        params = command.get("params") or {}

        if ctype == "mode_change":
            try:
                target = SatelliteMode(params.get("mode"))
            except ValueError:
                return f"invalid mode {params.get('mode')!r}"
            self._sat.force_mode(target)
        elif ctype in ("recovery", "abort", "science_stop"):
            self._sat.force_mode(SatelliteMode.NOMINAL)
        elif ctype == "reset":
            self._sat.force_mode(SatelliteMode.BEACON)
        elif ctype in ("ping", "diagnostic", "beacon_set", "deploy_antenna", "deploy_solar_panel"):
            pass  # acknowledged; no state change modelled
        else:
            return f"unsupported command type {ctype!r}"

        logger.info("Executed %s on %s", ctype, self._sat.satellite_id)
        return None


async def run_command_listener(
    nc: _Subscriber, js: _Publisher, satellite: CubeSat,
) -> CommandExecutor:
    """Subscribe to this satellite's uplink queue and ACK every command."""
    executor = CommandExecutor(satellite)
    sat_id = satellite.satellite_id

    async def _on_command(msg: Any) -> None:
        try:
            command = json.loads(msg.data)
            ack = executor.execute(command)
        except (ValueError, TypeError) as exc:
            logger.warning("Ignoring malformed command for %s: %s", sat_id, exc)
            return
        try:
            await js.publish(f"commands.ack.{sat_id}", json.dumps(ack).encode())
        except Exception as exc:  # noqa: BLE001 — a lost ACK is recovered by the C2 retry
            logger.error("ACK publish failed for %s: %s", ack["command_id"], exc)

    await nc.subscribe(f"commands.{sat_id}", cb=_on_command)
    logger.info("Listening for uplink commands on commands.%s", sat_id)
    return executor
