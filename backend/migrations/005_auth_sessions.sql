-- Session validity moves from JWT claims + Redis into Postgres.
--
-- v0.1.0 trusted the role baked into each JWT and kept the logout
-- blacklist only in Redis, so a demoted admin kept admin rights until the
-- token expired (forever, via refresh rotation) and every revoked token
-- became valid again whenever Redis was unreachable.
--
-- token_version: every JWT carries the version it was minted under. Bumping
-- the column (role change, deactivation, password change, refresh-token
-- replay) ends every outstanding session of that user at once.
--
-- revoked_tokens: per-token revocation (logout, refresh rotation). Rows are
-- only needed until the token would have expired anyway.

ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti         VARCHAR(64)  PRIMARY KEY,
    username    VARCHAR(64)  NOT NULL,
    expires_at  TIMESTAMPTZ  NOT NULL,
    revoked_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires
    ON revoked_tokens (expires_at);
