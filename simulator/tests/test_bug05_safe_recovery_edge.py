"""
Bug #5: CubeSat.tick() with safe_recovery_s=0 and fault_probability=1.0
would flip between SAFE and NOMINAL every tick.

Reproduction:
  sat = CubeSat("X", fault_probability=1.0, safe_recovery_s=0)
  sat.force_mode(SatelliteMode.NOMINAL)
  tick() → fault injects SAFE
  next tick() → elapsed >= 0 → auto-recover → NOMINAL
  next tick() → fault injects SAFE
  ... forever

This spams NATS events.fdir.* with rapid SAFE/NOMINAL oscillation.

Fix: enforce a minimum safe_recovery_s so the state is stable for at least
one FDIR cycle.
"""

import time

import pytest

from src.satellite import CubeSat, SatelliteMode


def test_safe_recovery_has_minimum_duration():
    """safe_recovery_s must be enforced to a sane minimum to avoid oscillation."""
    sat = CubeSat("X", safe_recovery_s=0)   # try to force 0
    # After construction, the effective safe_recovery_s should be at least 1 second
    assert sat.safe_recovery_s >= 1.0, (
        f"safe_recovery_s = {sat.safe_recovery_s} — must be >= 1s to prevent "
        "SAFE/NOMINAL oscillation under constant fault injection"
    )


def test_fault_probability_clamped_to_unit_interval():
    """fault_probability > 1 is nonsense; detector should clamp or raise."""
    with pytest.raises(ValueError):
        CubeSat("X", fault_probability=5.0)


def test_negative_fault_probability_rejected():
    with pytest.raises(ValueError):
        CubeSat("X", fault_probability=-0.1)
