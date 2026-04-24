"""
Custom Prometheus metrics for CubeSat C2.

Exposed at GET /metrics (via prometheus-fastapi-instrumentator).
Scrape interval is configured in deployment/prometheus/prometheus.yml.
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Telemetry pipeline ───────────────────────────────────────────────────────

telemetry_ingested_total = Counter(
    "cubesat_telemetry_ingested_total",
    "Number of telemetry packets written to TimescaleDB",
    ["satellite_id", "source"],
)

telemetry_decode_errors_total = Counter(
    "cubesat_telemetry_decode_errors_total",
    "Protocol adapter decode failures (bad frames)",
    ["protocol"],
)

# ── Commands ─────────────────────────────────────────────────────────────────

commands_total = Counter(
    "cubesat_commands_total",
    "Commands created by status",
    ["status", "command_type"],
)

commands_denied_by_policy_total = Counter(
    "cubesat_commands_denied_by_policy_total",
    "Commands rejected by the policy engine (wrong satellite mode)",
    ["satellite_mode", "command_type"],
)

# ── Passes ───────────────────────────────────────────────────────────────────

pass_predictions_total = Counter(
    "cubesat_pass_predictions_total",
    "Pass windows computed and stored",
    ["satellite_id"],
)

# ── Anomalies + FDIR ─────────────────────────────────────────────────────────

anomalies_detected_total = Counter(
    "cubesat_anomalies_detected_total",
    "Anomalies detected by the z-score detector",
    ["satellite_id", "parameter", "severity"],
)

fdir_alerts_total = Counter(
    "cubesat_fdir_alerts_total",
    "FDIR safe-mode alerts raised",
    ["satellite_id", "reason_category"],
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
