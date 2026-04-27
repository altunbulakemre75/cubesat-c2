import random

from src.anomaly.detector import AnomalyDetector, WINDOW_SIZE

_RNG = random.Random(42)


def _noisy_baseline(center: float, noise: float, n: int = 30) -> list[float]:
    """Realistic baseline with Gaussian noise."""
    return [center + _RNG.gauss(0, noise) for _ in range(n)]


def _feed_n(detector, sat_id, param, values):
    events = []
    for v in values:
        events.extend(detector.feed(sat_id, {param: v}))
    return events


def test_no_anomaly_during_baseline_buildup():
    det = AnomalyDetector()
    # Fewer than 10 values → has_baseline is False → no events
    events = _feed_n(det, "SAT1", "battery_voltage_v", [3.9] * 9)
    assert events == []


def test_normal_values_within_noise_produce_no_anomaly():
    det = AnomalyDetector()
    baseline = _noisy_baseline(3.9, noise=0.05, n=30)  # ±50mV realistic noise
    _feed_n(det, "SAT1", "battery_voltage_v", baseline)
    # Values within 1σ of baseline should not trigger
    normal = [3.9 + _RNG.gauss(0, 0.03) for _ in range(10)]
    events = _feed_n(det, "SAT1", "battery_voltage_v", normal)
    # May get occasional warning but not critical for values within ~1σ
    critical = [e for e in events if e.severity == "critical"]
    assert len(critical) == 0


def test_extreme_outlier_triggers_critical():
    det = AnomalyDetector()
    baseline = _noisy_baseline(3.9, noise=0.05, n=30)
    _feed_n(det, "SAT1", "battery_voltage_v", baseline)
    # 99.0V is thousands of σ away
    events = det.feed("SAT1", {"battery_voltage_v": 99.0})
    assert any(e.severity == "critical" for e in events)


def test_moderate_outlier_triggers_warning_or_critical():
    det = AnomalyDetector()
    baseline = _noisy_baseline(25.0, noise=0.3, n=30)  # ±0.3°C noise
    _feed_n(det, "SAT1", "temperature_obcs_c", baseline)
    # Value 5σ away from mean=25: 25 + 5*0.3 = 26.5
    events = det.feed("SAT1", {"temperature_obcs_c": 26.5})
    assert any(e.severity in ("warning", "critical") for e in events)


def test_different_satellites_tracked_independently():
    det = AnomalyDetector()
    baseline = _noisy_baseline(3.9, noise=0.05, n=30)
    _feed_n(det, "SAT1", "battery_voltage_v", baseline)
    _feed_n(det, "SAT2", "battery_voltage_v", list(baseline))

    events_sat1 = det.feed("SAT1", {"battery_voltage_v": 99.0})
    events_sat2 = det.feed("SAT2", {"battery_voltage_v": 3.9})

    assert any(e.satellite_id == "SAT1" for e in events_sat1)
    assert all(e.satellite_id != "SAT2" for e in events_sat2)


def test_anomaly_event_has_correct_fields():
    det = AnomalyDetector()
    baseline = _noisy_baseline(3.9, noise=0.05, n=30)
    _feed_n(det, "SAT1", "battery_voltage_v", baseline)
    events = det.feed("SAT1", {"battery_voltage_v": 99.0})
    assert events
    e = events[0]
    assert e.satellite_id == "SAT1"
    assert e.parameter == "battery_voltage_v"
    assert e.value == 99.0
    assert e.z_score > 0
    assert e.detected_at is not None


def test_multiple_parameters_checked_per_packet():
    det = AnomalyDetector()
    for _ in range(30):
        det.feed("SAT1", {
            "battery_voltage_v": 3.9 + _RNG.gauss(0, 0.05),
            "temperature_obcs_c": 25.0 + _RNG.gauss(0, 0.3),
            "solar_power_w": 2.5 + _RNG.gauss(0, 0.1),
        })

    events = det.feed("SAT1", {
        "battery_voltage_v": 99.0,
        "temperature_obcs_c": 999.0,
        "solar_power_w": 2.5,
    })
    affected = {e.parameter for e in events}
    assert "battery_voltage_v" in affected
    assert "temperature_obcs_c" in affected
    assert "solar_power_w" not in affected


# ─────────────────────────────────────────────────────────────────────
# Debounce / escalation / recovery — fixes the "flooded alerts" bug
# where a sustained fault produced one alert per telemetry packet.
#
# These tests use a deterministic per-test RNG so they don't depend on
# the test execution order (the module-level _RNG above is shared).
# ─────────────────────────────────────────────────────────────────────

def _local_baseline(rng_seed: int, center: float, noise: float, n: int = 30) -> list[float]:
    rng = random.Random(rng_seed)
    return [center + rng.gauss(0, noise) for _ in range(n)]


def test_sustained_critical_fault_emits_single_event():
    """While a parameter stays in the critical band, only the first packet
    emits. The next 9 must stay silent — no flood."""
    det = AnomalyDetector()
    baseline = _local_baseline(101, 25.0, 0.3, 30)
    _feed_n(det, "SAT1", "temperature_obcs_c", baseline)

    fault_value = 25.0 + 8 * 0.3  # comfortably critical
    first = det.feed("SAT1", {"temperature_obcs_c": fault_value})
    assert any(e.severity == "critical" for e in first)

    follow_ups = []
    for _ in range(9):
        follow_ups.extend(det.feed("SAT1", {"temperature_obcs_c": fault_value}))
    assert follow_ups == []


def test_warning_then_critical_emits_escalation():
    """A warning-level value followed by a critical-level value must emit
    BOTH events — the operator needs to know it got worse."""
    det = AnomalyDetector()
    baseline = _local_baseline(102, 25.0, 0.3, 30)
    _feed_n(det, "SAT1", "temperature_obcs_c", baseline)

    warning_val = 25.0 + 2.7 * 0.3   # ~2.7σ → warning
    critical_val = 25.0 + 8 * 0.3    # 8σ → critical

    w = det.feed("SAT1", {"temperature_obcs_c": warning_val})
    c = det.feed("SAT1", {"temperature_obcs_c": critical_val})
    assert any(e.severity == "warning" for e in w), f"expected warning, got {w}"
    assert any(e.severity == "critical" for e in c), f"expected critical, got {c}"


def test_recovered_then_re_excursion_emits_again():
    """After the value returns to normal AND drops below the recovery
    threshold, a subsequent excursion must produce a fresh alert.
    Pass cooldown=0 so the synchronous recovery+retrip in the test isn't
    suppressed by the 5-min production cooldown."""
    det = AnomalyDetector(emit_cooldown_s=0.0)
    baseline = _local_baseline(103, 25.0, 0.3, 30)
    _feed_n(det, "SAT1", "temperature_obcs_c", baseline)

    fault = 25.0 + 8 * 0.3
    first = det.feed("SAT1", {"temperature_obcs_c": fault})
    assert any(e.severity == "critical" for e in first)

    # Return to baseline for several packets — clears the active state.
    for _ in range(20):
        det.feed("SAT1", {"temperature_obcs_c": 25.0})

    second = det.feed("SAT1", {"temperature_obcs_c": fault})
    assert any(e.severity == "critical" for e in second)


def test_de_escalation_is_silent():
    """Once critical fires, dropping back to merely-warning must NOT emit a
    duplicate warning (would just be noise)."""
    det = AnomalyDetector()
    baseline = _local_baseline(104, 25.0, 0.3, 30)
    _feed_n(det, "SAT1", "temperature_obcs_c", baseline)

    crit = det.feed("SAT1", {"temperature_obcs_c": 25.0 + 8 * 0.3})
    warn = det.feed("SAT1", {"temperature_obcs_c": 25.0 + 2.5 * 0.3})
    assert any(e.severity == "critical" for e in crit)
    assert warn == []


def test_cooldown_suppresses_recovery_flap():
    """Real-world simulator noise causes z to oscillate across the recovery
    threshold (1.6) — without a time-based cooldown this would re-emit a
    fresh warning every flap. With the production cooldown enabled, the
    flap is silent."""
    det = AnomalyDetector()  # default 5-min cooldown
    baseline = _local_baseline(105, 25.0, 0.3, 30)
    _feed_n(det, "SAT1", "temperature_obcs_c", baseline)

    warning_val = 25.0 + 2.7 * 0.3
    healthy_val = 25.0
    flapping = []
    flapping.extend(det.feed("SAT1", {"temperature_obcs_c": warning_val}))
    for _ in range(20):
        flapping.extend(det.feed("SAT1", {"temperature_obcs_c": healthy_val}))
        flapping.extend(det.feed("SAT1", {"temperature_obcs_c": warning_val}))
    # First emit allowed; everything after stays silent inside the 5-min window.
    assert sum(1 for e in flapping if e.severity == "warning") == 1
