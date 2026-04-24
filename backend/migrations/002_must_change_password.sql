-- Force password change on first login after admin bootstrap.
-- Set TRUE for the seed admin; users created via /users endpoint keep FALSE.

ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
