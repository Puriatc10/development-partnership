-- =============================================================================
-- Fix: every UPDATE-only guard in the core schema was dead code
--
-- Eleven guards were written `IF OLD IS NOT NULL AND …`. In PostgreSQL a row expression is NULL if
-- any field is NULL, and `OLD IS NOT NULL` on a composite is true only when *every* column is
-- non-null. Every table here has nullable columns (approved_by_human_id, sent_at, reviewed_at,
-- completed_at, …), so the condition was FALSE on exactly the transitions it was meant to police,
-- and the blocks never ran. Reproduced consequences: an approved message could jump draft -> sent in
-- one UPDATE; a body could be rewritten with approval intact; an approved scorecard could be edited
-- without being demoted; company lifecycle, task and conversation transitions were unguarded.
--
-- The fix is semantic: `TG_OP = 'UPDATE'` expresses "an old row exists". The bodies below are the
-- deployed ones with only that substitution (plus secondary domains in the suppression check).
-- =============================================================================

BEGIN;

-- ---- fn_enforce_outreach_human_approval --------------------------
CREATE OR REPLACE FUNCTION fn_enforce_outreach_human_approval()
RETURNS TRIGGER AS $$
DECLARE
    contact_rec RECORD;
BEGIN
    -- Block revenue sharing in cold outreach drafts
    IF NEW.proposed_commercial_model = 'strategic_revenue_sharing' THEN
        RAISE EXCEPTION 'Business Rule Violation: Strategic Revenue Sharing is strictly prohibited in outreach correspondence.';
    END IF;

    -- Block outreach creation or queueing for disqualified companies
    IF EXISTS (
        SELECT 1 FROM companies comp 
        WHERE comp.id = NEW.company_id 
          AND (comp.qualification_tier = 'disqualified' OR comp.lifecycle_stage = 'disqualified')
    ) THEN
        RAISE EXCEPTION 'Compliance Violation: Outreach messages cannot be created, queued, or dispatched for a disqualified company (%).', NEW.company_id;
    END IF;

    -- Approval status state machine rules
    IF TG_OP = 'UPDATE' AND NEW.approval_status != OLD.approval_status THEN
        -- Cannot jump directly from rejected to approved or pending_human_approval
        IF OLD.approval_status = 'rejected' AND NEW.approval_status != 'draft' THEN
            RAISE EXCEPTION 'Lifecycle Violation: Rejected outreach messages must return to draft before resubmission or approval.';
        END IF;

        -- Rejection requires non-empty reason and locks sending status
        IF NEW.approval_status = 'rejected' THEN
            IF NEW.rejection_reason IS NULL OR TRIM(NEW.rejection_reason) = '' THEN
                RAISE EXCEPTION 'Integrity Violation: Rejection of outreach message requires a non-empty rejection_reason.';
            END IF;
            IF NEW.sending_status NOT IN ('draft', 'cancelled') THEN
                RAISE EXCEPTION 'Integrity Violation: Rejected outreach message cannot enter dispatch states.';
            END IF;
        END IF;
    END IF;

    -- Sending status state machine progression rules
    IF TG_OP = 'UPDATE' AND NEW.sending_status != OLD.sending_status THEN
        IF OLD.sending_status IN ('replied', 'bounced') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Outreach message in terminal sending status (%) cannot be transitioned.', OLD.sending_status;
        END IF;

        IF OLD.sending_status = 'draft' AND NEW.sending_status NOT IN ('queued', 'cancelled') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Draft outreach message must transition to queued before sending.';
        END IF;

        IF OLD.sending_status = 'queued' AND NEW.sending_status NOT IN ('sending', 'cancelled', 'draft', 'failed') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Queued outreach message can only transition to sending, cancelled, draft, or failed.';
        END IF;

        IF OLD.sending_status = 'sending' AND NEW.sending_status NOT IN ('sent', 'failed') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Sending outreach message can only transition to sent or failed.';
        END IF;

        IF OLD.sending_status = 'sent' AND NEW.sending_status NOT IN ('delivered', 'bounced', 'opened', 'replied') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Sent outreach message cannot transition backward to %.', NEW.sending_status;
        END IF;

        IF OLD.sending_status = 'delivered' AND NEW.sending_status NOT IN ('opened', 'replied') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Delivered outreach message cannot transition backward to %.', NEW.sending_status;
        END IF;

        IF OLD.sending_status = 'opened' AND NEW.sending_status NOT IN ('replied') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Opened outreach message cannot transition backward to %.', NEW.sending_status;
        END IF;

        -- Delivery events must not alter approval status or approval metadata
        IF NEW.sending_status IN ('delivered', 'opened', 'replied', 'bounced') THEN
            IF NEW.approval_status != OLD.approval_status OR
               NEW.approved_by_human_id IS DISTINCT FROM OLD.approved_by_human_id OR
               NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
                RAISE EXCEPTION 'Integrity Violation: Delivery event status updates cannot modify message approval status or approval metadata.';
            END IF;
        END IF;
    END IF;

    -- Invalidation on material edits: If an approved message has its content, recipient, targeting, commercial meaning, or personalization altered, reset to draft!
    IF TG_OP = 'UPDATE' AND OLD.approval_status = 'approved' AND NEW.approval_status = 'approved' THEN
        IF (
            NEW.body IS DISTINCT FROM OLD.body OR
            NEW.subject IS DISTINCT FROM OLD.subject OR
            NEW.channel IS DISTINCT FROM OLD.channel OR
            NEW.company_id IS DISTINCT FROM OLD.company_id OR
            NEW.contact_id IS DISTINCT FROM OLD.contact_id OR
            NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR
            NEW.sequence_step IS DISTINCT FROM OLD.sequence_step OR
            NEW.parent_message_id IS DISTINCT FROM OLD.parent_message_id OR
            NEW.personalization_evidence_ids IS DISTINCT FROM OLD.personalization_evidence_ids OR
            NEW.personalization_rationale IS DISTINCT FROM OLD.personalization_rationale OR
            NEW.proposed_commercial_model IS DISTINCT FROM OLD.proposed_commercial_model
        ) THEN
            -- Invalidate approval due to material modification
            NEW.approval_status = 'draft';
            NEW.approved_by_human_id = NULL;
            NEW.approved_at = NULL;
            NEW.is_admin_override = FALSE;
            NEW.override_reason = NULL;
            NEW.override_by_human_id = NULL;
            NEW.sending_status = 'draft';
        END IF;
    END IF;

    -- Protect approval metadata against arbitrary modification after approval
    IF TG_OP = 'UPDATE' AND OLD.approval_status = 'approved' AND NEW.approval_status = 'approved' THEN
        IF NEW.approved_by_human_id IS DISTINCT FROM OLD.approved_by_human_id OR
           NEW.approved_at IS DISTINCT FROM OLD.approved_at OR
           NEW.is_admin_override IS DISTINCT FROM OLD.is_admin_override OR
           NEW.override_reason IS DISTINCT FROM OLD.override_reason OR
           NEW.override_by_human_id IS DISTINCT FROM OLD.override_by_human_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter approval identity, timestamps, or override metadata on an already approved message.';
        END IF;
    END IF;

    -- Handle transition to approved
    IF NEW.approval_status = 'approved' AND (OLD IS NULL OR OLD.approval_status != 'approved') THEN
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from approving outreach messages.';
        END IF;

        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only authenticated human operators can approve outreach messages.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Approval requires an authenticated session with non-null auth.uid().';
        END IF;

        -- Admin override check
        IF NEW.is_admin_override = TRUE THEN
            IF NOT is_admin_operator() THEN
                RAISE EXCEPTION 'Security Policy Violation: Only authorized admin or founder roles can perform an administrative override.';
            END IF;

            IF NEW.override_reason IS NULL OR TRIM(NEW.override_reason) = '' THEN
                RAISE EXCEPTION 'Audit Violation: Admin override requires a non-empty override_reason.';
            END IF;

            IF NEW.override_by_human_id IS NULL OR NEW.override_by_human_id != auth.uid() THEN
                RAISE EXCEPTION 'Audit Violation: override_by_human_id must match authenticated admin user ID (%).', auth.uid();
            END IF;

            IF NEW.approved_by_human_id IS NULL THEN
                RAISE EXCEPTION 'Audit Violation: approved_by_human_id must be specified when performing admin override.';
            END IF;
        ELSE
            -- Standard Approval: approved_by_human_id must match auth.uid()
            IF NEW.approved_by_human_id IS DISTINCT FROM auth.uid() THEN
                RAISE EXCEPTION 'Security Policy Violation: approved_by_human_id (%) must match authenticated user (%).', NEW.approved_by_human_id, auth.uid();
            END IF;

            -- Non-override approvals must not contain override metadata
            NEW.override_reason = NULL;
            NEW.override_by_human_id = NULL;
        END IF;

        -- Overwrite with trusted database timestamp
        NEW.approved_at = clock_timestamp();
    END IF;

    -- Reset approval fields if approval_status is not approved
    IF NEW.approval_status != 'approved' THEN
        NEW.approved_by_human_id = NULL;
        NEW.approved_at = NULL;
        NEW.is_admin_override = FALSE;
        NEW.override_reason = NULL;
        NEW.override_by_human_id = NULL;
    END IF;

    -- Verify dispatch authorization & suppression check:
    -- Any status other than 'draft' or 'cancelled' implies dispatch or downstream event
    IF NEW.sending_status IN ('queued', 'sending', 'sent', 'delivered', 'opened', 'replied', 'bounced', 'failed') THEN
        IF NEW.approval_status != 'approved' OR NEW.approved_by_human_id IS NULL OR NEW.approved_at IS NULL THEN
            RAISE EXCEPTION 'Integrity Violation: Outreach message % cannot enter dispatch or downstream status (%) without valid human approval.', NEW.id, NEW.sending_status;
        END IF;

        -- Fetch contact details and company domain
        SELECT LOWER(c.email) AS email, LOWER(c.linkedin_url) AS linkedin_url, LOWER(comp.primary_domain) AS domain
        INTO contact_rec
        FROM contacts c
        JOIN companies comp ON comp.id = c.company_id
        WHERE c.id = NEW.contact_id;

        -- Check permanent multi-channel suppression directly against registry (trusted database gate)
        IF EXISTS (
            SELECT 1 FROM suppression_registry sr
            WHERE (sr.target_type = 'email' AND sr.identifier = contact_rec.email)
               OR (sr.target_type = 'domain' AND sr.identifier = contact_rec.domain)
               OR (sr.target_type = 'domain' AND EXISTS (
                       SELECT 1 FROM company_domains cd
                        WHERE cd.company_id = comp.id AND LOWER(cd.domain) = sr.identifier
                   ))
               OR (sr.target_type = 'linkedin' AND sr.identifier = contact_rec.linkedin_url)
        ) THEN
            RAISE EXCEPTION 'Suppression Violation: Recipient or domain is on the permanent suppression list.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---- fn_enforce_lead_qualification_rules -------------------------
CREATE OR REPLACE FUNCTION fn_enforce_lead_qualification_rules()
RETURNS TRIGGER AS $$
DECLARE
    has_dq BOOLEAN;
BEGIN
    -- 1. Validate that positive scores possess structured supporting evidence
    IF NEW.customer_flow_score > 0 AND (NEW.dimension_evidence -> 'customer_flow' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: customer_flow_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.development_demand_score > 0 AND (NEW.dimension_evidence -> 'development_demand' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: development_demand_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.repeatability_orders_score > 0 AND (NEW.dimension_evidence -> 'repeatability_orders' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: repeatability_orders_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.technical_fit_score > 0 AND (NEW.dimension_evidence -> 'technical_fit' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: technical_fit_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.business_size_score > 0 AND (NEW.dimension_evidence -> 'business_size' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: business_size_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.decision_maker_accessibility_score > 0 AND (NEW.dimension_evidence -> 'decision_maker_accessibility' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: decision_maker_accessibility_score > 0 requires documented evidence in dimension_evidence.';
    END IF;
    IF NEW.commercial_fit_score > 0 AND (NEW.dimension_evidence -> 'commercial_fit' ->> 'evidence') IS NULL THEN
        RAISE EXCEPTION 'Evidence Integrity Violation: commercial_fit_score > 0 requires documented evidence in dimension_evidence.';
    END IF;

    -- 2. Validate Tier assignment strictly against approved thresholds
    has_dq := (cardinality(NEW.disqualification_reasons) > 0);
    IF has_dq OR NEW.overall_score < 40 THEN
        IF NEW.qualification_tier != 'disqualified' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Leads with disqualification reasons or score < 40 must be disqualified.';
        END IF;
    ELSIF NEW.overall_score >= 80 THEN
        IF NEW.qualification_tier != 'tier_1_priority' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Score >= 80 must be assigned tier_1_priority.';
        END IF;
    ELSIF NEW.overall_score >= 60 THEN
        IF NEW.qualification_tier != 'tier_2_prospective' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Score between 60-79 must be assigned tier_2_prospective.';
        END IF;
    ELSE
        IF NEW.qualification_tier != 'tier_3_deferred' THEN
            RAISE EXCEPTION 'Tier Integrity Violation: Score between 40-59 must be assigned tier_3_deferred.';
        END IF;
    END IF;

    -- 3. Invalidate approval on material score, evidence, tier, or recommendation modification
    IF TG_OP = 'UPDATE' AND OLD.review_status = 'approved' AND NEW.review_status = 'approved' THEN
        IF (
            NEW.company_id IS DISTINCT FROM OLD.company_id OR
            NEW.overall_score IS DISTINCT FROM OLD.overall_score OR
            NEW.customer_flow_score IS DISTINCT FROM OLD.customer_flow_score OR
            NEW.development_demand_score IS DISTINCT FROM OLD.development_demand_score OR
            NEW.repeatability_orders_score IS DISTINCT FROM OLD.repeatability_orders_score OR
            NEW.technical_fit_score IS DISTINCT FROM OLD.technical_fit_score OR
            NEW.business_size_score IS DISTINCT FROM OLD.business_size_score OR
            NEW.decision_maker_accessibility_score IS DISTINCT FROM OLD.decision_maker_accessibility_score OR
            NEW.commercial_fit_score IS DISTINCT FROM OLD.commercial_fit_score OR
            NEW.dimension_evidence IS DISTINCT FROM OLD.dimension_evidence OR
            NEW.qualification_tier IS DISTINCT FROM OLD.qualification_tier OR
            NEW.disqualification_reasons IS DISTINCT FROM OLD.disqualification_reasons OR
            NEW.disqualification_notes IS DISTINCT FROM OLD.disqualification_notes OR
            NEW.recommended_next_action IS DISTINCT FROM OLD.recommended_next_action
        ) THEN
            NEW.review_status = 'draft';
            NEW.reviewed_by_human_id = NULL;
            NEW.reviewed_at = NULL;
        END IF;
    END IF;

    -- 4. Protect approval metadata against arbitrary modification after approval
    IF TG_OP = 'UPDATE' AND OLD.review_status = 'approved' AND NEW.review_status = 'approved' THEN
        IF NEW.reviewed_by_human_id IS DISTINCT FROM OLD.reviewed_by_human_id OR
           NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Cannot alter reviewer identity or timestamp of an approved qualification.';
        END IF;
    END IF;

    -- 5. Enforce Human Gate on Approval
    IF NEW.review_status = 'approved' AND (OLD IS NULL OR OLD.review_status != 'approved') THEN
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from approving lead qualifications.';
        END IF;

        IF NOT is_human_operator() THEN
            RAISE EXCEPTION 'Security Policy Violation: Only authenticated human operators can approve lead qualifications.';
        END IF;

        IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'Security Policy Violation: Qualification approval requires an authenticated session with non-null auth.uid().';
        END IF;

        IF NEW.reviewed_by_human_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'Security Policy Violation: reviewed_by_human_id must match authenticated operator ID (%).', auth.uid();
        END IF;

        NEW.reviewed_at = clock_timestamp();
    END IF;

    -- Review status transition rules
    IF TG_OP = 'UPDATE' AND NEW.review_status != OLD.review_status THEN
        -- Rejected qualifications must return to draft before resubmission
        IF OLD.review_status = 'rejected' AND NEW.review_status NOT IN ('draft') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Rejected qualifications must return to draft before resubmission or approval.';
        END IF;

        -- Rejection requires disqualification reasons or notes
        IF NEW.review_status = 'rejected' THEN
            IF NEW.disqualification_notes IS NULL AND cardinality(NEW.disqualification_reasons) = 0 THEN
                RAISE EXCEPTION 'Integrity Violation: Rejection of lead qualification requires documented disqualification reasons or notes.';
            END IF;
        END IF;
    END IF;

    -- Reset reviewer fields if review_status is not approved
    IF NEW.review_status != 'approved' THEN
        NEW.reviewed_by_human_id = NULL;
        NEW.reviewed_at = NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---- fn_enforce_company_lifecycle_transitions --------------------
CREATE OR REPLACE FUNCTION fn_enforce_company_lifecycle_transitions()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.lifecycle_stage != OLD.lifecycle_stage THEN
        -- Agents are strictly prohibited from changing company lifecycle stage
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are strictly prohibited from modifying company lifecycle stages.';
        END IF;

        -- Disqualified company cannot enter active outreach, negotiation, or partnership
        IF OLD.lifecycle_stage = 'disqualified' AND NEW.lifecycle_stage NOT IN ('identified', 'researching', 'churned') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Disqualified company cannot transition directly to %. Re-evaluation via researching or identified required.', NEW.lifecycle_stage;
        END IF;

        -- Outreach stages require qualification tier to be qualified (not null and not disqualified)
        IF NEW.lifecycle_stage IN ('outreach_queued', 'in_outreach') THEN
            IF NEW.qualification_tier IS NULL OR NEW.qualification_tier = 'disqualified' THEN
                RAISE EXCEPTION 'Lifecycle Violation: Company cannot enter outreach stages while unqualified or disqualified.';
            END IF;
        END IF;

        -- Identified cannot jump directly to active execution or partnership
        IF OLD.lifecycle_stage = 'identified' AND NEW.lifecycle_stage IN ('in_outreach', 'active_conversation', 'negotiating', 'partner_active', 'partner_dormant') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Identified companies must be researched and qualified before entering %.', NEW.lifecycle_stage;
        END IF;

        -- Churned partner re-activation rules
        IF OLD.lifecycle_stage = 'churned' AND NEW.lifecycle_stage NOT IN ('identified', 'researching', 'partner_dormant') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Churned partners can only re-enter lifecycle via identified, researching, or partner_dormant.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---- fn_enforce_task_governance ----------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_task_governance()
RETURNS TRIGGER AS $$
BEGIN
    -- Operational lifecycle rules for all callers
    IF NEW.status = 'completed' AND (OLD IS NULL OR OLD.status != 'completed') THEN
        IF NEW.completed_at IS NULL THEN
            NEW.completed_at = clock_timestamp();
        END IF;
    ELSIF NEW.status != 'completed' THEN
        NEW.completed_at = NULL;
    END IF;

    -- Only restrict agents; human operators retain full operational management
    IF is_agent() THEN
        -- Prevent agents from modifying or reopening completed or cancelled tasks
        IF TG_OP = 'UPDATE' AND OLD.status IN ('completed', 'cancelled') THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot reopen, modify, or transition completed or cancelled tasks.';
        END IF;

        -- Agents cannot jump pending directly to completed without in_progress
        IF TG_OP = 'UPDATE' AND OLD.status = 'pending' AND NEW.status = 'completed' THEN
            RAISE EXCEPTION 'Lifecycle Violation: Tasks must be transitioned to in_progress before completion.';
        END IF;

        -- Prevent modification of assignment and scope fields
        IF NEW.assigned_actor_type IS DISTINCT FROM OLD.assigned_actor_type OR
           NEW.assigned_actor_identity IS DISTINCT FROM OLD.assigned_actor_identity THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot reassign tasks or alter task actor assignment.';
        END IF;

        IF NEW.campaign_id IS DISTINCT FROM OLD.campaign_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task campaign_id scope.';
        END IF;

        IF NEW.company_id IS DISTINCT FROM OLD.company_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task company_id scope.';
        END IF;

        IF NEW.contact_id IS DISTINCT FROM OLD.contact_id THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task contact_id scope.';
        END IF;

        IF NEW.task_type IS DISTINCT FROM OLD.task_type THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task_type.';
        END IF;

        IF NEW.title IS DISTINCT FROM OLD.title THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task title.';
        END IF;

        IF NEW.priority IS DISTINCT FROM OLD.priority THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task priority.';
        END IF;

        IF NEW.due_date IS DISTINCT FROM OLD.due_date THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task due_date.';
        END IF;

        IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents cannot alter task created_at.';
        END IF;

        -- Prevent tampering with task completed_at timestamp
        IF OLD.status = 'completed' AND NEW.status = 'completed' THEN
            IF NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                RAISE EXCEPTION 'Security Policy Violation: Agents cannot tamper with task completed_at timestamp.';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---- fn_enforce_conversation_status_transitions ------------------
CREATE OR REPLACE FUNCTION fn_enforce_conversation_status_transitions()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.status != OLD.status THEN
        -- Only human operators can change conversation status
        IF is_agent() THEN
            RAISE EXCEPTION 'Security Policy Violation: Agents are not permitted to modify conversation status.';
        END IF;

        -- Reopening terminal conversations requires human operator authority
        IF OLD.status IN ('closed_lost', 'closed_won') AND NEW.status NOT IN ('closed_lost', 'closed_won') THEN
            IF NOT is_human_operator() THEN
                RAISE EXCEPTION 'Security Policy Violation: Only authenticated human operators can reopen closed conversations.';
            END IF;
            IF NEW.status NOT IN ('active', 'paused') THEN
                RAISE EXCEPTION 'Lifecycle Violation: Closed conversations can only be reopened to active or paused stage.';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Additional guards the audit showed were missing
-- =============================================================================

-- 1. A scorecard may only be *created* as a draft, whatever identity creates it. Approval is a
--    separate human action on an existing draft; the human INSERT policy previously allowed an
--    already-approved row naming its own reviewer in one statement.
CREATE OR REPLACE FUNCTION fn_enforce_qualification_insert_draft()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    -- Owner / migration / test-fixture context: no actor to attribute an approval to.
    IF auth.uid() IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.review_status <> 'draft' THEN
        RAISE EXCEPTION
            'Qualification Integrity Violation: a scorecard must be inserted as a draft (attempted %). '
            'Approval is a separate human action.', NEW.review_status
            USING ERRCODE = '42501';
    END IF;
    IF NEW.reviewed_by_human_id IS NOT NULL OR NEW.reviewed_at IS NOT NULL THEN
        RAISE EXCEPTION
            'Qualification Integrity Violation: a new scorecard may not carry a reviewer.'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_lead_qualifications_insert_draft ON lead_qualifications;
CREATE TRIGGER trg_lead_qualifications_insert_draft
    BEFORE INSERT ON lead_qualifications
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_qualification_insert_draft();

-- 2. A new outreach message is a draft, and only for a company that is actually qualified. The
--    agent policy said this; the human policy did not, so the operator (the identity the CLI uses)
--    could insert an already-approved, already-queued message with no scorecard at all.
CREATE OR REPLACE FUNCTION fn_enforce_outreach_insert_preconditions()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF auth.uid() IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.approval_status <> 'draft' OR NEW.sending_status <> 'draft' THEN
        RAISE EXCEPTION
            'Outreach Integrity Violation: a new message must be a draft (approval=%, sending=%); '
            'approval and dispatch are separate gated actions.', NEW.approval_status, NEW.sending_status
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM lead_qualifications q
         WHERE q.company_id = NEW.company_id
           AND q.review_status = 'approved'
           AND q.qualification_tier <> 'disqualified'
    ) THEN
        RAISE EXCEPTION
            'Outreach Integrity Violation: company % has no approved, non-disqualified scorecard.',
            NEW.company_id
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM companies c
         WHERE c.id = NEW.company_id
           AND c.lifecycle_stage IN ('qualified', 'outreach_queued', 'in_outreach')
    ) THEN
        RAISE EXCEPTION
            'Outreach Integrity Violation: company % is not in a qualified lifecycle stage.',
            NEW.company_id
            USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_outreach_insert_preconditions ON outreach_messages;
CREATE TRIGGER trg_outreach_insert_preconditions
    BEFORE INSERT ON outreach_messages
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_outreach_insert_preconditions();

-- 3. Sending states move in one direction, an approval is required to leave draft, and an approved
--    message's body and approver are immutable until the approval is revoked.
CREATE OR REPLACE FUNCTION fn_enforce_outreach_sending_state()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RETURN NEW;
    END IF;

    IF NEW.sending_status IS DISTINCT FROM OLD.sending_status THEN
        IF NOT (
            (OLD.sending_status = 'draft'   AND NEW.sending_status = 'queued')
            OR (OLD.sending_status = 'queued'  AND NEW.sending_status IN ('sending', 'failed', 'cancelled'))
            OR (OLD.sending_status = 'sending' AND NEW.sending_status IN ('sent', 'failed'))
            OR (OLD.sending_status = 'sent'    AND NEW.sending_status IN ('delivered', 'opened', 'replied', 'bounced'))
        ) THEN
            RAISE EXCEPTION
                'Sending state violation: % -> % is not a permitted transition.',
                OLD.sending_status, NEW.sending_status
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    IF NEW.sending_status <> 'draft' AND NEW.approval_status <> 'approved' THEN
        RAISE EXCEPTION
            'Outreach Integrity Violation: a message may not leave draft without human approval.'
            USING ERRCODE = 'P0001';
    END IF;

    IF OLD.approval_status = 'approved' THEN
        IF NEW.body IS DISTINCT FROM OLD.body OR NEW.subject IS DISTINCT FROM OLD.subject
           OR NEW.personalization_evidence_ids IS DISTINCT FROM OLD.personalization_evidence_ids THEN
            RAISE EXCEPTION
                'Outreach Integrity Violation: the content of an approved message is immutable; revoke approval first.'
                USING ERRCODE = 'P0001';
        END IF;
        IF NEW.approved_by_human_id IS DISTINCT FROM OLD.approved_by_human_id THEN
            RAISE EXCEPTION
                'Outreach Integrity Violation: approved_by_human_id may not be rewritten.'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_outreach_sending_state ON outreach_messages;
CREATE TRIGGER trg_outreach_sending_state
    BEFORE UPDATE ON outreach_messages
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_outreach_sending_state();

-- 4. Discovery candidates are deleted by fixtures and probes; a review that can vanish unrecorded
--    is not an audit trail.
DROP TRIGGER IF EXISTS trg_discovery_candidates_audit ON discovery_candidates;
CREATE TRIGGER trg_discovery_candidates_audit
    AFTER INSERT OR UPDATE OR DELETE ON discovery_candidates
    FOR EACH ROW
    EXECUTE FUNCTION fn_audit_log_capture();

COMMIT;
