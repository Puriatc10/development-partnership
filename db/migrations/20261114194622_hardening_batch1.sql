-- =============================================================================
-- Eryndex CRM hardening — batch 1
-- Status: AUTHORED, NOT APPLIED.
--
-- Why not applied: this machine holds no DDL credentials for the Development
-- project (no DB password, no PAT for the owning org) — BLK-01 in
-- reports/eryndex/phase-0-baseline.md §10. Authored and reviewed now so the fix
-- is ready the moment access exists; applying it without access would be a
-- claim, not a change.
--
-- Scope: only the findings independently re-verified in
-- reports/eryndex/security-findings.md. Every change is additive or a
-- policy/function replacement; no data is rewritten and no column is dropped.
--
-- Rollback: each section is independent. Re-run the original definition from
-- crm/schema.sql to revert a section. Take a logical export first
-- (backups/dev-snapshot-*.json) and prefer a branch database over the shared
-- Development project.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- H-01  Suppression sync could never write as the operator role
--
-- fn_sync_contact_suppression / fn_sync_company_suppression are SECURITY INVOKER
-- triggers that INSERT into suppression_registry, but `authenticated` holds only
-- SELECT on that table (schema.sql L2086 after REVOKE ALL L2070). Therefore an
-- operator setting contacts.is_opted_out = TRUE raised
-- 42501 permission denied — the opt-out path was unusable by design.
--
-- Fix: run the two triggers as the owner with a pinned search_path. Granting
-- INSERT to `authenticated` instead would let any operator forge suppression
-- rows directly; SECURITY DEFINER keeps the ledger writable only through the
-- trigger's fixed logic.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_sync_contact_suppression()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.is_opted_out = TRUE THEN
        IF (OLD IS NULL OR OLD.is_opted_out = FALSE OR NEW.email IS DISTINCT FROM OLD.email) AND NEW.email IS NOT NULL THEN
            INSERT INTO suppression_registry (
                target_type, identifier, reason, source, created_by_actor_type, created_by_actor_identity
            ) VALUES (
                'email', LOWER(TRIM(NEW.email)), COALESCE(NEW.opt_out_reason, 'Contact opt-out request'),
                'opt_out_request', 'system', 'trg_contacts_suppression_sync'
            ) ON CONFLICT (identifier) DO NOTHING;
        END IF;

        IF (OLD IS NULL OR OLD.is_opted_out = FALSE OR NEW.linkedin_url IS DISTINCT FROM OLD.linkedin_url) AND NEW.linkedin_url IS NOT NULL THEN
            INSERT INTO suppression_registry (
                target_type, identifier, reason, source, created_by_actor_type, created_by_actor_identity
            ) VALUES (
                'linkedin', LOWER(TRIM(NEW.linkedin_url)), COALESCE(NEW.opt_out_reason, 'Contact opt-out request'),
                'opt_out_request', 'system', 'trg_contacts_suppression_sync'
            ) ON CONFLICT (identifier) DO NOTHING;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fn_sync_company_suppression()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.is_suppressed = TRUE THEN
        IF (OLD IS NULL OR OLD.is_suppressed = FALSE OR NEW.primary_domain IS DISTINCT FROM OLD.primary_domain) AND NEW.primary_domain IS NOT NULL THEN
            INSERT INTO suppression_registry (
                target_type, identifier, reason, source, created_by_actor_type, created_by_actor_identity
            ) VALUES (
                'domain', LOWER(TRIM(NEW.primary_domain)), COALESCE(NEW.suppression_reason, 'Company suppression'),
                -- Preserve the deployed semantic exactly: a company row is suppressed by
                -- an operator decision (manual_blacklist), not by an inbound opt-out
                -- request. This batch repairs the permission failure; it must not also
                -- change what the audit ledger records. See migration-review-batch1.md §4.
                'manual_blacklist', 'system', 'trg_companies_suppression_sync'
            ) ON CONFLICT (identifier) DO NOTHING;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- H-02  Agents could rewind and cancel tasks
--
-- fn_enforce_task_governance blocks pending→completed and edits to
-- completed/cancelled rows, but allows in_progress→pending and in_progress→
-- cancelled. Three documents (task-execution-protocol.md L53/96/97,
-- phase-2.2, phase-2-batch-b) state the opposite.
--
-- Fix: a separate BEFORE UPDATE guard that whitelists agent transitions. Kept
-- separate from the existing function so the history of that function stays
-- readable.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_enforce_agent_task_transitions()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT is_agent() THEN
        RETURN NEW;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'pending'     AND NEW.status = 'in_progress') OR
            (OLD.status = 'in_progress' AND NEW.status = 'completed')
        ) THEN
            RAISE EXCEPTION
                'Lifecycle Violation: agents may only move pending -> in_progress -> completed. Attempted % -> % on task %.',
                OLD.status, NEW.status, OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_agent_task_transitions
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_agent_task_transitions();

-- -----------------------------------------------------------------------------
-- H-03  Outreach drafting had no qualification precondition
--
-- agent_insert_outreach_messages requires an active task but never requires an
-- approved qualification or a qualified lifecycle stage, so a drafter could
-- produce outreach for a company that was never researched or scored. Contract
-- agent-contracts.md L457 states the precondition; the database did not.
-- -----------------------------------------------------------------------------

DROP POLICY IF EXISTS agent_insert_outreach_messages ON outreach_messages;

CREATE POLICY agent_insert_outreach_messages ON outreach_messages FOR INSERT TO authenticated
    WITH CHECK (
        is_agent()
        AND is_agent_type('drafter')
        AND drafted_by_agent_identity = current_agent_identity()
        AND approval_status = 'draft'
        AND approved_by_human_id IS NULL
        AND approved_at IS NULL
        AND is_admin_override = FALSE
        AND override_reason IS NULL
        AND override_by_human_id IS NULL
        AND sending_status = 'draft'
        AND proposed_commercial_model != 'strategic_revenue_sharing'
        AND EXISTS (
            SELECT 1 FROM tasks t
            WHERE t.assigned_actor_type = 'agent'
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = outreach_messages.company_id
              AND (t.contact_id IS NULL OR t.contact_id = outreach_messages.contact_id)
              AND (t.campaign_id IS NULL OR t.campaign_id = outreach_messages.campaign_id)
              AND t.status IN ('pending', 'in_progress')
        )
        -- New: the company must hold an approved, non-disqualified scorecard,
        -- and must have reached a stage where outreach is legitimate.
        AND EXISTS (
            SELECT 1 FROM lead_qualifications lq
            WHERE lq.company_id = outreach_messages.company_id
              AND lq.review_status = 'approved'
              AND lq.qualification_tier <> 'disqualified'
        )
        AND EXISTS (
            SELECT 1 FROM companies c
            WHERE c.id = outreach_messages.company_id
              AND c.lifecycle_stage IN ('qualified', 'outreach_queued', 'in_outreach')
        )
    );

-- -----------------------------------------------------------------------------
-- H-04  Two approved scorecards could coexist for one company
--
-- fn_sync_company_qualification takes "the last approved row", so the company's
-- tier depended on insertion order. A partial unique index makes a re-approval
-- an explicit replacement rather than an accident.
-- -----------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_qualifications_approved
    ON lead_qualifications (company_id)
    WHERE review_status = 'approved';

-- -----------------------------------------------------------------------------
-- H-05  Primary-domain sync could re-parent another company's domain
--
-- ON CONFLICT (domain) DO UPDATE SET company_id = EXCLUDED.company_id moved an
-- existing domain row to whichever company was written last. A duplicate domain
-- is a duplicate company, so this must fail loudly instead.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_sync_company_primary_domain()
RETURNS TRIGGER AS $$
DECLARE
    existing_company UUID;
BEGIN
    INSERT INTO company_domains (company_id, domain, is_primary)
    VALUES (NEW.id, LOWER(TRIM(NEW.primary_domain)), TRUE)
    ON CONFLICT (domain) DO NOTHING;

    SELECT cd.company_id INTO existing_company
    FROM company_domains cd
    WHERE cd.domain = LOWER(TRIM(NEW.primary_domain));

    IF existing_company IS NOT NULL AND existing_company <> NEW.id THEN
        RAISE EXCEPTION
            'Duplicate domain: % is already registered to company %. Merge the records instead of re-parenting a domain.',
            LOWER(TRIM(NEW.primary_domain)), existing_company;
    END IF;

    UPDATE company_domains SET is_primary = TRUE
    WHERE domain = LOWER(TRIM(NEW.primary_domain)) AND company_id = NEW.id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
