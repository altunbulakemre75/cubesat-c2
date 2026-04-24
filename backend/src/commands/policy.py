"""
Policy engine: decides whether a command is allowed given the satellite's current mode.

Rules are static (table-driven). No ML. No external calls.
"""

from src.ingestion.models import SatelliteMode

# Which command categories are allowed in each satellite mode.
# The special value "*" means all normal commands are allowed.
_POLICY: dict[SatelliteMode, set[str] | str] = {
    SatelliteMode.BEACON: {"mode_change"},
    SatelliteMode.DEPLOYMENT: {"mode_change", "deploy_antenna", "deploy_solar_panel", "beacon_set"},
    SatelliteMode.NOMINAL: "*",    # all commands allowed
    SatelliteMode.SCIENCE: {"abort", "mode_change", "science_stop"},
    SatelliteMode.SAFE: {"recovery", "mode_change", "diagnostic", "reset", "ping"},
}

# Commands that always require admin role regardless of mode
ADMIN_ONLY_COMMANDS: frozenset[str] = frozenset({
    "engine_fire",
    "separation",
    "format_storage",
    "factory_reset",
})

# Commands that require TWO admin approvals
TWO_ADMIN_COMMANDS: frozenset[str] = frozenset({
    "separation",
    "factory_reset",
})


class PolicyDecision:
    __slots__ = ("allowed", "reason")

    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason

    def __bool__(self) -> bool:
        return self.allowed


def evaluate(command_type: str, satellite_mode: SatelliteMode) -> PolicyDecision:
    """
    Return whether command_type is permitted given satellite_mode.

    Does NOT check user role — that is RBAC's job (src/api/rbac.py).
    """
    allowed = _POLICY.get(satellite_mode)

    if allowed is None:
        return PolicyDecision(False, f"Unknown satellite mode: {satellite_mode}")

    if allowed == "*":
        return PolicyDecision(True)

    assert isinstance(allowed, set)
    if command_type in allowed:
        return PolicyDecision(True)

    return PolicyDecision(
        False,
        f"Command '{command_type}' is not permitted when satellite is in '{satellite_mode.value}' mode. "
        f"Allowed commands: {sorted(allowed)}",
    )
