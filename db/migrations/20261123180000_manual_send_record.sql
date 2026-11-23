-- =============================================================================
-- Closing the loop for a human-sent email, without giving the platform the ability to send
--
-- The build has no transport and must keep none. What was missing is the other half of the
-- workflow: the operator really does paste an approved message into Gmail and press send, and until
-- now the database had no way to hear about it, so every approved message stayed 'draft' for ever
-- and the pipeline could not tell "not sent yet" from "sent by hand".
--
-- What this migration adds is a *record*, not a capability:
--
-- * `sent_via` says how a message left — 'platform' (a dispatcher we do not have), 'manual_gmail'
--   (a human pasted it into the partnerships mailbox) or 'manual_other';
-- * `external_message_id` and `sent_recorded_by` make that record attributable and checkable
--   against the Gmail thread, and name the human who says they sent it.
--
-- The narrow exception, stated plainly: `fn_enforce_outreach_sending_state` refuses every transport
-- state to a session without a `dispatcher` claim, and that stays true. One statement may reach
-- 'sent' without a dispatcher — the UPDATE issued *inside* fn_record_manual_send(), by a human
-- operator, for a message that was approved and still a draft, carrying the manual-send columns and
-- a transaction-local token that only that function sets. A raw
-- `UPDATE outreach_messages SET sending_status = 'sent'` still fails, with or without the new
-- columns filled in; both cases are covered in tests/db/test_manual_send_db.py.
-- =============================================================================

BEGIN;

-- ---- 1. the record ---------------------------------------------------------
-- Additive and nullable: every existing row keeps saying nothing about how it left, which is the
-- truth. `external_message_id` already exists on this table (it was reserved for a dispatcher), so
-- it is added `IF NOT EXISTS` rather than duplicated.
ALTER TABLE outreach_messages
    ADD COLUMN IF NOT EXISTS sent_via text,
    ADD COLUMN IF NOT EXISTS external_message_id text,
    ADD COLUMN IF NOT EXISTS sent_recorded_by uuid;

COMMENT ON COLUMN outreach_messages.sent_via IS
    'How the message left the building: platform (a dispatcher, unused), manual_gmail (a human '
    'pasted it into the partnerships mailbox), manual_other. NULL means no send has been recorded.';
COMMENT ON COLUMN outreach_messages.sent_recorded_by IS
    'The human operator who reported the send. Never set by the platform, which cannot send.';

ALTER TABLE outreach_messages DROP CONSTRAINT IF EXISTS chk_outreach_sent_via;
ALTER TABLE outreach_messages ADD CONSTRAINT chk_outreach_sent_via
    CHECK (sent_via IS NULL OR sent_via IN ('platform', 'manual_gmail', 'manual_other'));

-- A manual send is attributed as a whole or not at all: a row that claims a human sent it by hand
-- must name the human and the message id the mailbox gave it, otherwise the record is unfalsifiable.
ALTER TABLE outreach_messages DROP CONSTRAINT IF EXISTS chk_outreach_manual_send_attribution;
ALTER TABLE outreach_messages ADD CONSTRAINT chk_outreach_manual_send_attribution
    CHECK (
        sent_via IS DISTINCT FROM 'manual_gmail'
        OR (sent_recorded_by IS NOT NULL AND NULLIF(BTRIM(external_message_id), '') IS NOT NULL)
    );

CREATE INDEX IF NOT EXISTS idx_outreach_messages_external_message_id
    ON outreach_messages (external_message_id)
    WHERE external_message_id IS NOT NULL;

-- ---- 2. the one predicate both triggers consult ----------------------------
-- Every clause is non-null-or-false, so this returns a real boolean rather than NULL for an INSERT
-- (where OLD is an all-null composite and `OLD.sending_status IN (…)` would be NULL).
--
-- The token is the load-bearing part: `eryndex.manual_send_message` is set transaction-locally by
-- fn_record_manual_send immediately before its UPDATE, and it must name *this* row. Reproducing the
-- columns by hand is therefore not enough — the statement has to come from the function.
CREATE OR REPLACE FUNCTION fn_manual_send_recording(
    p_new outreach_messages,
    p_old outreach_messages
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT COALESCE(
        p_new.sending_status = 'sent'
        AND p_old.sending_status IN ('draft', 'queued')
        AND p_new.approval_status = 'approved'
        AND p_new.sent_via = 'manual_gmail'
        AND p_new.sent_recorded_by IS NOT NULL
        AND auth.uid() IS NOT NULL
        AND p_new.sent_recorded_by = auth.uid()
        AND NULLIF(BTRIM(COALESCE(p_new.external_message_id, '')), '') IS NOT NULL
        AND COALESCE(current_setting('eryndex.manual_send_message', true), '') = p_new.id::text,
        false
    );
$$;

-- ---- 3. the dispatch gate, with exactly one door --------------------------
CREATE OR REPLACE FUNCTION fn_enforce_outreach_sending_state()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_manual_send boolean;
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RETURN NEW;
    END IF;

    v_manual_send := fn_manual_send_recording(NEW, OLD);

    IF NEW.sending_status IN ('sending', 'sent', 'delivered', 'opened', 'replied', 'bounced')
       AND NOT v_manual_send
       AND (auth.jwt() -> 'app_metadata' ->> 'user_role') IS DISTINCT FROM 'dispatcher' THEN
        RAISE EXCEPTION
            'Dispatch is disabled: no dispatcher identity is provisioned, so a message cannot be '
            'marked % by this session. Dispatch requires a deliberate migration that grants it.',
            NEW.sending_status
            USING ERRCODE = '42501';
    END IF;

    -- Approving a draft is a human act that must still respect suppression: a draft prepared
    -- before a domain or a company was suppressed must not become approvable afterwards.
    IF NEW.approval_status = 'approved' AND OLD.approval_status IS DISTINCT FROM 'approved' THEN
        IF EXISTS (SELECT 1 FROM companies c WHERE c.id = NEW.company_id AND c.is_suppressed) THEN
            RAISE EXCEPTION
                'Suppression Violation: company % is suppressed; its message cannot be approved.',
                NEW.company_id USING ERRCODE = 'P0001';
        END IF;
        IF EXISTS (SELECT 1 FROM contacts ct WHERE ct.id = NEW.contact_id AND ct.is_opted_out) THEN
            RAISE EXCEPTION
                'Suppression Violation: the recipient has opted out; the message cannot be approved.'
                USING ERRCODE = 'P0001';
        END IF;
        IF EXISTS (
            SELECT 1 FROM suppression_registry sr
             WHERE (sr.target_type = 'email'
                    AND sr.identifier = (SELECT LOWER(ct.email) FROM contacts ct WHERE ct.id = NEW.contact_id))
                OR (sr.target_type = 'domain'
                    AND sr.identifier IN (
                        SELECT LOWER(c.primary_domain) FROM companies c WHERE c.id = NEW.company_id
                        UNION
                        SELECT LOWER(cd.domain) FROM company_domains cd WHERE cd.company_id = NEW.company_id
                    ))
        ) THEN
            RAISE EXCEPTION
                'Suppression Violation: the recipient or one of the company domains is on the '
                'permanent suppression list; the message cannot be approved.'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    IF NEW.sending_status IS DISTINCT FROM OLD.sending_status THEN
        IF NOT (
            (OLD.sending_status = 'draft'   AND NEW.sending_status = 'queued')
            OR (OLD.sending_status = 'queued'  AND NEW.sending_status IN ('sending', 'failed', 'cancelled'))
            OR (OLD.sending_status = 'sending' AND NEW.sending_status IN ('sent', 'failed'))
            OR (OLD.sending_status = 'sent'    AND NEW.sending_status IN ('delivered', 'opened', 'replied', 'bounced'))
            OR v_manual_send
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

-- ---- 4. the lifecycle machine, with the same door -------------------------
-- This function's own state machine refuses draft -> sent and queued -> sent unconditionally
-- ("must transition to queued before sending"), so the manual send cannot be recorded while it is
-- left as written. The body below is the deployed one with `v_manual_send` guarding the two
-- transition rules that would otherwise fire; every other rule still applies to a manual send.
CREATE OR REPLACE FUNCTION fn_enforce_outreach_human_approval()
RETURNS TRIGGER AS $$
DECLARE
    contact_rec RECORD;
    v_manual_send boolean;
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
        v_manual_send := fn_manual_send_recording(NEW, OLD);

        IF OLD.sending_status IN ('replied', 'bounced') THEN
            RAISE EXCEPTION 'Lifecycle Violation: Outreach message in terminal sending status (%) cannot be transitioned.', OLD.sending_status;
        END IF;

        IF NOT v_manual_send THEN
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

-- ---- 5. the record of the act --------------------------------------------
-- Why a function rather than a policy: five preconditions have to hold together — a human caller, an
-- approved message, a message that has not left yet, a non-blank external id, and a Gmail-thread
-- identity — and the audit row has to be written in the same transaction as the state change. A
-- policy cannot express "and write an activity"; a function can, and it is one place to audit.
CREATE OR REPLACE FUNCTION fn_record_manual_send(
    p_message uuid,
    p_external_id text,
    p_note text DEFAULT NULL
) RETURNS outreach_messages
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_operator uuid := auth.uid();
    v_external text := BTRIM(COALESCE(p_external_id, ''));
    v_note text := NULLIF(BTRIM(COALESCE(p_note, '')), '');
    v_row outreach_messages;
    v_activity_id uuid;
BEGIN
    IF NOT is_human_operator() THEN
        RAISE EXCEPTION
            'Manual Send Refused: recording a manual send is a human act and this session role is '
            '% (is_human_operator() is false). Sign in as the operator: nothing was recorded.',
            current_actor_role()
            USING ERRCODE = '42501';
    END IF;

    IF v_operator IS NULL THEN
        RAISE EXCEPTION
            'Manual Send Refused: this session carries no subject (auth.uid() is null), so the send '
            'could not be attributed to a person. Reopen the operator session: nothing was recorded.'
            USING ERRCODE = '42501';
    END IF;

    IF v_external = '' THEN
        RAISE EXCEPTION
            'Manual Send Refused: the external message id is blank. Paste the Message-ID (or the '
            'thread id) Gmail shows for the message you actually sent, so the record can be '
            'checked against the mailbox. Nothing was recorded.'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_row FROM outreach_messages WHERE id = p_message FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Manual Send Refused: outreach message % does not exist. Nothing was recorded.', p_message
            USING ERRCODE = 'P0002';
    END IF;

    IF v_row.approval_status <> 'approved' THEN
        RAISE EXCEPTION
            'Manual Send Refused: outreach message % is % ; only an approved message may be '
            'recorded as sent (approve it at gate G3 first). Nothing was recorded.',
            p_message, v_row.approval_status
            USING ERRCODE = 'P0001';
    END IF;

    IF v_row.sending_status = 'sent' THEN
        RAISE EXCEPTION
            'Manual Send Refused: outreach message % already has a recorded send (sent_at %, '
            'sent_via %, external id %). A send is recorded once; nothing was rewritten.',
            p_message, v_row.sent_at, v_row.sent_via, v_row.external_message_id
            USING ERRCODE = 'P0001';
    END IF;

    IF v_row.sending_status NOT IN ('draft', 'queued') THEN
        RAISE EXCEPTION
            'Manual Send Refused: outreach message % is in sending_status % ; a manual send can '
            'only be recorded for a draft or a queued message. Nothing was recorded.',
            p_message, v_row.sending_status
            USING ERRCODE = 'P0001';
    END IF;

    -- The token fn_manual_send_recording() insists on: transaction-local, names this row, and is
    -- only ever set immediately above the UPDATE that carries the manual-send columns.
    PERFORM set_config('eryndex.manual_send_message', p_message::text, true);

    UPDATE outreach_messages
       SET sending_status = 'sent',
           sent_at = clock_timestamp(),
           sent_via = 'manual_gmail',
           external_message_id = v_external,
           sent_recorded_by = v_operator
     WHERE id = p_message
    RETURNING * INTO v_row;

    -- 'email_sent' is the deployed activity vocabulary for exactly this event. The description
    -- names the operator and the external id because that is what makes the row falsifiable.
    INSERT INTO activities (
        company_id,
        contact_id,
        activity_type,
        subject,
        description,
        performed_by_actor_type,
        performed_by_actor_identity,
        metadata
    ) VALUES (
        v_row.company_id,
        v_row.contact_id,
        'email_sent',
        format('Manual Gmail send of outreach message %s', p_message),
        format(
            'Operator %s recorded that outreach message %s was sent by hand from %s and not by the '
            'platform. External message id: %s.%s',
            v_operator, p_message, 'eryndexco@gmail.com', v_external,
            CASE WHEN v_note IS NULL THEN '' ELSE ' Note: ' || v_note END
        ),
        'human',
        v_operator::text,
        jsonb_build_object(
            'message_id', p_message,
            'external_message_id', v_external,
            'sent_via', 'manual_gmail',
            'recorded_by', v_operator,
            'recorded_by_function', 'fn_record_manual_send',
            'note', v_note
        )
    )
    RETURNING id INTO v_activity_id;

    IF v_activity_id IS NULL THEN
        RAISE EXCEPTION
            'Manual Send Refused: the send was not recorded on the activity trail, so it would be '
            'an unattributed claim. The whole transaction is being rolled back.'
            USING ERRCODE = 'P0001';
    END IF;

    RETURN v_row;
END;
$$ LANGUAGE plpgsql;

REVOKE ALL ON FUNCTION fn_record_manual_send(uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_record_manual_send(uuid, text, text) TO authenticated;

COMMIT;
