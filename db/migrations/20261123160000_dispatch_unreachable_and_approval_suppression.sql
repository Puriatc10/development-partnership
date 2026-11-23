-- =============================================================================
-- Two invariants the previous build stated but did not enforce
--
-- 1. "Dispatch is unreachable" was true of the code and false of the database: any authenticated
--    operator could type `sending_status='sent'`. Transport states now require a `dispatcher`
--    identity in the session claims. None is provisioned anywhere, so dispatch is unreachable in
--    the database too, and turning it on is a deliberate act rather than an UPDATE.
-- 2. Approval re-checks suppression. A draft written before a domain, a company or a contact was
--    suppressed could previously be approved afterwards; the queue step refused it, but the
--    approval recorded a human endorsement the platform should never have asked for.
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION fn_enforce_outreach_sending_state()
RETURNS TRIGGER
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RETURN NEW;
    END IF;

    IF NEW.sending_status IN ('sending', 'sent', 'delivered', 'opened', 'replied', 'bounced')
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

COMMIT;
