-- SatNOGS DB telemetry observations.
--
-- Stores demodulated frames pulled from db.satnogs.org/api/telemetry/.
-- These are REAL satellite telemetry: amateurs around the world receive
-- amateur cubesats with RTL-SDRs and upload the decoded frames to SatNOGS.
-- We pull on a schedule and persist for display/analysis.
--
-- We deliberately keep raw + decoded separately: raw is the verbatim hex/base64
-- frame (always available), decoded is the JSON parsed by SatNOGS DB (only
-- present for satellites with a public schema).

CREATE TABLE IF NOT EXISTS satnogs_observations (
    id              BIGSERIAL    PRIMARY KEY,
    satellite_id    VARCHAR(64)  REFERENCES satellites(id) ON DELETE CASCADE,
    norad_cat_id    INTEGER      NOT NULL,
    observer        VARCHAR(128),
    transmitter     VARCHAR(64),
    timestamp_utc   TIMESTAMPTZ  NOT NULL,
    frame_hex       TEXT,
    decoded_json    JSONB,
    app_source      VARCHAR(32),
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Dedupe key: same satellite + same timestamp + same observer is the
    -- same observation (SatNOGS sometimes returns dups across pages).
    UNIQUE (norad_cat_id, timestamp_utc, observer)
);

CREATE INDEX IF NOT EXISTS idx_satnogs_obs_norad_time
    ON satnogs_observations (norad_cat_id, timestamp_utc DESC);

CREATE INDEX IF NOT EXISTS idx_satnogs_obs_sat_time
    ON satnogs_observations (satellite_id, timestamp_utc DESC)
    WHERE satellite_id IS NOT NULL;
