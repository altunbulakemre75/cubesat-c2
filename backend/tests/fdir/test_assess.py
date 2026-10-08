"""
FDIR health assessment — pure logic, no I/O.

Covers the thresholds and missing-field handling that the old
Redis-mocked tests covered, plus the pass-aware staleness rule: a LEO
satellite that is simply out of view is not a fault, but a pass over our
own station that produced no telemetry is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.fdir.monitor import (
    BATTERY_CRITICAL_V,
    STALE_TELEMETRY_MINUTES,
    TEMP_EPS_CRITICAL_C,
    TEMP_OBCS_CRITICAL_C,
    LastPass,
    Observation,
    assess,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _obs(age_s: float = 5, **values: float | None) -> Observation:
    base: dict[str, float | None] = {
        "battery_voltage_v": 3.9, "temperature_obcs_c": 30.0, "temperature_eps_c": 25.0,
    }
    base.update(values)
    return Observation(time=NOW - timedelta(seconds=age_s), **base)  # type: ignore[arg-type]


def _pass(ended_min_ago: float, length_min: float = 8) -> LastPass:
    los = NOW - timedelta(minutes=ended_min_ago)
    return LastPass(station="Club GS", aos=los - timedelta(minutes=length_min), los=los)


# ── thresholds ───────────────────────────────────────────────────────────────

def test_healthy_telemetry_is_quiet() -> None:
    assert assess(_obs(), None, has_passes=False, now=NOW).warnings == []


def test_low_battery_is_critical() -> None:
    w = assess(_obs(battery_voltage_v=BATTERY_CRITICAL_V - 0.1), None, False, NOW).warnings
    assert any("Battery critical" in x for x in w)


def test_battery_exactly_at_threshold_is_fine() -> None:
    assert assess(_obs(battery_voltage_v=BATTERY_CRITICAL_V), None, False, NOW).warnings == []


def test_zero_battery_is_critical_not_missing() -> None:
    w = assess(_obs(battery_voltage_v=0.0), None, False, NOW).warnings
    assert any("Battery critical" in x for x in w)
    assert not any("Missing" in x for x in w)


def test_hot_obc_and_eps_are_reported_together() -> None:
    w = assess(_obs(temperature_obcs_c=TEMP_OBCS_CRITICAL_C + 1,
                    temperature_eps_c=TEMP_EPS_CRITICAL_C + 1), None, False, NOW).warnings
    assert any("OBC temperature critical" in x for x in w)
    assert any("EPS temperature critical" in x for x in w)


def test_missing_field_is_a_warning_not_a_silent_pass() -> None:
    w = assess(_obs(battery_voltage_v=None), None, False, NOW).warnings
    assert any("Missing battery_voltage_v" in x for x in w)


# ── staleness: continuous link (no pass predictions, e.g. the simulator) ─────

def test_continuous_link_goes_stale_after_the_threshold() -> None:
    old = _obs(age_s=(STALE_TELEMETRY_MINUTES + 1) * 60)
    assert any("stale" in x for x in assess(old, None, has_passes=False, now=NOW).warnings)


def test_continuous_link_within_threshold_is_fine() -> None:
    recent = _obs(age_s=(STALE_TELEMETRY_MINUTES - 1) * 60)
    assert assess(recent, None, has_passes=False, now=NOW).warnings == []


# ── staleness: orbiting satellite tracked by passes ──────────────────────────

def test_out_of_view_between_passes_is_not_a_fault() -> None:
    # Last heard during the previous pass, 40 min ago; nothing since because
    # the satellite is over the other side of the planet.
    heard_during_pass = _obs(age_s=45 * 60)
    result = assess(heard_during_pass, _pass(ended_min_ago=40), has_passes=True, now=NOW)
    assert result.warnings == []


def test_pass_without_any_telemetry_is_a_fault() -> None:
    heard_long_before = _obs(age_s=5 * 3600)
    result = assess(heard_long_before, _pass(ended_min_ago=10), has_passes=True, now=NOW)
    assert any("No telemetry during the pass over Club GS" in x for x in result.warnings)


def test_never_heard_satellite_with_a_missed_pass_is_a_fault() -> None:
    result = assess(None, _pass(ended_min_ago=10), has_passes=True, now=NOW)
    assert any("No telemetry during the pass" in x for x in result.warnings)


def test_never_heard_satellite_without_passes_raises_nothing() -> None:
    # Nothing to compare against: registering a satellite isn't a fault.
    assert assess(None, None, has_passes=False, now=NOW).warnings == []


# ── evidence time (drives de-duplication) ────────────────────────────────────

def test_evidence_time_is_the_observation_for_value_faults() -> None:
    obs = _obs(age_s=30, battery_voltage_v=3.0)
    assert assess(obs, None, False, NOW).evidence_at == obs.time


def test_evidence_time_is_the_los_for_a_missed_pass() -> None:
    last = _pass(ended_min_ago=10)
    assert assess(_obs(age_s=5 * 3600), last, True, NOW).evidence_at == last.los
