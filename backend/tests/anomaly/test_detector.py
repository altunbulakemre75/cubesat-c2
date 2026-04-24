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
