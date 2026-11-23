-- =============================================================================
-- Human Gate 1 attribution + audit coverage
--
-- Two gaps found by the platform audit, both about trusting the trail rather than the caller:
--
--   1. `reviewed_by_human_id` was `NOT NULL`-only. Any operator could record a *different*
--      human as the reviewer of a decision. The code now verifies that the row carries the
--      signing operator, but the database is where the guarantee has to live.
--   2. `companies` and `discovery_candidates` — the two tables Gate 1 writes — had no audit
--      trigger. A company could appear, and a candidate could be approved, with no record of
--      who did it beyond two columns that nothing protects from later UPDATE.
--
-- Both triggers are deliberately narrow:
--   * the reviewer guard only fires when the review fields change, and stays silent when there
--     is no JWT claim at all (migrations, owner-run seeding, tests), because `auth.uid()` is
--     NULL in that context and there is no actor to bind to;
--   * the audit trigger records INSERT/UPDATE only — nothing in the platform deletes companies.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. The reviewer must be the identity that signed the request
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_enforce_reviewer_binding()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_actor UUID := auth.uid();
BEGIN
    -- No JWT context (owner connection, migration, test fixture): nothing to bind to.
    IF v_actor IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.reviewed_by_human_id IS NOT NULL AND NEW.reviewed_by_human_id <> v_actor THEN
        RAISE EXCEPTION
            'Reviewer attribution violation: reviewed_by_human_id (%) must be the signing operator (%). '
            'A human decision cannot be attributed to another human.',
            NEW.reviewed_by_human_id, v_actor
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_enforce_reviewer_binding() IS
    'Refuses a review recorded against anyone but the signing operator. Silent when auth.uid() is NULL.';

DROP TRIGGER IF EXISTS trg_discovery_candidates_reviewer_binding ON discovery_candidates;
CREATE TRIGGER trg_discovery_candidates_reviewer_binding
    BEFORE UPDATE ON discovery_candidates
    FOR EACH ROW
    WHEN (
        NEW.reviewed_by_human_id IS DISTINCT FROM OLD.reviewed_by_human_id
        OR NEW.status IS DISTINCT FROM OLD.status
    )
    EXECUTE FUNCTION fn_enforce_reviewer_binding();

-- -----------------------------------------------------------------------------
-- 2. Audit coverage for the Gate 1 tables
--
-- fn_audit_log_capture is generic: it stamps table_name, record_id, action, the actor type and
-- identity, and both row images. It is SECURITY DEFINER, which is what lets an operator's write
-- land an audit row even though `authenticated` holds only SELECT on audit_logs.
-- -----------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_companies_audit ON companies;
CREATE TRIGGER trg_companies_audit
    AFTER INSERT OR UPDATE ON companies
    FOR EACH ROW
    EXECUTE FUNCTION fn_audit_log_capture();

DROP TRIGGER IF EXISTS trg_discovery_candidates_audit ON discovery_candidates;
CREATE TRIGGER trg_discovery_candidates_audit
    AFTER UPDATE ON discovery_candidates
    FOR EACH ROW
    EXECUTE FUNCTION fn_audit_log_capture();

COMMIT;
