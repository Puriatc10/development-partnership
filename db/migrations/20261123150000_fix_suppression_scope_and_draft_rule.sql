-- =============================================================================
-- Fix two defects in 20261123140000_fix_update_guard_semantics
--
-- 1. The secondary-domain clause added to fn_enforce_outreach_human_approval referenced `comp`,
--    an alias of a SELECT ... INTO that has already finished, so any statement reaching the
--    dispatch-status branch raised "missing FROM-clause entry for table "comp"" instead of
--    performing the suppression check. It now correlates on NEW.company_id.
-- 2. The insert-is-draft rule lived in its own BEFORE INSERT trigger, which raced another trigger
--    on the same table and could report the evidence rule first. It is now the first check inside
--    fn_enforce_lead_qualification_rules, so the reason is always the draft rule.
-- =============================================================================

BEGIN;

-- ---- fn_enforce_outreach_human_approval ------------------------------------
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
                        WHERE cd.company_id = NEW.company_id AND LOWER(cd.domain) = sr.identifier
                   ))
               OR (sr.target_type = 'linkedin' AND sr.identifier = contact_rec.linkedin_url)
        ) THEN
            RAISE EXCEPTION 'Suppression Violation: Recipient or domain is on the permanent suppression list.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---- fn_enforce_lead_qualification_rules -----------------------------------
CREATE OR REPLACE FUNCTION fn_enforce_lead_qualification_rules()
RETURNS TRIGGER AS $$
DECLARE
    has_dq BOOLEAN;
BEGIN
    -- 0. A scorecard is created as a draft by a person or an agent; approval is a separate human
    --    action on an existing draft. (`auth.uid()` is NULL only in owner/migration/test context.)
    IF TG_OP = 'INSERT' AND auth.uid() IS NOT NULL THEN
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
    END IF;

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

DROP TRIGGER IF EXISTS trg_lead_qualifications_insert_draft ON lead_qualifications;
DROP FUNCTION IF EXISTS fn_enforce_qualification_insert_draft();

COMMIT;
