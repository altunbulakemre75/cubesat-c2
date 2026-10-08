-- Two-admin approval for critical commands.
--
-- v0.1.0 stored the first admin's request as 'pending', which the scheduler
-- picks up within seconds — the second approval was never waited for.
-- Critical commands now start in 'awaiting_approval' (the scheduler only
-- reads 'pending') and record who released them.

ALTER TABLE commands ADD COLUMN IF NOT EXISTS approved_by VARCHAR(64);
ALTER TABLE commands ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
