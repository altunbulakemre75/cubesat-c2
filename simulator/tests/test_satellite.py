import time

import pytest

from src.satellite import CubeSat, SatelliteMode


def test_initial_mode_is_beacon():
    sat = CubeSat("TEST1")
    assert sat.mode == SatelliteMode.BEACON


def test_tick_returns_correct_satellite_id():
    sat = CubeSat("TEST1")
    tm = sat.tick()
    assert tm.satellite_id == "TEST1"


def test_sequence_increments_monotonically():
    sat = CubeSat("TEST1")
    prev = sat.tick().sequence
    for _ in range(10):
        cur = sat.tick().sequence
        assert cur == prev + 1
        prev = cur


def test_telemetry_values_within_physical_bounds():
    sat = CubeSat("TEST1")
    for _ in range(200):
        tm = sat.tick()
        assert 3.3 <= tm.battery_voltage_v <= 4.2, f"battery out of range: {tm.battery_voltage_v}"
        assert -30.0 <= tm.temperature_obcs_c <= 70.0, f"obcs temp out of range: {tm.temperature_obcs_c}"
        assert -40.0 <= tm.temperature_eps_c <= 60.0, f"eps temp out of range: {tm.temperature_eps_c}"
        assert 0.0 <= tm.solar_power_w <= 7.0, f"solar power out of range: {tm.solar_power_w}"
        assert -120.0 <= tm.rssi_dbm <= -80.0, f"rssi out of range: {tm.rssi_dbm}"
        assert tm.uptime_s >= 0


def test_uptime_is_non_decreasing():
    sat = CubeSat("TEST1")
    prev_uptime = sat.tick().uptime_s
    time.sleep(0.01)
    cur_uptime = sat.tick().uptime_s
    assert cur_uptime >= prev_uptime


def test_mode_transition_beacon_to_deployment():
    sat = CubeSat("TEST1")
    assert sat.mode == SatelliteMode.BEACON
    # Simulate time elapsed past BEACON duration (30s)
    sat._mode_entered_at -= 31.0
    sat.tick()
    assert sat.mode == SatelliteMode.DEPLOYMENT


def test_mode_transition_deployment_to_nominal():
    sat = CubeSat("TEST1")
    sat._mode = SatelliteMode.DEPLOYMENT
    sat._mode_entered_at -= 61.0
    sat.tick()
    assert sat.mode == SatelliteMode.NOMINAL


def test_force_mode():
    sat = CubeSat("TEST1")
    sat.force_mode(SatelliteMode.SAFE)
    assert sat.mode == SatelliteMode.SAFE


def test_safe_mode_auto_recovery():
    sat = CubeSat("TEST1", safe_recovery_s=5.0)
    sat.force_mode(SatelliteMode.SAFE)
    sat._mode_entered_at -= 6.0
    sat.tick()
    assert sat.mode == SatelliteMode.NOMINAL


def test_fault_injection_at_probability_1():
    """With 100% fault probability every tick triggers safe mode."""
    sat = CubeSat("TEST1", fault_probability=1.0, safe_recovery_s=9999.0)
    # Advance past BEACON and DEPLOYMENT first
    sat._mode = SatelliteMode.NOMINAL
    sat._mode_entered_at = time.monotonic()
    sat.tick()
    assert sat.mode == SatelliteMode.SAFE


def test_no_fault_at_probability_0():
    """With 0% fault probability NOMINAL stays NOMINAL for many ticks."""
    sat = CubeSat("TEST1", fault_probability=0.0)
    sat._mode = SatelliteMode.NOMINAL
    sat._mode_entered_at = time.monotonic()
    for _ in range(500):
        sat.tick()
    assert sat.mode == SatelliteMode.NOMINAL


def test_multiple_satellites_are_independent():
    sat1 = CubeSat("SAT1", fault_probability=0.0)
    sat2 = CubeSat("SAT2", fault_probability=1.0, safe_recovery_s=9999.0)
    sat1._mode = SatelliteMode.NOMINAL
    sat2._mode = SatelliteMode.NOMINAL
    sat1._mode_entered_at = sat2._mode_entered_at = time.monotonic()

    sat2.tick()  # fault injected
    sat1.tick()

    assert sat1.mode == SatelliteMode.NOMINAL
    assert sat2.mode == SatelliteMode.SAFE
