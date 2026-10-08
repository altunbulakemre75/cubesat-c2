-- Distinguish stations that can transmit (uplink) from receive-only ones.
--
-- SatNOGS is a receive-only network, but v0.1.0 scheduled commands onto
-- passes over any active station, including thousands of imported SatNOGS
-- stations. Imported stations default to receive-only; stations operators
-- created by hand are assumed to be their own and uplink-capable.

ALTER TABLE ground_stations
    ADD COLUMN IF NOT EXISTS uplink_capable BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE ground_stations SET uplink_capable = TRUE WHERE satnogs_id IS NULL;
