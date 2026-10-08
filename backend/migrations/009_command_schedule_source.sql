-- Remember whether a command's transmit time came from the operator or
-- from pass planning. Pass-planned commands must only go out while an
-- uplink window is open; an operator-set time is an explicit override.

ALTER TABLE commands ADD COLUMN IF NOT EXISTS scheduled_manually BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE commands SET scheduled_manually = TRUE
 WHERE scheduled_at IS NOT NULL AND status IN ('pending', 'awaiting_approval');
