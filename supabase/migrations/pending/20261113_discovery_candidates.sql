-- =============================================================================
-- Discovery candidate staging (Workflow 1, Human Gate 1)
-- Status: AUTHORED, NOT APPLIED (BLK-01: no DDL credentials on this machine).
--
-- Why this table: discovery must never write to `companies`. Candidates land
-- here, an operator reviews them, and only then does a company row exist.
--
-- Design notes
--  * `domain` is normalized by the application (discovery/identity.py) before it
--    reaches this table; the partial unique index makes an accidental re-stage of
--    the same pending domain impossible while still allowing a rejected
--    candidate to be re-proposed later.
--  * `batch_key` is the content-addressed idempotency key from
--    orchestration.idempotency; re-running the same directive is a no-op.
--  * Evidence lives in a child table so a candidate can carry several citable
--    sources without JSONB archaeology.
--  * Agents may INSERT pending rows and read their own; only operators may
--    approve/reject or convert to a company.
--
-- Rollback: see the end of file.
-- =============================================================================

BEGIN;

CREATE TYPE discovery_candidate_status AS ENUM (
    'pending_review',   -- staged, awaiting Human Gate 1
    'approved',         -- operator approved; company record created separately
    'rejected',         -- operator rejected (reason in review_notes)
    'duplicate',        -- matched an existing company
    'conflict'          -- domain owned by one company, name matches another
);

CREATE TABLE discovery_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    signals JSONB NOT NULL DEFAULT '{}',
    status discovery_candidate_status NOT NULL DEFAULT 'pending_review',
    batch_key TEXT,
    discovered_by_agent_identity TEXT NOT NULL,
    reviewed_by_human_id UUID,
    reviewed_at TIMESTAMPTZ,
    review_notes TEXT,
    converted_company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT chk_discovery_candidate_domain_format
        CHECK (domain = LOWER(domain) AND domain NOT LIKE '%,%' AND domain <> ''),
    CONSTRAINT chk_discovery_candidate_review_pair
        CHECK (
            (status = 'pending_review' AND reviewed_by_human_id IS NULL AND reviewed_at IS NULL)
            OR (status <> 'pending_review' AND reviewed_by_human_id IS NOT NULL AND reviewed_at IS NOT NULL)
        )
);

COMMENT ON TABLE discovery_candidates IS
    'Staging for companies discovered by Workflow 1. Never the system of record; a company exists only after Human Gate 1.';

CREATE UNIQUE INDEX uq_discovery_candidates_pending_domain
    ON discovery_candidates (domain) WHERE status = 'pending_review';
CREATE UNIQUE INDEX uq_discovery_candidates_batch_domain
    ON discovery_candidates (batch_key, domain);
CREATE INDEX idx_discovery_candidates_status ON discovery_candidates (status);
CREATE INDEX idx_discovery_candidates_normalized_name ON discovery_candidates (normalized_name);

CREATE TABLE discovery_candidate_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id UUID NOT NULL REFERENCES discovery_candidates(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    excerpt TEXT,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    provider TEXT,
    CONSTRAINT chk_candidate_evidence_url CHECK (url LIKE 'http://%' OR url LIKE 'https://%')
);

CREATE INDEX idx_discovery_candidate_evidence_candidate ON discovery_candidate_evidence (candidate_id);

CREATE TRIGGER trg_discovery_candidates_updated_at
    BEFORE UPDATE ON discovery_candidates
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

ALTER TABLE discovery_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_candidate_evidence ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON discovery_candidates, discovery_candidate_evidence FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON discovery_candidates, discovery_candidate_evidence TO authenticated;

CREATE POLICY anon_deny_discovery_candidates ON discovery_candidates FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_discovery_evidence ON discovery_candidate_evidence FOR ALL TO anon USING (false);

-- Operators own Gate 1.
CREATE POLICY human_operator_all_discovery_candidates ON discovery_candidates FOR ALL TO authenticated
    USING (is_human_operator()) WITH CHECK (is_human_operator());
CREATE POLICY human_operator_all_discovery_evidence ON discovery_candidate_evidence FOR ALL TO authenticated
    USING (is_human_operator()) WITH CHECK (is_human_operator());

-- Discovery agents may stage their own pending candidates and read them back
-- (needed for idempotency). They cannot approve, reject, or convert.
CREATE POLICY agent_insert_discovery_candidates ON discovery_candidates FOR INSERT TO authenticated
    WITH CHECK (
        is_agent()
        AND is_agent_type('discovery')
        AND discovered_by_agent_identity = current_agent_identity()
        AND status = 'pending_review'
        AND reviewed_by_human_id IS NULL
        AND reviewed_at IS NULL
        AND converted_company_id IS NULL
    );

CREATE POLICY agent_select_own_discovery_candidates ON discovery_candidates FOR SELECT TO authenticated
    USING (
        is_agent()
        AND is_agent_type('discovery')
        AND discovered_by_agent_identity = current_agent_identity()
    );

CREATE POLICY agent_insert_discovery_evidence ON discovery_candidate_evidence FOR INSERT TO authenticated
    WITH CHECK (
        is_agent()
        AND is_agent_type('discovery')
        AND EXISTS (
            SELECT 1 FROM discovery_candidates c
            WHERE c.id = discovery_candidate_evidence.candidate_id
              AND c.discovered_by_agent_identity = current_agent_identity()
              AND c.status = 'pending_review'
        )
    );

CREATE POLICY agent_select_discovery_evidence ON discovery_candidate_evidence FOR SELECT TO authenticated
    USING (
        is_agent()
        AND is_agent_type('discovery')
        AND EXISTS (
            SELECT 1 FROM discovery_candidates c
            WHERE c.id = discovery_candidate_evidence.candidate_id
              AND c.discovered_by_agent_identity = current_agent_identity()
        )
    );

COMMIT;

-- =============================================================================
-- ROLLBACK (run manually, Development only, after exporting any staged rows)
--
--   BEGIN;
--   DROP TABLE IF EXISTS discovery_candidate_evidence;
--   DROP TABLE IF EXISTS discovery_candidates;
--   DROP TYPE IF EXISTS discovery_candidate_status;
--   COMMIT;
--
-- Human Gate 1 path after this migration exists:
--   operator updates status='approved', reviewed_by_human_id=auth.uid(),
--   reviewed_at=clock_timestamp(), then registers the company through
--   operator_cli.register_company and sets converted_company_id.
-- =============================================================================
