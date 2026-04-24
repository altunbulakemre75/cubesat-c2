"""
Statistical anomaly detector — z-score based, no ML dependencies.

Maintains a rolling window of recent values per (satellite, parameter).
Raises warning at 2σ, critical at 3σ.
"""

from __future__ import annotations

import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

WINDOW_SIZE = 60          # number of recent values per parameter
WARNING_THRESHOLD = 2.0   # z-score for warning
CRITICAL_THRESHOLD = 3.0  # z-score for critical
MAX_TRACKED_KEYS = 1000   # LRU cap: max (satellite, parameter) pairs tracked


@dataclass
class AnomalyEvent:
    satellite_id: str
    parameter: str
    value: float
    z_score: float
    severity: str        # "warning" | "critical"
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ParameterStats:
    """Online mean + variance using Welford's algorithm."""

    def __init__(self, maxlen: int = WINDOW_SIZE) -> None:
        self._window: deque[float] = deque(maxlen=maxlen)

    def push(self, value: float) -> float | None:
        """
        Add a value. Returns z-score if enough data, else None.

        When actual std is below the min_std floor (baseline has no noise),
        the returned z-score is capped so a single modest deviation doesn't
        spike to "critical" just because we divided by the synthetic floor.
        """
        if len(self._window) >= 2:
            mean = sum(self._window) / len(self._window)
            variance = sum((x - mean) ** 2 for x in self._window) / len(self._window)
            std = math.sqrt(variance)
            # Floor prevents /0 on perfectly constant baselines. We scale it
            # with |mean| to be units-aware (1% of the nominal value).
            min_std = max(0.001, 0.01 * abs(mean)) if mean != 0 else 0.001
            effective_std = max(std, min_std)
            z = abs(value - mean) / effective_std
            # If the baseline was nearly constant (actual std << floor), we
            # don't yet trust the variance estimate. Cap z below the critical
            # threshold so the detector waits for real noise to accumulate.
            if std < min_std * 0.5:
                z = min(z, CRITICAL_THRESHOLD - 0.01)
        else:
            z = None
        self._window.append(value)
        return z

    @property
    def has_baseline(self) -> bool:
        return len(self._window) >= 10  # need at least 10 points for a meaningful baseline


class AnomalyDetector:
    """
    One detector instance per process. Call feed() for each telemetry packet.
    Thread-safe within a single asyncio event loop.
    """

    _TRACKED_PARAMS = (
        "battery_voltage_v",
        "temperature_obcs_c",
        "temperature_eps_c",
        "solar_power_w",
        "rssi_dbm",
    )

    def __init__(self) -> None:
        # LRU-ordered dict so oldest-seen keys drop when MAX_TRACKED_KEYS exceeded.
        # key: (satellite_id, parameter) → ParameterStats
        self._stats: OrderedDict[tuple[str, str], ParameterStats] = OrderedDict()

    def feed(self, satellite_id: str, params: dict[str, float]) -> list[AnomalyEvent]:
        """
        Feed telemetry parameters for one satellite.
        Returns any anomaly events detected for this packet.
        """
        events: list[AnomalyEvent] = []

        for param in self._TRACKED_PARAMS:
            value = params.get(param)
            if value is None:
                continue

            key = (satellite_id, param)
            if key in self._stats:
                self._stats.move_to_end(key)  # bump freshness
            else:
                self._stats[key] = ParameterStats()
                # Evict oldest entries when over the cap
                while len(self._stats) > MAX_TRACKED_KEYS:
                    self._stats.popitem(last=False)

            stats = self._stats[key]
            z = stats.push(value)

            if z is None or not stats.has_baseline:
                continue

            if z >= CRITICAL_THRESHOLD:
                events.append(AnomalyEvent(
                    satellite_id=satellite_id,
                    parameter=param,
                    value=round(value, 4),
                    z_score=round(z, 3),
                    severity="critical",
                ))
            elif z >= WARNING_THRESHOLD:
                events.append(AnomalyEvent(
                    satellite_id=satellite_id,
                    parameter=param,
                    value=round(value, 4),
                    z_score=round(z, 3),
                    severity="warning",
                ))

        return events
