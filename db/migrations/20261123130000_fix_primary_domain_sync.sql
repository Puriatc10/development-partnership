-- =============================================================================
-- Fix: the one-primary-domain index made a primary-domain change impossible
--
-- `uq_company_domains_one_primary` (20261123121000) is a partial unique index on
-- `company_domains(company_id) WHERE is_primary`. The trigger that maintains the primary row
-- inserted the new primary *before* touching the old one, so any company that already had a
-- primary domain hit the index on the way through and the change failed with 23505 — before the
-- duplicate-domain check could ever run. Found by the database security suite, which is exactly
-- the kind of regression a constraint is supposed to surface loudly.
--
-- The fix is ordering, not a weaker constraint: demote the company's other primaries first, then
-- insert (or adopt) the new row, then prove ownership, then mark it primary. At no point does the
-- company hold two primaries, so the index stays strict.
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION fn_sync_company_primary_domain()
RETURNS TRIGGER AS $$
DECLARE
    v_domain TEXT := LOWER(TRIM(NEW.primary_domain));
    existing_company UUID;
BEGIN
    -- 1. Demote this company's other primaries first. The one-primary index makes the order
    --    load-bearing: insert-then-demote violates it, demote-then-insert does not.
    UPDATE company_domains
       SET is_primary = FALSE
     WHERE company_id = NEW.id
       AND is_primary
       AND domain <> v_domain;

    -- 2. Register the domain if it is new. If another company already owns it, this is a no-op and
    --    the ownership check below raises with the merge advice.
    INSERT INTO company_domains (company_id, domain, is_primary)
    VALUES (NEW.id, v_domain, TRUE)
    ON CONFLICT (domain) DO NOTHING;

    SELECT cd.company_id INTO existing_company
      FROM company_domains cd
     WHERE cd.domain = v_domain;

    IF existing_company IS NOT NULL AND existing_company <> NEW.id THEN
        RAISE EXCEPTION
            'Duplicate domain: % is already registered to company %. Merge the records instead of re-parenting a domain.',
            v_domain, existing_company;
    END IF;

    -- 3. Adopt an existing secondary row as the primary.
    UPDATE company_domains
       SET is_primary = TRUE
     WHERE domain = v_domain
       AND company_id = NEW.id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
