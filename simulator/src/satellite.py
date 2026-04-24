import math
import random
import time
from dataclasses import dataclass
from enum import Enum


class SatelliteMode(str, Enum):
    BEACON = "beacon"
    DEPLOYMENT = "deployment"
    NOMINAL = "nominal"
    SCIENCE = "science"
    SAFE = "safe"


# Auto-transition targets per mode (first element = default next state)
_TRANSITIONS: dict[SatelliteMode, list[SatelliteMode]] = {
    SatelliteMode.BEACON: [SatelliteMode.DEPLOYMENT],
    SatelliteMode.DEPLOYMENT: [SatelliteMode.NOMINAL],
    SatelliteMode.NOMINAL: [],   # stays; fault → SAFE
    SatelliteMode.SCIENCE: [],   # stays; fault → SAFE
    SatelliteMode.SAFE: [],      # stays; manual recovery only
}

# Seconds in each mode before auto-transitioning (inf = manual only)
_MODE_DURATION_S: dict[SatelliteMode, float] = {
    SatelliteMode.BEACON: 30.0,
    SatelliteMode.DEPLOYMENT: 60.0,
    SatelliteMode.NOMINAL: float("inf"),
    SatelliteMode.SCIENCE: float("inf"),
    SatelliteMode.SAFE: float("inf"),
}

ORBIT_PERIOD_S = 5400.0  # ~90-minute LEO orbit


@dataclass
class SimulatedTelemetry:
    satellite_id: str
    mode: SatelliteMode
    battery_voltage_v: float
    temperature_obcs_c: float
    temperature_eps_c: float
    solar_power_w: float
    rssi_dbm: float
    uptime_s: int
    sequence: int


class CubeSat:
    """
    Simulated CubeSat with a state machine and realistic-ish telemetry.

    Fault injection via fault_probability: probability per tick of entering SAFE.
    Safe mode auto-recovers after safe_recovery_s for simulation convenience.
    """

    def __init__(
        self,
        satellite_id: str,
        fault_probability: float = 0.001,
        safe_recovery_s: float = 120.0,
    ) -> None:
        self.satellite_id = satellite_id
        self.fault_probability = fault_probability
        self.safe_recovery_s = safe_recovery_s

        self._mode = SatelliteMode.BEACON
        self._mode_entered_at = time.monotonic()
        self._boot_time = time.monotonic()
        self._sequence = 0

        # Random walk baselines — each satellite starts slightly different
        self._temp_obcs = random.uniform(18.0, 30.0)
        self._temp_eps = random.uniform(15.0, 25.0)

    @property
    def mode(self) -> SatelliteMode:
        return self._mode

    def force_mode(self, mode: SatelliteMode) -> None:
        """External command to change mode (used by command handler in later phases)."""
        self._set_mode(mode)

    def tick(self) -> SimulatedTelemetry:
        """Advance state machine by one step and return a telemetry packet."""
        self._maybe_transition()
        self._sequence += 1
        uptime = int(time.monotonic() - self._boot_time)
        temp_obcs, temp_eps = self._step_temperatures()

        return SimulatedTelemetry(
            satellite_id=self.satellite_id,
            mode=self._mode,
            battery_voltage_v=self._battery_voltage(uptime),
            temperature_obcs_c=temp_obcs,
            temperature_eps_c=temp_eps,
            solar_power_w=self._solar_power(uptime),
            rssi_dbm=round(random.uniform(-120.0, -80.0), 1),
            uptime_s=uptime,
            sequence=self._sequence,
        )

    # --- private helpers ---

    def _set_mode(self, mode: SatelliteMode) -> None:
        self._mode = mode
        self._mode_entered_at = time.monotonic()

    def _elapsed_in_mode(self) -> float:
        return time.monotonic() - self._mode_entered_at

    def _maybe_transition(self) -> None:
        elapsed = self._elapsed_in_mode()

        # Timed auto-transitions (BEACON → DEPLOYMENT → NOMINAL)
        if elapsed >= _MODE_DURATION_S[self._mode]:
            targets = _TRANSITIONS[self._mode]
            if targets:
                self._set_mode(targets[0])
                return

        # Safe mode auto-recovery (simulation convenience)
        if self._mode == SatelliteMode.SAFE and elapsed >= self.safe_recovery_s:
            self._set_mode(SatelliteMode.NOMINAL)
            return

        # Random fault injection for NOMINAL / SCIENCE
        if self._mode in (SatelliteMode.NOMINAL, SatelliteMode.SCIENCE):
            if random.random() < self.fault_probability:
                self._set_mode(SatelliteMode.SAFE)

    def _battery_voltage(self, uptime: int) -> float:
        # Sinusoidal charge/discharge matching orbit period
        phase = 2 * math.pi * uptime / ORBIT_PERIOD_S
        base = 3.9 + 0.15 * math.sin(phase)
        if self._mode == SatelliteMode.SAFE:
            base -= 0.2  # higher drain in safe mode
        noise = random.gauss(0, 0.01)
        return round(max(3.3, min(4.2, base + noise)), 3)

    def _step_temperatures(self) -> tuple[float, float]:
        self._temp_obcs += random.gauss(0, 0.4)
        self._temp_obcs = max(-30.0, min(70.0, self._temp_obcs))
        self._temp_eps += random.gauss(0, 0.3)
        self._temp_eps = max(-40.0, min(60.0, self._temp_eps))
        return round(self._temp_obcs, 2), round(self._temp_eps, 2)

    def _solar_power(self, uptime: int) -> float:
        phase = 2 * math.pi * uptime / ORBIT_PERIOD_S
        # Sun-lit for ~60% of orbit; clamp eclipse to zero
        raw = 5.0 * math.sin(phase) + random.gauss(0, 0.1)
        return round(max(0.0, min(7.0, raw)), 3)
