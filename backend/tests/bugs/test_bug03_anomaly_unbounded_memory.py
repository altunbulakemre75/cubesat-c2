"""
Bug #3: AnomalyDetector._stats is a dict keyed by (sat_id, param) that grows
forever. For a long-running process that observes many (test) satellite IDs,
this is a slow memory leak.

Fix: cap the number of tracked (sat, param) pairs; evict least-recently-seen
when the limit is reached.
"""

from src.anomaly.detector import AnomalyDetector, MAX_TRACKED_KEYS


def test_detector_evicts_oldest_keys_when_limit_exceeded():
    det = AnomalyDetector()
    # Feed MAX_TRACKED_KEYS + 50 unique satellite IDs (one param each)
    for i in range(MAX_TRACKED_KEYS + 50):
        det.feed(f"SAT-{i}", {"battery_voltage_v": 3.9})

    # The internal stats dict must not exceed the cap
    assert len(det._stats) <= MAX_TRACKED_KEYS, (
        f"AnomalyDetector kept {len(det._stats)} keys "
        f"but limit is {MAX_TRACKED_KEYS} — unbounded growth"
    )


def test_detector_keeps_recent_satellites():
    """Recently-seen satellites should not be evicted in favor of old ones."""
    det = AnomalyDetector()
    # Fill past limit
    for i in range(MAX_TRACKED_KEYS + 10):
        det.feed(f"SAT-{i}", {"battery_voltage_v": 3.9})

    # The last satellite fed should still be tracked
    last_key = (f"SAT-{MAX_TRACKED_KEYS + 9}", "battery_voltage_v")
    assert last_key in det._stats, "Most recent satellite was evicted — wrong order"
