-- =============================================================================
-- Integrity constraints the schema was missing
--
--   * H-06: the two composite foreign keys on `activities` said `ON DELETE SET NULL` without a
--     column list, so deleting a contact or a conversation would also try to null `company_id` —
--     which is NOT NULL. The cascade would fail, or the row would be lost. PostgreSQL 15+ lets the
--     FK name the single column to null: `ON DELETE SET NULL (contact_id)`.
--   * `research_reports` could hold two reports with the same version for one company. The
--     research executor builds on that column, so it has to be unique.
--   * `company_domains` allowed more than one primary domain per company; "primary" was a claim
--     nothing checked.
--   * `tasks` had no index matching the predicates its RLS policies actually filter on
--     (company_id, status, assigned_actor_identity), so every agent claim scanned.
--
-- Indexes are created with IF NOT EXISTS so a re-run against a database that already has them is
-- a no-op; the constraint swap is naturally idempotent because it drops before adding.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. H-06 — value the nullable column only
-- -----------------------------------------------------------------------------

ALTER TABLE activities DROP CONSTRAINT IF EXISTS fk_activities_contact_company;
ALTER TABLE activities
    ADD CONSTRAINT fk_activities_contact_company
    FOREIGN KEY (contact_id, company_id)
    REFERENCES contacts(id, company_id)
    ON DELETE SET NULL (contact_id);

ALTER TABLE activities DROP CONSTRAINT IF EXISTS fk_activities_conversation_company;
ALTER TABLE activities
    ADD CONSTRAINT fk_activities_conversation_company
    FOREIGN KEY (conversation_id, company_id)
    REFERENCES conversations(id, company_id)
    ON DELETE SET NULL (conversation_id);

-- -----------------------------------------------------------------------------
-- 2. One report per version, one primary domain per company
-- -----------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS uq_research_reports_company_version
    ON research_reports (company_id, report_version);

CREATE UNIQUE INDEX IF NOT EXISTS uq_company_domains_one_primary
    ON company_domains (company_id)
    WHERE is_primary;

-- -----------------------------------------------------------------------------
-- 3. Index the RLS predicates
-- -----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_tasks_company_status_actor
    ON tasks (company_id, status, assigned_actor_identity);

CREATE INDEX IF NOT EXISTS idx_lead_qualifications_evaluator
    ON lead_qualifications (evaluator_identity);

CREATE INDEX IF NOT EXISTS idx_discovery_candidates_converted
    ON discovery_candidates (converted_company_id);

COMMIT;
