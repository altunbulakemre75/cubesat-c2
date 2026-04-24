-- CubeSat C2 — Initial Schema
-- Run order: 001 (this file)
-- TimescaleDB extension must be enabled on the database.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- =====================================================================
-- satellites
-- =====================================================================
CREATE TABLE IF NOT EXISTS satellites (
    id          VARCHAR(64)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL DEFAULT '',
    norad_id    INTEGER,
    description TEXT         DEFAULT '',
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- =====================================================================
-- ground_stations
-- =====================================================================
CREATE TABLE IF NOT EXISTS ground_stations (
    id                  SERIAL       PRIMARY KEY,
    name                VARCHAR(255) NOT NULL,
    satnogs_id          INTEGER      UNIQUE,
    latitude_deg        DOUBLE PRECISION NOT NULL,
    longitude_deg       DOUBLE PRECISION NOT NULL,
    elevation_m         DOUBLE PRECISION NOT NULL DEFAULT 0,
    min_elevation_deg   DOUBLE PRECISION NOT NULL DEFAULT 10.0,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- =====================================================================
-- tle_history (time-ordered TLE entries)
-- =====================================================================
CREATE TABLE IF NOT EXISTS tle_history (
    id            BIGSERIAL    PRIMARY KEY,
    satellite_id  VARCHAR(64)  NOT NULL REFERENCES satellites(id) ON DELETE CASCADE,
    epoch         TIMESTAMPTZ  NOT NULL,
    tle_line1     VARCHAR(70)  NOT NULL,
    tle_line2     VARCHAR(70)  NOT NULL,
    fetched_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tle_history_satellite_epoch
    ON tle_history (satellite_id, epoch DESC);

-- =====================================================================
-- telemetry (TimescaleDB hypertable — partitioned by time)
-- =====================================================================
CREATE TABLE IF NOT EXISTS telemetry (
    time                TIMESTAMPTZ      NOT NULL,
    satellite_id        VARCHAR(64)      NOT NULL,
    source              VARCHAR(32)      NOT NULL,
    sequence            INTEGER          NOT NULL,
    battery_voltage_v   DOUBLE PRECISION,
    temperature_obcs_c  DOUBLE PRECISION,
    temperature_eps_c   DOUBLE PRECISION,
    solar_power_w       DOUBLE PRECISION,
    rssi_dbm            DOUBLE PRECISION,
    uptime_s            INTEGER,
    mode                VARCHAR(32)
);

SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_telemetry_satellite_time
    ON telemetry (satellite_id, time DESC);

-- =====================================================================
-- commands
-- =====================================================================
CREATE TABLE IF NOT EXISTS commands (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    satellite_id     VARCHAR(64)  NOT NULL REFERENCES satellites(id) ON DELETE CASCADE,
    command_type     VARCHAR(64)  NOT NULL,
    params           JSONB,
    priority         INTEGER      NOT NULL DEFAULT 5,   -- 1=highest, 10=lowest
    status           VARCHAR(32)  NOT NULL DEFAULT 'pending',
    safe_retry       BOOLEAN      NOT NULL DEFAULT FALSE,
    idempotency_key  VARCHAR(128) UNIQUE,
    created_by       VARCHAR(64),
    retry_count      INTEGER      NOT NULL DEFAULT 0,
    error_message    TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    scheduled_at     TIMESTAMPTZ,
    sent_at          TIMESTAMPTZ,
    acked_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_commands_satellite_status
    ON commands (satellite_id, status, created_at DESC);

-- =====================================================================
-- users
-- =====================================================================
CREATE TABLE IF NOT EXISTS users (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(64)  NOT NULL UNIQUE,
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(32)  NOT NULL DEFAULT 'viewer',  -- viewer | operator | admin
    active        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMPTZ
);

-- =====================================================================
-- audit_log (append-only — no updates, no deletes)
-- =====================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL    PRIMARY KEY,
    timestamp    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    user_id      UUID         REFERENCES users(id),
    username     VARCHAR(64),
    action       VARCHAR(128) NOT NULL,
    target_type  VARCHAR(64),
    target_id    VARCHAR(128),
    details      JSONB,
    ip_address   INET,
    result       VARCHAR(32)  NOT NULL DEFAULT 'ok'  -- ok | denied | error
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON audit_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user
    ON audit_log (user_id, timestamp DESC);

-- =====================================================================
-- anomalies
-- =====================================================================
CREATE TABLE IF NOT EXISTS anomalies (
    id           BIGSERIAL    PRIMARY KEY,
    satellite_id VARCHAR(64)  NOT NULL,
    parameter    VARCHAR(64)  NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    z_score      DOUBLE PRECISION NOT NULL,
    severity     VARCHAR(16)  NOT NULL,  -- warning | critical
    detected_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    acknowledged BOOLEAN      NOT NULL DEFAULT FALSE,
    ack_by       VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_anomalies_satellite
    ON anomalies (satellite_id, detected_at DESC);

-- =====================================================================
-- pass_schedule (pre-computed pass windows)
-- =====================================================================
CREATE TABLE IF NOT EXISTS pass_schedule (
    id                  BIGSERIAL    PRIMARY KEY,
    satellite_id        VARCHAR(64)  NOT NULL,
    station_id          INTEGER      NOT NULL REFERENCES ground_stations(id),
    aos                 TIMESTAMPTZ  NOT NULL,   -- Acquisition of Signal
    los                 TIMESTAMPTZ  NOT NULL,   -- Loss of Signal
    max_elevation_deg   DOUBLE PRECISION NOT NULL,
    azimuth_at_aos_deg  DOUBLE PRECISION,
    computed_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pass_schedule_satellite_aos
    ON pass_schedule (satellite_id, aos);

-- Default admin user (password: admin123 — change immediately in production)
-- bcrypt hash of 'admin123'
INSERT INTO users (username, email, password_hash, role)
VALUES (
    'admin',
    'admin@cubesat-c2.local',
    '$2b$12$yiDr9tvZD6xHMEIWGb1nNuHx8UNiGHcU9Cl9niknu8/m4gGltS9Ry',
    'admin'
) ON CONFLICT DO NOTHING;
