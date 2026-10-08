-- Make audit_log append-only in the database, not only by convention.
--
-- Rows can be inserted; any UPDATE, DELETE or TRUNCATE is rejected. This
-- stops the application (or anyone with its credentials) from rewriting
-- history. A database superuser can still drop the trigger — real
-- tamper-evidence needs a separate role or log shipping to write-once
-- storage, which is out of scope for a single-host deployment.

CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% rejected)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$;

DROP TRIGGER IF EXISTS audit_log_no_modify ON audit_log;
CREATE TRIGGER audit_log_no_modify
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
