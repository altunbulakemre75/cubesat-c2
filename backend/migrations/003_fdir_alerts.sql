-- FDIR alert persistence + acknowledgement.
--
-- Until now FDIR fired safe-mode events on NATS (events.fdir.*) but kept
-- no record in the database, so:
--   1. Restarting the backend lost all FDIR history.
--   2. The dashboard's Active Alerts panel was rebuilt from a transient
--      WS stream — operators couldn't ack an alert and have it stay acked.
--
-- This migration adds an append-only alert log that the FDIR monitor
-- writes to before publishing on NATS, plus an ack column for operators.
-- We deliberately do NOT use TimescaleDB hypertables here — alerts are
-- low-volume and operators query by sat + ack status, not by time bucket.

CREATE TABLE IF NOT EXISTS fdir_alerts (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    satellite_id    VARCHAR(64)  NOT NULL REFERENCES satellites(id) ON DELETE CASCADE,
    reason          TEXT         NOT NULL,
    severity        VARCHAR(16)  NOT NULL DEFAULT 'critical',
    triggered_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    acknowledged    BOOLEAN      NOT NULL DEFAULT FALSE,
    acknowledged_by VARCHAR(64),
    acknowledged_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_fdir_alerts_unack
    ON fdir_alerts (satellite_id, acknowledged, triggered_at DESC);
