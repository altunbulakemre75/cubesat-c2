"""
Statistical anomaly detector — z-score based, no ML dependencies.

Maintains a rolling window of recent values per (satellite, parameter).
Raises warning at 2σ, critical at 3σ.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

WINDOW_SIZE = 60          # number of recent values per parameter
WARNING_THRESHOLD = 2.0   # z-score for warning
CRITICAL_THRESHOLD = 3.0  # z-score for critical
# Hysteresis: an active anomaly is cleared once z drops below this. Lower
# than WARNING_THRESHOLD so a parameter trembling around 2.0σ doesn't
# bounce in and out of the alert panel.
RECOVERY_THRESHOLD = 1.6
# Time-based cooldown: even if z flaps across the recovery threshold (real
# telemetry on a noisy parameter does), the same severity won't re-emit
# within this window. Sustained faults still produce one heartbeat alert
# per cooldown so the operator sees the fault is still ongoing.
EMIT_COOLDOWN_S = 300.0   # 5 minutes
MAX_TRACKED_KEYS = 1000   # LRU cap: max (satellite, parameter) pairs tracked

_SEVERITY_RANK = {None: 0, "warning": 1, "critical": 2}


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

    def __init__(self, emit_cooldown_s: float = EMIT_COOLDOWN_S) -> None:
        # LRU-ordered dict so oldest-seen keys drop when MAX_TRACKED_KEYS exceeded.
        # key: (satellite_id, parameter) → ParameterStats
        self._stats: OrderedDict[tuple[str, str], ParameterStats] = OrderedDict()
        # Active anomaly state per (sat, param). Without this we'd emit a
        # fresh event every telemetry packet while a sustained fault holds
        # the value above threshold, which floods the operator alert panel.
        self._active: dict[tuple[str, str], str] = {}
        # Last-emit time per (sat, param, severity). Survives recovery so
        # a flapping value (z bouncing across the recovery threshold) still
        # gets debounced to one alert per cooldown.
        self._last_emit: dict[tuple[str, str], tuple[str, float]] = {}
        # Cooldown is configurable so tests can run with 0 (synchronous
        # recovery+retrip should still emit). Production stays at the 5-min
        # default which suppresses noisy flapping.
        self._cooldown = emit_cooldown_s

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

            # Recovery: clear active state once we drop comfortably back into
            # the noise floor, so the next true excursion fires a fresh event.
            if z < RECOVERY_THRESHOLD:
                self._active.pop(key, None)
                continue

            if z >= CRITICAL_THRESHOLD:
                band = "critical"
            elif z >= WARNING_THRESHOLD:
                band = "warning"
            else:
                # Between RECOVERY_THRESHOLD and WARNING_THRESHOLD: no emit,
                # but also don't clear the active state — hysteresis band.
                continue

            active = self._active.get(key)
            now = time.monotonic()
            band_rank = _SEVERITY_RANK[band]
            active_rank = _SEVERITY_RANK[active]

            # Cooldown: the same severity emitted recently for this key
            # suppresses re-emit, even after a recovery clear (otherwise
            # noise that flaps across the recovery threshold would re-emit
            # every cycle).
            last = self._last_emit.get(key)
            in_cooldown = (
                last is not None
                and last[0] == band
                and (now - last[1]) < self._cooldown
            )

            should_emit = False
            if band_rank > active_rank:
                # Escalation: None→warning, None→critical, warning→critical.
                # Skip if the same band already emitted within cooldown
                # (real-world flap) — otherwise emit.
                if not in_cooldown:
                    should_emit = True
                self._active[key] = band
            elif band_rank == active_rank:
                # Same band as the active state — heartbeat after cooldown.
                if not in_cooldown:
                    should_emit = True
            # band_rank < active_rank: de-escalation, stay silent, keep active.

            if should_emit:
                events.append(AnomalyEvent(
                    satellite_id=satellite_id,
                    parameter=param,
                    value=round(value, 4),
                    z_score=round(z, 3),
                    severity=band,
                ))
                self._last_emit[key] = (band, now)

        return events
