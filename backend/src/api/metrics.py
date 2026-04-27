"""
Custom Prometheus metrics for CubeSat C2.

Exposed at GET /metrics (via prometheus-fastapi-instrumentator).
Scrape interval is configured in deployment/prometheus/prometheus.yml.
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Telemetry pipeline ───────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# Cardinality discipline:
# Prometheus stores one time-series per unique label combination. A label
# value driven by user input (command_type, satellite_id created on first
# telemetry packet) means an attacker with one curl can create millions of
# series and OOM the Prometheus server. We deliberately keep labels SHORT
# and BOUNDED. For per-satellite breakdown, query the database, not metrics.
# ──────────────────────────────────────────────────────────────────────────────

telemetry_ingested_total = Counter(
    "cubesat_telemetry_ingested_total",
    "Number of telemetry packets written to TimescaleDB",
    ["source"],   # bounded set: ax25, kiss, ccsds, simulated, satnogs
)

telemetry_decode_errors_total = Counter(
    "cubesat_telemetry_decode_errors_total",
    "Protocol adapter decode failures (bad frames)",
    ["protocol"],   # bounded
)

# ── Commands ─────────────────────────────────────────────────────────────────

commands_total = Counter(
    "cubesat_commands_total",
    "Total commands created (no per-type label — query DB for that)",
)

commands_denied_by_policy_total = Counter(
    "cubesat_commands_denied_by_policy_total",
    "Commands rejected by the policy engine (wrong satellite mode)",
    ["satellite_mode"],   # bounded enum
)

# ── Passes ───────────────────────────────────────────────────────────────────

pass_predictions_total = Counter(
    "cubesat_pass_predictions_total",
    "Pass windows computed and stored (no per-satellite label)",
)

# ── Anomalies + FDIR ─────────────────────────────────────────────────────────

anomalies_detected_total = Counter(
    "cubesat_anomalies_detected_total",
    "Anomalies detected by the z-score detector",
    ["parameter", "severity"],   # bounded: 5 params × 2 severities = 10 series
)

fdir_alerts_total = Counter(
    "cubesat_fdir_alerts_total",
    "FDIR safe-mode alerts raised",
    ["reason_category"],   # bounded enum
)

# ── System state ─────────────────────────────────────────────────────────────

satellites_active = Gauge(
    "cubesat_satellites_active",
    "Number of active satellites currently in the database",
)

ground_stations_active = Gauge(
    "cubesat_ground_stations_active",
    "Number of active ground stations",
)

tle_age_hours = Histogram(
    "cubesat_tle_age_hours",
    "Age distribution of the latest TLE per satellite at propagation time",
    buckets=(1, 6, 12, 24, 48, 72, 168, 720),
)

# ── Auth / security ──────────────────────────────────────────────────────────

auth_login_total = Counter(
    "cubesat_auth_login_total",
    "Login attempts by result",
    ["result"],
)

websocket_connections_active = Gauge(
    "cubesat_websocket_connections_active",
    "Live WebSocket connections",
    ["channel"],
)
