"""Static checks on the security migration (Directive 02 §7.5).

These do not prove the fixes work — only a database can do that. They prove the
authored SQL actually contains the intended controls, is transactional, and is
not being confused with the deployed schema. The executable counterpart is
``tests/db/test_security_fixes_db.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEPLOYED = REPO / "supabase" / "migrations" / "20261107174129_core_crm_schema.sql"
# Applied: a 14-digit version is what the CLI enumerates as a migration. Presence here
# is a *claim of application*; the remote history (`supabase migration list --remote`)
# is the only authority, and a file must not sit here before it is applied.
PENDING = REPO / "supabase" / "migrations" / "20261114194622_hardening_batch1.sql"
DISCOVERY = REPO / "supabase" / "migrations" / "20261115212728_discovery_candidates.sql"
PENDING_DIR = REPO / "supabase" / "migrations" / "pending"


@pytest.fixture(scope="module")
def sql() -> str:
    return PENDING.read_text(encoding="utf-8")


def test_applied_migrations_are_staged_with_valid_versions():
    """A 14-digit prefix is what the CLI enumerates; `migration list --remote` decides.

    Both migrations have been applied and verified in Development, so both files live in
    `supabase/migrations/` with versions that sort after the schema they follow, and
    nothing is left in `pending/` where a future `db push` could pick it up.
    """
    for path, after in ((PENDING, "20261107174129"), (DISCOVERY, "20261114194622")):
        assert path.exists(), f"applied migration is missing: {path.name}"
        assert re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", path.name), f"not a valid migration name: {path.name}"
        assert path.name[:14] > after, "a migration must sort after the one it follows"
    assert not list(PENDING_DIR.glob("*.sql")), "an applied migration must not remain in pending/"


def test_migration_is_transactional(sql: str):
    body = sql.upper()
    assert body.strip().startswith("--") or body.lstrip().startswith("begin")
    assert re.search(r"\bBEGIN;", body)
    assert re.search(r"\bCOMMIT;", body)
    assert body.count("BEGIN;") == body.count("COMMIT;") == 1


def test_h01_suppression_triggers_are_security_definer_with_fixed_search_path(sql: str):
    for fn in ("fn_sync_contact_suppression", "fn_sync_company_suppression"):
        block = sql.split(f"FUNCTION {fn}()", 1)[1].split("$$ LANGUAGE plpgsql;", 1)[0]
        assert "SECURITY DEFINER" in block, f"{fn} must run as definer"
        assert "SET search_path = public, pg_temp" in block, f"{fn} must pin search_path"
    # The fix must not hand INSERT to authenticated — that would let operators forge rows.
    assert not re.search(r"GRANT\s+INSERT\s+ON\s+suppression_registry\s+TO\s+authenticated", sql, re.I)


def test_h02_agent_transitions_are_whitelisted(sql: str):
    block = sql.split("FUNCTION fn_enforce_agent_task_transitions()", 1)[1]
    assert "OLD.status = 'pending'" in block and "NEW.status = 'in_progress'" in block
    assert "OLD.status = 'in_progress' AND NEW.status = 'completed'" in block
    assert "RAISE EXCEPTION" in block
    assert "CREATE TRIGGER trg_enforce_agent_task_transitions" in sql


def test_h03_outreach_requires_an_approved_qualification(sql: str):
    policy = sql.split("CREATE POLICY agent_insert_outreach_messages", 1)[1]
    assert "lead_qualifications" in policy
    assert "review_status = 'approved'" in policy
    assert "qualification_tier <> 'disqualified'" in policy
    assert "lifecycle_stage IN ('qualified', 'outreach_queued', 'in_outreach')" in policy


def test_h04_single_approved_scorecard_per_company(sql: str):
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_qualifications_approved\s+ON lead_qualifications \(company_id\)\s+WHERE review_status = 'approved'",
        sql,
    )


def test_h05_domain_reparenting_raises_instead_of_moving(sql: str):
    block = sql.split("FUNCTION fn_sync_company_primary_domain()", 1)[1]
    assert "ON CONFLICT (domain) DO NOTHING" in block
    assert "RAISE EXCEPTION" in block
    assert "Duplicate domain" in block
    assert "SET company_id = EXCLUDED.company_id" not in block


def test_h01_preserves_suppression_source_semantics(sql: str):
    """A permission fix must not quietly change what the audit ledger records.

    The deployed company trigger writes 'manual_blacklist'; an early draft of this
    batch wrote 'opt_out_request', which would have re-labelled every operator
    suppression as an inbound opt-out. Contact suppression legitimately means
    opt-out and must keep that value.
    """
    company_block = sql.split("FUNCTION fn_sync_company_suppression()", 1)[1].split("$$ LANGUAGE", 1)[0]
    assert "'manual_blacklist'" in company_block
    assert "'opt_out_request'" not in company_block

    contact_block = sql.split("FUNCTION fn_sync_contact_suppression()", 1)[1].split("$$ LANGUAGE", 1)[0]
    assert "'opt_out_request'" in contact_block

    deployed = DEPLOYED.read_text(encoding="utf-8")
    deployed_company = deployed.split("FUNCTION fn_sync_company_suppression()", 1)[1].split("$$ LANGUAGE", 1)[0]
    assert "'manual_blacklist'" in deployed_company, "deployed semantic changed; re-verify the migration's intent"


def test_migration_does_not_drop_or_rewrite_business_data(sql: str):
    lowered = sql.lower()
    for forbidden in ("delete from", "truncate", "drop table", "drop column", "update companies", "update contacts"):
        assert forbidden not in lowered, f"hardening batch must not contain '{forbidden}'"


def test_deployed_schema_still_contains_the_original_defects(sql: str):
    """If these disappear, someone edited the historical schema — that needs an ADR, not a quiet edit."""
    deployed = DEPLOYED.read_text(encoding="utf-8")
    assert "SET company_id = EXCLUDED.company_id" in deployed, "H-05 anchor missing from deployed schema"
    assert "GRANT SELECT ON suppression_registry" in deployed, "H-01 anchor missing from deployed schema"
    assert "uq_lead_qualifications_approved" not in deployed
