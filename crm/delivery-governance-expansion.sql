-- =============================================================================
-- Operational Delivery & Governance Platform — Expansion Schema Migration
-- Business: Managed Development Partner
-- Phase: 2.1 — Super Prompt 05 Hardened Delivery & Governance Expansion
-- Scope: Partnerships, Projects, Milestones, Change Requests, Technical Lead
--        Feasibility, Delivery Authorization Gates, State Machines & Audit
-- Prerequisite: crm/schema.sql (MVP Core) must be migrated first.
-- Status: DESIGN ONLY (DO NOT EXECUTE AGAINST REMOTE DATABASE)
-- Engine: PostgreSQL 15+ (Supabase Native)
-- =============================================================================

-- =============================================================================
-- 1. ROLE PREDICATE EXTENSION
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Function: is_technical_lead
-- Verifies whether caller possesses authenticated Technical Lead authority.
-- Compatible with Core custom claims: checks technical_role = 'lead_engineer'
-- or user_role IN ('technical_lead', 'lead_engineer', 'founder').
-- Does not treat every human operator as a technical lead.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION is_technical_lead()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'user_role') != 'agent' AND (
            (auth.jwt() -> 'app_metadata' ->> 'technical_role') = 'lead_engineer' OR
            (auth.jwt() -> 'app_metadata' ->> 'user_role') IN ('technical_lead', 'lead_engineer', 'founder')
        ),
        FALSE
    );
END;
$$ LANGUAGE plpgsql STABLE;

-- =============================================================================
-- 2. DELIVERY & GOVERNANCE ENUM TYPES
-- =============================================================================

CREATE TYPE partnership_stage_type AS ENUM (
    'onboarding',              -- Master agreement signed, establishing communication & delivery channels
    'active',                  -- Actively collaborating; in-flight projects or capacity retainers
    'dormant',                 -- Partnership in good standing between active project cycles
    'suspended',               -- Operational, contractual, or payment hold
    'terminated'               -- Wholesale relationship formally concluded
);

CREATE TYPE partnership_agreement_status_type AS ENUM (
    'drafting',
    'pending_signature',
    'signed_active',
    'expired',
    'amended'
);

CREATE TYPE delivery_mode_type AS ENUM (
    'white_label',             -- Behind-the-scenes under partner brand (Default Mandatory Mode)
    'co_branded'               -- Introduced as specialized engineering division (Requires mutual agreement)
);

CREATE TYPE project_status_type AS ENUM (
    'scoping',                 -- Defining features, deliverables, and boundary specifications
    'feasibility_review',      -- Mandatory review by Technical Lead prior to commercial acceptance
    'proposal_approved',       -- Scope of Work signed by both parties
    'deposit_pending',         -- Awaiting agreed upfront milestone kickoff deposit
    'in_progress',             -- Development underway by technical delivery team
    'in_acceptance_review',    -- Deliverables deployed on staging undergoing partner inspection
    'completed',               -- Technical delivery complete and all milestones accepted by partner
    'warranty_active',         -- Post-delivery bug-fix period active
    'closed',                  -- Administratively and financially closed (final payment confirmed)
    'cancelled'                -- Project cancelled prior to completion (Terminal state)
);

CREATE TYPE feasibility_status_type AS ENUM (
    'pending_review',
    'approved_by_lead',
    'rejected_unfeasible'
);

CREATE TYPE milestone_status_type AS ENUM (
    'pending_delivery',        -- Implementation in progress
    'submitted_for_review',    -- Delivered to partner for acceptance review
    'accepted',                -- Formally accepted (explicit sign-off or window elapsed without defects)
    'revisions_requested',     -- Partner requested modifications within in-scope revision limits
    'disputed'                 -- Partner raised defects exceeding scope or unresolved defects
);

CREATE TYPE milestone_acceptance_type AS ENUM (
    'explicit_partner_signoff',-- Documented sign-off from partner contact
    'automatic_window_expiry', -- 5-business-day review window elapsed without reported defects
    'human_operator_override'  -- Authorized operator manual sign-off
);

CREATE TYPE change_request_status_type AS ENUM (
    'draft',
    'submitted_to_partner',
    'approved_by_partner',
    'rejected_by_partner',
    'cancelled'
);

-- =============================================================================
-- 3. DELIVERY & GOVERNANCE TABLES
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 3.1. Partnerships
-- Master commercial relationship record codifying wholesale terms, white-label defaults, and client protection.
-- Composite UNIQUE (id, company_id) enforces cross-entity referential integrity.
-- -----------------------------------------------------------------------------
CREATE TABLE partnerships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT UNIQUE,
    partnership_stage partnership_stage_type NOT NULL DEFAULT 'onboarding',
    primary_commercial_model commercial_model_type NOT NULL DEFAULT 'project_based_wholesale',
    agreement_status partnership_agreement_status_type NOT NULL DEFAULT 'drafting',
    partner_owns_end_client BOOLEAN NOT NULL DEFAULT TRUE,
    non_circumvention_agreed BOOLEAN NOT NULL DEFAULT TRUE,
    delivery_mode delivery_mode_type NOT NULL DEFAULT 'white_label',
    co_branded_agreement_reference TEXT,
    rev_share_recognized_revenue_terms TEXT,
    rev_share_direct_costs_terms TEXT,
    rev_share_audit_rights_terms TEXT,
    signed_agreement_url TEXT,
    effective_date DATE,
    expiration_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_partnerships_id_company UNIQUE (id, company_id),
    CONSTRAINT chk_partnership_partner_owns_client CHECK (partner_owns_end_client = TRUE),
    CONSTRAINT chk_partnership_non_circumvention CHECK (non_circumvention_agreed = TRUE),
    CONSTRAINT chk_partnership_co_branded_reference CHECK (
        delivery_mode != 'co_branded' OR 
        co_branded_agreement_reference IS NOT NULL
    ),
    CONSTRAINT chk_partnership_dates CHECK (
        expiration_date IS NULL OR 
        effective_date IS NULL OR 
        expiration_date >= effective_date
    ),
    CONSTRAINT chk_partnership_signed_doc CHECK (
        agreement_status != 'signed_active' OR 
        (signed_agreement_url IS NOT NULL AND effective_date IS NOT NULL)
    ),
    CONSTRAINT chk_rev_share_governance CHECK (
        primary_commercial_model != 'strategic_revenue_sharing' OR 
        (
            rev_share_recognized_revenue_terms IS NOT NULL AND 
            rev_share_direct_costs_terms IS NOT NULL AND 
            rev_share_audit_rights_terms IS NOT NULL
        )
    )
);

COMMENT ON TABLE partnerships IS 'Governing agreement records. Enforces partner client ownership and non-circumvention.';

CREATE TRIGGER trg_partnerships_updated_at
    BEFORE UPDATE ON partnerships
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.2. Projects (Hardened Technical Lead & Pre-Operational Delivery Gates)
-- Discrete development engagements executed under a partnership.
-- Composite FK guarantees partnership_id belongs to the designated company_id.
-- -----------------------------------------------------------------------------
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partnership_id UUID NOT NULL,
    company_id UUID NOT NULL,
    project_name TEXT NOT NULL,
    project_code TEXT NOT NULL UNIQUE,
    scope_description TEXT NOT NULL,
    status project_status_type NOT NULL DEFAULT 'scoping',
    delivery_mode delivery_mode_type NOT NULL DEFAULT 'white_label',
    -- Technical Lead Feasibility Sign-off
    feasibility_status feasibility_status_type NOT NULL DEFAULT 'pending_review',
    feasibility_reviewed_by_lead_id UUID,
    feasibility_review_notes TEXT,
    feasibility_reviewed_at TIMESTAMPTZ,
    -- Commercial Model & Upfront Deposit
    commercial_model commercial_model_type NOT NULL DEFAULT 'project_based_wholesale',
    commercial_value NUMERIC(12,2),
    currency VARCHAR(3),
    deposit_required NUMERIC(12,2),
    deposit_paid BOOLEAN NOT NULL DEFAULT FALSE,
    deposit_confirmed_by_human_id UUID,
    deposit_paid_at TIMESTAMPTZ,
    -- Financial Completion Semantics (Auditable closeout condition)
    final_payment_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    final_payment_confirmed_by_human_id UUID,
    final_payment_confirmed_at TIMESTAMPTZ,
    -- Auditable Payment Reversal / Correction Metadata
    payment_reversal_reason TEXT,
    payment_reversal_by_human_id UUID,
    payment_reversal_at TIMESTAMPTZ,
    -- Pre-operational Delivery Readiness Authorization Gate
    team_delivery_authorized BOOLEAN NOT NULL DEFAULT FALSE,
    team_delivery_authorized_by_human_id UUID,
    team_delivery_authorized_at TIMESTAMPTZ,
    team_delivery_authorization_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    -- Composite FK: Guarantees project references a partnership belonging to its own company!
    CONSTRAINT fk_projects_partnership_company FOREIGN KEY (partnership_id, company_id) 
        REFERENCES partnerships(id, company_id) ON DELETE RESTRICT,
    CONSTRAINT uq_projects_id_company UNIQUE (id, company_id),

    -- Format & Amount Checks
    CONSTRAINT chk_projects_currency CHECK (currency IS NULL OR currency ~* '^[A-Z]{3}$'),
    CONSTRAINT chk_project_deposit_sync CHECK (
        (deposit_paid = FALSE AND deposit_paid_at IS NULL AND deposit_confirmed_by_human_id IS NULL) OR 
        (deposit_paid = TRUE AND deposit_paid_at IS NOT NULL AND deposit_confirmed_by_human_id IS NOT NULL)
    ),
    CONSTRAINT chk_project_final_payment_sync CHECK (
        (final_payment_confirmed = FALSE AND final_payment_confirmed_at IS NULL AND final_payment_confirmed_by_human_id IS NULL) OR 
        (final_payment_confirmed = TRUE AND final_payment_confirmed_at IS NOT NULL AND final_payment_confirmed_by_human_id IS NOT NULL)
    ),
    CONSTRAINT chk_project_payment_reversal_sync CHECK (
        (payment_reversal_reason IS NULL AND payment_reversal_by_human_id IS NULL AND payment_reversal_at IS NULL) OR 
        (payment_reversal_reason IS NOT NULL AND TRIM(payment_reversal_reason) != '' AND payment_reversal_by_human_id IS NOT NULL AND payment_reversal_at IS NOT NULL)
    ),
    CONSTRAINT chk_project_feasibility_sync CHECK (
        (feasibility_status = 'pending_review' AND feasibility_reviewed_by_lead_id IS NULL AND feasibility_reviewed_at IS NULL) OR 
        (feasibility_status IN ('approved_by_lead', 'rejected_unfeasible') AND feasibility_reviewed_by_lead_id IS NOT NULL AND feasibility_reviewed_at IS NOT NULL)
    ),
    CONSTRAINT chk_project_delivery_auth_sync CHECK (
        (team_delivery_authorized = FALSE AND team_delivery_authorized_by_human_id IS NULL AND team_delivery_authorized_at IS NULL) OR 
        (team_delivery_authorized = TRUE AND team_delivery_authorized_by_human_id IS NOT NULL AND team_delivery_authorized_at IS NOT NULL)
    ),
    CONSTRAINT chk_project_amounts CHECK (
        (commercial_value IS NULL OR commercial_value >= 0) AND
        (deposit_required IS NULL OR deposit_required >= 0)
    )
);

COMMENT ON TABLE projects IS 'Client development projects. Requires Technical Lead feasibility, deposit, and delivery sign-off.';

CREATE TRIGGER trg_projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.3. Project Milestones (Hardened Acceptance & Revision Limits)
-- Specific deliverables, acceptance criteria, and inspection windows governing project execution.
-- ON DELETE RESTRICT protects delivery and payment history from accidental deletion.
-- -----------------------------------------------------------------------------
CREATE TABLE project_milestones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    company_id UUID NOT NULL,
    milestone_number INT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    deliverables TEXT[] NOT NULL DEFAULT '{}',
    acceptance_criteria TEXT NOT NULL,
    status milestone_status_type NOT NULL DEFAULT 'pending_delivery',
    milestone_payout_amount NUMERIC(12,2),
    due_date DATE,
    delivered_at TIMESTAMPTZ,
    inspection_window_days INT NOT NULL DEFAULT 5, -- Standard 5 business days
    inspection_deadline TIMESTAMPTZ,
    -- Detailed Acceptance Attribution
    accepted_at TIMESTAMPTZ,
    acceptance_type milestone_acceptance_type,
    accepted_by_contact_id UUID REFERENCES contacts(id),
    accepted_by_human_id UUID,
    acceptance_notes TEXT,
    -- Revision Tracking & Enforcement
    revision_rounds_used INT NOT NULL DEFAULT 0,
    max_in_scope_revisions INT NOT NULL DEFAULT 2,  -- Standard 2 in-scope revision rounds
    revision_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT fk_milestones_project_company FOREIGN KEY (project_id, company_id) 
        REFERENCES projects(id, company_id) ON DELETE RESTRICT,
    CONSTRAINT uq_project_milestone_number UNIQUE (project_id, milestone_number),
    CONSTRAINT chk_milestone_inspection_window CHECK (inspection_window_days > 0),
    CONSTRAINT chk_milestone_revision_limits CHECK (
        revision_rounds_used >= 0 AND 
        max_in_scope_revisions >= 0 AND 
        revision_rounds_used <= max_in_scope_revisions
    ),
    CONSTRAINT chk_milestone_acceptance_sync CHECK (
        (status != 'accepted' AND accepted_at IS NULL AND acceptance_type IS NULL) OR 
        (status = 'accepted' AND accepted_at IS NOT NULL AND acceptance_type IS NOT NULL)
    )
);

COMMENT ON TABLE project_milestones IS 'Milestone deliverables with business-day inspection window and explicit acceptance attribution.';

CREATE TRIGGER trg_project_milestones_updated_at
    BEFORE UPDATE ON project_milestones
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 3.4. Project Change Requests (Hardened Feasibility & Partner Decision Governance)
-- Formal governance for out-of-scope requests, timeline adjustments, and commercial impacts.
-- -----------------------------------------------------------------------------
CREATE TABLE project_change_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL,
    company_id UUID NOT NULL,
    cr_number INT NOT NULL,
    title TEXT NOT NULL,
    scope_description TEXT NOT NULL,
    cost_impact NUMERIC(12,2) NOT NULL DEFAULT 0,
    timeline_impact_days INT NOT NULL DEFAULT 0,
    -- Technical Lead Feasibility on Change Request
    feasibility_status feasibility_status_type NOT NULL DEFAULT 'pending_review',
    feasibility_reviewed_by_lead_id UUID,
    feasibility_reviewed_at TIMESTAMPTZ,
    feasibility_review_notes TEXT,
    -- Partner Decision Attribution
    status change_request_status_type NOT NULL DEFAULT 'draft',
    partner_decision_notes TEXT,
    decision_actor_type actor_type,
    decision_actor_identity TEXT,
    partner_approved_by_human_id UUID, -- Authorized operator recording partner approval
    decision_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT fk_change_requests_project_company FOREIGN KEY (project_id, company_id) 
        REFERENCES projects(id, company_id) ON DELETE RESTRICT,
    CONSTRAINT uq_project_cr_number UNIQUE (project_id, cr_number),
    CONSTRAINT chk_cr_partner_approval CHECK (
        status != 'approved_by_partner' OR 
        (decision_at IS NOT NULL AND partner_approved_by_human_id IS NOT NULL AND feasibility_status = 'approved_by_lead')
    )
);

COMMENT ON TABLE project_change_requests IS 'Formal change management for out-of-scope work requiring Lead feasibility and partner approval.';

CREATE TRIGGER trg_project_change_requests_updated_at
    BEFORE UPDATE ON project_change_requests
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 4. DELIVERY & GOVERNANCE TRIGGERS & BUSINESS LOGIC
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Secure Payment Confirmation & Privileged Reversal
-- Enforces:
-- 1. Deposit and Final payment confirmation require authenticated human operator (auth.uid() IS NOT NULL).
-- 2. Recorded identity must match auth.uid().
-- 3. Confirmation timestamps are database-generated (clock_timestamp()).
-- 4. Confirmed metadata cannot be arbitrarily modified or spoofed.
-- 5. Reversal requires admin/founder role, non-empty reversal reason, and records reversal attribution.
-- 6. Closed projects cannot have final payment confirmation reversed.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_project_payment_governance()
RETURNS TRIGGER AS $$
DECLARE
    v_is_deposit_confirming BOOLEAN := FALSE;
    v_is_deposit_reversing BOOLEAN := FALSE;
    v_is_final_confirming BOOLEAN := FALSE;
    v_is_final_reversing BOOLEAN := FALSE;
BEGIN
    -- Agents are strictly prohibited from modifying project payment parameters
    IF is_agent() THEN
        RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from modifying project payment parameters.';
    END IF;

    -- =========================================================================
    -- 1. INSERT BEHAVIOR (NEW PROJECT RECORD INITIALIZATION)
    -- =========================================================================
    IF OLD IS NULL THEN
        -- Reversal metadata can NEVER be set on initial project creation
        IF NEW.payment_reversal_reason IS NOT NULL OR 
           NEW.payment_reversal_by_human_id IS NOT NULL OR 
           NEW.payment_reversal_at IS NOT NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Payment reversal metadata cannot be initialized on new project creation.';
        END IF;

        -- Initial deposit payment confirmation validation if set on INSERT
        IF NEW.deposit_paid = TRUE THEN
            IF NOT is_human_operator() THEN
                RAISE EXCEPTION 'Security Policy Violation: Only an authorized human operator can confirm deposit payment.';
            END IF;
            IF auth.uid() IS NULL THEN
                RAISE EXCEPTION 'Security Policy Violation: Payment confirmation requires an authenticated session with non-null auth.uid().';
            END IF;
            IF NEW.deposit_confirmed_by_human_id IS DISTINCT FROM auth.uid() THEN
                RAISE EXCEPTION 'Security Policy Violation: deposit_confirmed_by_human_id must match authenticated operator ID (%).', auth.uid();
            END IF;
            NEW.deposit_paid_at = clock_timestamp();
        ELSE
            NEW.deposit_confirmed_by_human_id = NULL;
            NEW.deposit_paid_at = NULL;
        END IF;

        -- Final payment can NEVER be confirmed on initial project creation (scoping/feasibility stage)
        IF NEW.final_payment_confirmed = TRUE THEN
            RAISE EXCEPTION 'Lifecycle Violation: Final payment cannot be confirmed upon project creation.';
        ELSE
            NEW.final_payment_confirmed_by_human_id = NULL;
            NEW.final_payment_confirmed_at = NULL;
        END IF;

        RETURN NEW;
    END IF;

    -- =========================================================================
    -- 2. UPDATE BEHAVIOR & TRANSITION IDENTIFICATION
    -- =========================================================================
    v_is_deposit_confirming := (OLD.deposit_paid = FALSE AND NEW.deposit_paid = TRUE);
    v_is_deposit_reversing  := (OLD.deposit_paid = TRUE AND NEW.deposit_paid = FALSE);
    v_is_final_confirming   := (OLD.final_payment_confirmed = FALSE AND NEW.final_payment_confirmed = TRUE);
    v_is_final_reversing    := (OLD.final_payment_confirmed = TRUE AND NEW.final_payment_confirmed = FALSE);

    -- =========================================================================
    -- 3. CONFIRMATION GOVERNANCE (FALSE -> TRUE)
    -- =========================================================================
    IF v_is_deposit_confirming THEN
        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only an authorized human operator can confirm deposit payment.';
        END IF;
        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Payment confirmation requires an authenticated session with non-null auth.uid().';
        END IF;
        IF NEW.deposit_confirmed_by_human_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'Security Policy Violation: deposit_confirmed_by_human_id must match authenticated operator ID (%).', auth.uid();
        END IF;
        NEW.deposit_paid_at = clock_timestamp();
    END IF;

    IF v_is_final_confirming THEN
        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only an authorized human operator can confirm final payment.';
        END IF;
        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Payment confirmation requires an authenticated session with non-null auth.uid().';
        END IF;
        IF NEW.final_payment_confirmed_by_human_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'Security Policy Violation: final_payment_confirmed_by_human_id must match authenticated operator ID (%).', auth.uid();
        END IF;
        NEW.final_payment_confirmed_at = clock_timestamp();
    END IF;

    -- =========================================================================
    -- 4. CONFIRMED METADATA IMMUTABILITY (TRUE -> TRUE)
    -- =========================================================================
    IF OLD.deposit_paid = TRUE AND NEW.deposit_paid = TRUE THEN
        IF NEW.deposit_confirmed_by_human_id IS DISTINCT FROM OLD.deposit_confirmed_by_human_id OR
           NEW.deposit_paid_at IS DISTINCT FROM OLD.deposit_paid_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter deposit confirmation metadata on an already confirmed project.';
        END IF;
    END IF;

    IF OLD.final_payment_confirmed = TRUE AND NEW.final_payment_confirmed = TRUE THEN
        IF NEW.final_payment_confirmed_by_human_id IS DISTINCT FROM OLD.final_payment_confirmed_by_human_id OR
           NEW.final_payment_confirmed_at IS DISTINCT FROM OLD.final_payment_confirmed_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter final payment confirmation metadata on an already confirmed project.';
        END IF;
    END IF;

    -- =========================================================================
    -- 5. REVERSAL GOVERNANCE (TRUE -> FALSE)
    -- =========================================================================
    IF v_is_deposit_reversing OR v_is_final_reversing THEN
        -- Only admin/founder can reverse payment confirmation
        IF NOT is_admin_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only authorized admin or founder roles can reverse a payment confirmation.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Payment reversal requires an authenticated session with non-null auth.uid().';
        END IF;

        -- Terminal state guard: cannot reverse final payment on closed projects
        IF v_is_final_reversing AND (OLD.status = 'closed' OR NEW.status = 'closed') THEN
            RAISE EXCEPTION 'Integrity Violation: Final payment confirmation cannot be reversed on a closed project.';
        END IF;

        -- Mandatory non-empty reason required
        IF NEW.payment_reversal_reason IS NULL OR TRIM(NEW.payment_reversal_reason) = '' THEN
            RAISE EXCEPTION 'Audit Violation: Payment reversal requires a documented non-empty payment_reversal_reason.';
        END IF;

        -- Database securely overwrites reversal actor and timestamp
        NEW.payment_reversal_by_human_id = auth.uid();
        NEW.payment_reversal_at = clock_timestamp();

        -- Clear confirmation metadata for reversed items
        IF v_is_deposit_reversing THEN
            NEW.deposit_confirmed_by_human_id = NULL;
            NEW.deposit_paid_at = NULL;
        END IF;

        IF v_is_final_reversing THEN
            NEW.final_payment_confirmed_by_human_id = NULL;
            NEW.final_payment_confirmed_at = NULL;
        END IF;
    ELSE
        -- =====================================================================
        -- 6. REVERSAL METADATA PROTECTION WHEN NO REVERSAL OCCURS
        -- =====================================================================
        -- If payment confirmation is happening (and no other payment is being reversed), clear old reversal metadata
        IF (v_is_deposit_confirming OR v_is_final_confirming) THEN
            NEW.payment_reversal_reason = NULL;
            NEW.payment_reversal_by_human_id = NULL;
            NEW.payment_reversal_at = NULL;
        ELSE
            -- No payment status change: block any independent modification or clearance of reversal metadata
            IF NEW.payment_reversal_reason IS DISTINCT FROM OLD.payment_reversal_reason OR
               NEW.payment_reversal_by_human_id IS DISTINCT FROM OLD.payment_reversal_by_human_id OR
               NEW.payment_reversal_at IS DISTINCT FROM OLD.payment_reversal_at THEN
                RAISE EXCEPTION 'Security Policy Violation: Payment reversal metadata cannot be independently inserted, modified, or cleared outside of an authorized payment reversal.';
            END IF;
        END IF;
    END IF;

    -- =========================================================================
    -- 7. DEFENSIVE CLEANUP FOR UNCONFIRMED PAYMENTS
    -- =========================================================================
    IF NEW.deposit_paid = FALSE AND NOT v_is_deposit_reversing THEN
        NEW.deposit_confirmed_by_human_id = NULL;
        NEW.deposit_paid_at = NULL;
    END IF;

    IF NEW.final_payment_confirmed = FALSE AND NOT v_is_final_reversing THEN
        NEW.final_payment_confirmed_by_human_id = NULL;
        NEW.final_payment_confirmed_at = NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_project_payment
    BEFORE INSERT OR UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_project_payment_governance();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Technical Lead Feasibility Sign-Off
-- Ensures only an authenticated Technical Lead (is_technical_lead() = TRUE)
-- can approve or reject feasibility on projects.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_feasibility_lead_approval()
RETURNS TRIGGER AS $$
BEGIN
    -- If feasibility status is changing to approved or rejected
    IF NEW.feasibility_status IN ('approved_by_lead', 'rejected_unfeasible') AND 
       (OLD IS NULL OR OLD.feasibility_status != NEW.feasibility_status) THEN

        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from evaluating project technical feasibility.';
        END IF;

        IF NOT is_technical_lead() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only an authorized Technical Lead can approve or reject technical feasibility.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Feasibility evaluation requires an authenticated session with non-null auth.uid().';
        END IF;

        IF NEW.feasibility_reviewed_by_lead_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'Security Policy Violation: feasibility_reviewed_by_lead_id must match authenticated Technical Lead ID (%).', auth.uid();
        END IF;

        NEW.feasibility_reviewed_at = clock_timestamp();
    END IF;

    -- Prevent modification of reviewer identity or timestamp once set
    IF OLD IS NOT NULL AND OLD.feasibility_status IN ('approved_by_lead', 'rejected_unfeasible') AND 
       NEW.feasibility_status = OLD.feasibility_status THEN
        IF NEW.feasibility_reviewed_by_lead_id IS DISTINCT FROM OLD.feasibility_reviewed_by_lead_id OR 
           NEW.feasibility_reviewed_at IS DISTINCT FROM OLD.feasibility_reviewed_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter reviewer identity or timestamp of a signed feasibility determination.';
        END IF;
    END IF;

    -- Invalidate feasibility if material scope or delivery mode inputs change after approval
    IF OLD IS NOT NULL AND OLD.feasibility_status = 'approved_by_lead' AND NEW.feasibility_status = 'approved_by_lead' THEN
        IF NEW.scope_description IS DISTINCT FROM OLD.scope_description OR 
           NEW.delivery_mode IS DISTINCT FROM OLD.delivery_mode THEN
            NEW.feasibility_status = 'pending_review';
            NEW.feasibility_reviewed_by_lead_id = NULL;
            NEW.feasibility_reviewed_at = NULL;
            NEW.feasibility_review_notes = COALESCE(OLD.feasibility_review_notes, '') || ' [RESET: Scope or delivery mode altered on ' || clock_timestamp()::text || ']';
        END IF;
    END IF;

    -- Reset reviewer fields if pending_review
    IF NEW.feasibility_status = 'pending_review' THEN
        NEW.feasibility_reviewed_by_lead_id = NULL;
        NEW.feasibility_reviewed_at = NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_project_feasibility_lead
    BEFORE INSERT OR UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_feasibility_lead_approval();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Pre-Operational Delivery Authorization & Safe Invalidation
-- Ensures only an authorized human operator can authorize delivery team readiness.
-- Automatically invalidates authorization when material project parameters change.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_project_delivery_authorization()
RETURNS TRIGGER AS $$
BEGIN
    -- Handle transition to authorized
    IF NEW.team_delivery_authorized = TRUE AND (OLD IS NULL OR OLD.team_delivery_authorized = FALSE) THEN
        IF is_agent() THEN
            RAISE EXCEPTION 'Operational Security Violation: Agents are strictly prohibited from activating delivery team readiness.';
        END IF;

        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Operational Security Violation: Only an authorized human operator can activate delivery team readiness.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Operational Security Violation: Delivery team readiness requires an authenticated session with non-null auth.uid().';
        END IF;

        IF NEW.feasibility_status != 'approved_by_lead' THEN
            RAISE EXCEPTION 'Operational Security Violation: Cannot authorize delivery before Technical Lead feasibility approval.';
        END IF;

        IF NEW.team_delivery_authorized_by_human_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'Operational Security Violation: team_delivery_authorized_by_human_id must match authenticated operator ID (%).', auth.uid();
        END IF;

        NEW.team_delivery_authorized_at = clock_timestamp();
    END IF;

    -- Prevent modification of authorization identity or timestamp while active
    IF OLD IS NOT NULL AND OLD.team_delivery_authorized = TRUE AND NEW.team_delivery_authorized = TRUE THEN
        IF NEW.team_delivery_authorized_by_human_id IS DISTINCT FROM OLD.team_delivery_authorized_by_human_id OR 
           NEW.team_delivery_authorized_at IS DISTINCT FROM OLD.team_delivery_authorized_at THEN
            RAISE EXCEPTION 'Operational Security Violation: Cannot alter authorization identity or timestamp of an active delivery authorization.';
        END IF;
    END IF;

    -- Invalidate authorization whenever any material field affecting delivery readiness changes
    IF OLD IS NOT NULL AND OLD.team_delivery_authorized = TRUE AND NEW.team_delivery_authorized = TRUE THEN
        IF (
            NEW.scope_description IS DISTINCT FROM OLD.scope_description OR
            NEW.commercial_value IS DISTINCT FROM OLD.commercial_value OR
            NEW.deposit_required IS DISTINCT FROM OLD.deposit_required OR
            NEW.deposit_paid IS DISTINCT FROM OLD.deposit_paid OR
            NEW.feasibility_status IS DISTINCT FROM OLD.feasibility_status OR
            NEW.delivery_mode IS DISTINCT FROM OLD.delivery_mode OR
            NEW.partnership_id IS DISTINCT FROM OLD.partnership_id OR
            NEW.company_id IS DISTINCT FROM OLD.company_id
        ) THEN
            NEW.team_delivery_authorized = FALSE;
            NEW.team_delivery_authorized_by_human_id = NULL;
            NEW.team_delivery_authorized_at = NULL;
            NEW.team_delivery_authorization_notes = COALESCE(OLD.team_delivery_authorization_notes, '') || ' [REVOKED: Material project parameters altered on ' || clock_timestamp()::text || ']';
        END IF;
    END IF;

    -- Reset authorization fields if not authorized
    IF NEW.team_delivery_authorized = FALSE THEN
        NEW.team_delivery_authorized_by_human_id = NULL;
        NEW.team_delivery_authorized_at = NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_project_delivery_auth
    BEFORE INSERT OR UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_project_delivery_authorization();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Project State Machine & Mandatory Prerequisites
-- Prevents stage jumping and guarantees all quality and financial gates are satisfied.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_project_status_transitions()
RETURNS TRIGGER AS $$
DECLARE
    unaccepted_milestones_count INT;
BEGIN
    IF OLD IS NULL THEN
        -- Initial insert status must be scoping or feasibility_review
        IF NEW.status NOT IN ('scoping', 'feasibility_review', 'cancelled') THEN
            RAISE EXCEPTION 'Lifecycle Violation: New projects must initialize in scoping or feasibility_review stage.';
        END IF;
        RETURN NEW;
    END IF;

    -- If status is not changing, return
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;

    -- Terminal states cannot be exited
    IF OLD.status = 'cancelled' THEN
        RAISE EXCEPTION 'Lifecycle Violation: Cancelled projects are terminal and cannot be transitioned.';
    END IF;
    IF OLD.status = 'closed' THEN
        RAISE EXCEPTION 'Lifecycle Violation: Closed projects are terminal and cannot be transitioned.';
    END IF;

    -- Cancellation is allowed from any active non-terminal state
    IF NEW.status = 'cancelled' THEN
        RETURN NEW;
    END IF;

    -- Stage Progression Rules
    CASE OLD.status
        WHEN 'scoping' THEN
            IF NEW.status NOT IN ('feasibility_review') THEN
                RAISE EXCEPTION 'Lifecycle Violation: Projects in scoping can only transition to feasibility_review.';
            END IF;

        WHEN 'feasibility_review' THEN
            IF NEW.status = 'proposal_approved' THEN
                IF NEW.feasibility_status != 'approved_by_lead' THEN
                    RAISE EXCEPTION 'Quality Gate Violation: Cannot approve proposal without Lead Feasibility approval.';
                END IF;
            ELSIF NEW.status = 'scoping' THEN
                -- Permitted if scope requires rework
                NULL;
            ELSE
                RAISE EXCEPTION 'Lifecycle Violation: Projects in feasibility_review can only transition to proposal_approved or back to scoping.';
            END IF;

        WHEN 'proposal_approved' THEN
            IF NEW.status != 'deposit_pending' THEN
                RAISE EXCEPTION 'Lifecycle Violation: Projects with approved proposal must transition to deposit_pending.';
            END IF;

        WHEN 'deposit_pending' THEN
            IF NEW.status = 'in_progress' THEN
                IF NEW.feasibility_status != 'approved_by_lead' THEN
                    RAISE EXCEPTION 'Prerequisite Violation: in_progress requires Lead Feasibility approval.';
                END IF;
                IF NEW.deposit_paid != TRUE THEN
                    RAISE EXCEPTION 'Commercial Gate Violation: in_progress requires upfront deposit payment confirmation.';
                END IF;
                IF NEW.team_delivery_authorized != TRUE THEN
                    RAISE EXCEPTION 'Operational Gate Violation: in_progress requires human delivery authorization sign-off.';
                END IF;
            ELSE
                RAISE EXCEPTION 'Lifecycle Violation: Projects in deposit_pending can only transition to in_progress.';
            END IF;

        WHEN 'in_progress' THEN
            IF NEW.status != 'in_acceptance_review' THEN
                RAISE EXCEPTION 'Lifecycle Violation: Projects in_progress can only transition to in_acceptance_review.';
            END IF;

        WHEN 'in_acceptance_review' THEN
            IF NEW.status = 'completed' THEN
                -- Verify that all project milestones have been accepted
                SELECT COUNT(*) INTO unaccepted_milestones_count
                FROM project_milestones
                WHERE project_id = NEW.id AND status != 'accepted';

                IF unaccepted_milestones_count > 0 THEN
                    RAISE EXCEPTION 'Acceptance Violation: Cannot mark project completed while % milestones remain unaccepted.', unaccepted_milestones_count;
                END IF;
            ELSIF NEW.status = 'in_progress' THEN
                -- Allowed if revisions or rework are underway
                NULL;
            ELSE
                RAISE EXCEPTION 'Lifecycle Violation: Projects in acceptance review can only transition to completed or back to in_progress.';
            END IF;

        WHEN 'completed' THEN
            IF NEW.status NOT IN ('warranty_active', 'closed') THEN
                RAISE EXCEPTION 'Lifecycle Violation: Completed projects can only transition to warranty_active or closed.';
            END IF;

        WHEN 'warranty_active' THEN
            IF NEW.status != 'closed' THEN
                RAISE EXCEPTION 'Lifecycle Violation: Projects in warranty can only transition to closed.';
            END IF;

        ELSE
            RAISE EXCEPTION 'Lifecycle Violation: Unknown or unsupported project status transition from % to %.', OLD.status, NEW.status;
    END CASE;

    -- Financial Closeout Requirement: Transition to 'closed' requires confirmed final payment
    IF NEW.status = 'closed' THEN
        IF NEW.final_payment_confirmed != TRUE THEN
            RAISE EXCEPTION 'Financial Gate Violation: Projects cannot be closed without confirmed final payment (final_payment_confirmed = TRUE).';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_project_status_transitions
    BEFORE INSERT OR UPDATE OF status ON projects
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_project_status_transitions();

-- -----------------------------------------------------------------------------
-- Trigger Function: Milestone Deadline Calculation & Stale Clearance
-- Recalculates deadline whenever delivered_at, inspection_window_days, or timezone changes.
-- Clears deadline when delivery is withdrawn (delivered_at set to NULL).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_calculate_milestone_deadline()
RETURNS TRIGGER AS $$
DECLARE
    v_tz TEXT;
BEGIN
    -- If delivery is withdrawn, clear inspection deadline
    IF NEW.delivered_at IS NULL THEN
        NEW.inspection_deadline = NULL;
        RETURN NEW;
    END IF;

    -- Recalculate if delivered_at, window, or company timezone changed
    IF OLD IS NULL OR 
       NEW.delivered_at IS DISTINCT FROM OLD.delivered_at OR 
       NEW.inspection_window_days IS DISTINCT FROM OLD.inspection_window_days THEN

        -- Retrieve partner timezone from companies table via projects
        SELECT COALESCE(c.timezone, 'UTC') INTO v_tz
        FROM projects p
        JOIN companies c ON c.id = p.company_id
        WHERE p.id = NEW.project_id;

        NEW.inspection_deadline = add_business_days(NEW.delivered_at, NEW.inspection_window_days, v_tz);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_set_milestone_inspection_deadline
    BEFORE INSERT OR UPDATE OF delivered_at, inspection_window_days ON project_milestones
    FOR EACH ROW
    EXECUTE FUNCTION fn_calculate_milestone_deadline();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Milestone Transitions, Disputed Guards & Revision Limits
-- Prevents auto-acceptance of disputed milestones and enforces revision ceilings.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_milestone_governance()
RETURNS TRIGGER AS $$
BEGIN
    -- Enforce valid status transitions
    IF OLD IS NOT NULL AND NEW.status != OLD.status THEN
        -- Cannot accept a disputed or revision-requested milestone via automatic expiry
        IF OLD.status IN ('disputed', 'revisions_requested') AND NEW.status = 'accepted' THEN
            IF NEW.acceptance_type = 'automatic_window_expiry' THEN
                RAISE EXCEPTION 'Governance Violation: Disputed or revision-requested milestones cannot be accepted via automatic window expiry. Requires explicit sign-off.';
            END IF;
        END IF;

        -- Increment revision rounds used when transitioning to revisions_requested
        IF NEW.status = 'revisions_requested' AND OLD.status != 'revisions_requested' THEN
            IF OLD.revision_rounds_used >= OLD.max_in_scope_revisions THEN
                RAISE EXCEPTION 'Scope Boundary Violation: In-scope revision limit of % rounds reached. Additional revisions require an authorized Change Request.', OLD.max_in_scope_revisions;
            END IF;
            NEW.revision_rounds_used = OLD.revision_rounds_used + 1;
        END IF;

        -- Record acceptance attribution
        IF NEW.status = 'accepted' AND OLD.status != 'accepted' THEN
            IF NEW.accepted_at IS NULL THEN
                NEW.accepted_at = clock_timestamp();
            END IF;
            IF NEW.acceptance_type IS NULL THEN
                NEW.acceptance_type = 'explicit_partner_signoff';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_milestone_governance
    BEFORE UPDATE OF status ON project_milestones
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_milestone_governance();

-- -----------------------------------------------------------------------------
-- Trigger Function: Enforce Change Request Feasibility & Delivery Authorization Invalidation
-- Ensures Technical Lead approves CR feasibility before partner sign-off.
-- If approved CR increases cost or timeline, invalidates project delivery authorization.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_change_request_governance()
RETURNS TRIGGER AS $$
BEGIN
    -- Handle CR Technical Lead Feasibility
    IF NEW.feasibility_status IN ('approved_by_lead', 'rejected_unfeasible') AND 
       (OLD IS NULL OR OLD.feasibility_status != NEW.feasibility_status) THEN

        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from evaluating Change Request feasibility.';
        END IF;

        IF NOT is_technical_lead() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only an authorized Technical Lead can approve or reject Change Request feasibility.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Feasibility evaluation requires an authenticated session with non-null auth.uid().';
        END IF;

        IF NEW.feasibility_reviewed_by_lead_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'Security Policy Violation: feasibility_reviewed_by_lead_id must match authenticated Technical Lead ID (%).', auth.uid();
        END IF;

        NEW.feasibility_reviewed_at = clock_timestamp();
    END IF;

    -- Prevent modification of reviewer identity or timestamp once set
    IF OLD IS NOT NULL AND OLD.feasibility_status IN ('approved_by_lead', 'rejected_unfeasible') AND 
       NEW.feasibility_status = OLD.feasibility_status THEN
        IF NEW.feasibility_reviewed_by_lead_id IS DISTINCT FROM OLD.feasibility_reviewed_by_lead_id OR 
           NEW.feasibility_reviewed_at IS DISTINCT FROM OLD.feasibility_reviewed_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter reviewer identity or timestamp of a signed Change Request feasibility determination.';
        END IF;
    END IF;

    -- Prevent modification of partner approval identity or timestamp once approved
    IF OLD IS NOT NULL AND OLD.status = 'approved_by_partner' AND NEW.status = 'approved_by_partner' THEN
        IF NEW.partner_approved_by_human_id IS DISTINCT FROM OLD.partner_approved_by_human_id OR 
           NEW.decision_at IS DISTINCT FROM OLD.decision_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter partner approval identity or timestamp of an approved Change Request.';
        END IF;
    END IF;

    -- Handle partner approval of Change Request
    IF NEW.status = 'approved_by_partner' AND (OLD IS NULL OR OLD.status != 'approved_by_partner') THEN
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from recording partner Change Request approval.';
        END IF;

        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only an authorized human operator can record partner Change Request approval.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Recording approval requires an authenticated session with non-null auth.uid().';
        END IF;

        IF NEW.feasibility_status != 'approved_by_lead' THEN
            RAISE EXCEPTION 'Feasibility Prerequisite Violation: Cannot approve Change Request without prior Technical Lead Feasibility approval.';
        END IF;

        NEW.partner_approved_by_human_id = auth.uid();
        NEW.decision_at = clock_timestamp();

        -- If approved CR modifies project cost or timeline, update project and invalidate delivery authorization
        IF NEW.cost_impact != 0 OR NEW.timeline_impact_days != 0 THEN
            UPDATE projects
            SET 
                commercial_value = COALESCE(commercial_value, 0) + NEW.cost_impact,
                team_delivery_authorized = FALSE,
                team_delivery_authorized_by_human_id = NULL,
                team_delivery_authorized_at = NULL,
                team_delivery_authorization_notes = COALESCE(team_delivery_authorization_notes, '') || ' [RE-AUTHORIZATION REQUIRED: CR #' || NEW.cr_number || ' approved with cost/timeline adjustment on ' || clock_timestamp()::text || ']',
                updated_at = clock_timestamp()
            WHERE id = NEW.project_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_change_request_governance
    BEFORE INSERT OR UPDATE ON project_change_requests
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_change_request_governance();

-- =============================================================================
-- 5. AUDIT INTEGRATION (REUSING CORE fn_audit_log_capture)
-- =============================================================================

CREATE TRIGGER trg_audit_partnerships
    AFTER INSERT OR UPDATE OR DELETE ON partnerships
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log_capture();

CREATE TRIGGER trg_audit_projects
    AFTER INSERT OR UPDATE OR DELETE ON projects
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log_capture();

CREATE TRIGGER trg_audit_project_milestones
    AFTER INSERT OR UPDATE OR DELETE ON project_milestones
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log_capture();

CREATE TRIGGER trg_audit_project_change_requests
    AFTER INSERT OR UPDATE OR DELETE ON project_change_requests
    FOR EACH ROW EXECUTE FUNCTION fn_audit_log_capture();

-- =============================================================================
-- 6. PERFORMANCE & REFERENTIAL INDEXES (EXPANSION)
-- =============================================================================

CREATE INDEX idx_partnerships_company ON partnerships(company_id);
CREATE INDEX idx_projects_partnership_company ON projects(partnership_id, company_id);
CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_project_milestones_project_company ON project_milestones(project_id, company_id);
CREATE INDEX idx_project_milestones_status ON project_milestones(status);
CREATE INDEX idx_project_change_requests_project_company ON project_change_requests(project_id, company_id);
CREATE INDEX idx_project_change_requests_status ON project_change_requests(status);

-- =============================================================================
-- 7. ROW LEVEL SECURITY POLICIES (EXPANSION)
-- =============================================================================

ALTER TABLE partnerships ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_milestones ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_change_requests ENABLE ROW LEVEL SECURITY;

-- 7.1. Public Anonymous Denials
CREATE POLICY anon_deny_partnerships ON partnerships FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_projects ON projects FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_project_milestones ON project_milestones FOR ALL TO anon USING (false);
CREATE POLICY anon_deny_project_change_requests ON project_change_requests FOR ALL TO anon USING (false);

-- 7.2. Human Operator Full Operational Permissions
CREATE POLICY human_operator_all_partnerships ON partnerships FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_projects ON projects FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_project_milestones ON project_milestones FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY human_operator_all_project_change_requests ON project_change_requests FOR ALL TO authenticated 
    USING (is_human_operator()) WITH CHECK (is_human_operator());

-- 7.3. Scoped Agent Access Restriction (Zero Agent Access in Phase 2.1)
-- In the pre-operational stage, subagents (researcher, qualifier, drafter) are
-- strictly restricted from reading or mutating delivery tables. RLS defaults to DENY.

-- 7.4. Table Level Privilege Grants & Helper Function Permissions
GRANT EXECUTE ON FUNCTION is_technical_lead TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON partnerships, projects, project_milestones, project_change_requests TO authenticated;

-- =============================================================================
-- END OF DELIVERY & GOVERNANCE EXPANSION MIGRATION
-- =============================================================================
