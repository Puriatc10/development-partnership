"""Executable checks for the five security fixes — run against a real database.

Skipped unless ``ERYNDEX_DB_URL`` is set. That is deliberate: a database policy
cannot be proven by reading SQL. Point it at the **Development** project only,
never Production.

    ERYNDEX_DB_URL="postgresql://postgres.<ref>:...@...:5432/postgres" \\
        python -m pytest tests/db -q

Each test is named after the finding it proves, so the run output doubles as the
verification evidence required by Directive 02 §7.6.
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DB_URL = os.environ.get("ERYNDEX_DB_URL", "")
DEV_REF = "vyfqmrdznmocnmzyjjcj"
PROD_REF = "vqnamszywsuqjxuqseas"

pytestmark = pytest.mark.skipif(
    not DB_URL,
    reason="ERYNDEX_DB_URL not set: database policy tests need a Development Postgres connection",
)


@pytest.fixture(scope="module")
def conn():
    if PROD_REF in DB_URL:
        pytest.fail("Refusing to run: ERYNDEX_DB_URL points at Production")
    with psycopg.connect(DB_URL, connect_timeout=15) as connection:
        connection.autocommit = False
        yield connection
        connection.rollback()


@pytest.fixture(scope="module")
def dev_confirmed(conn):
    with conn.cursor() as cur:
        cur.execute("select current_user, current_database()")
        user, _ = cur.fetchone()
    assert DEV_REF in (user or ""), f"connected as {user!r}, expected the Development project role"
    return True


def _as_role(cur, role: str) -> None:
    """Simulate a JWT role for policy evaluation."""
    cur.execute(f"set local role {role}")


def _claims(cur, user_role: str, agent_id: str = "", subagent: str = "") -> None:
    cur.execute(
        "select set_config('request.jwt.claims', %s, true)",
        (
            '{"sub":"00000000-0000-0000-0000-000000000001","role":"authenticated",'
            f'"app_metadata":{{"user_role":"{user_role}","agent_id":"{agent_id}","subagent_type":"{subagent}"}}}}',
        ),
    )


# --- H-01 -----------------------------------------------------------------


def test_h01_operator_can_register_suppression(conn, dev_confirmed):
    """Was: 42501 permission denied for table suppression_registry.

    The UPDATE must also satisfy ``chk_companies_suppression_logic`` — setting
    ``is_suppressed = true`` alone raises 23514 on the CHECK constraint *before* the
    AFTER trigger runs, which would look like an H-01 failure while proving nothing.
    """
    company_id = str(uuid.uuid4())
    domain = f"probe-{company_id[:8]}.test"
    with conn.cursor() as cur:
        _as_role(cur, "authenticated")
        _claims(cur, "operator")
        cur.execute(
            "insert into companies (id, name, normalized_name, primary_domain, website_url, primary_segment, discovery_source, lifecycle_stage)"
            " values (%s, 'Suppression Probe', 'suppressionprobe', %s, %s, 'webflow_studio', 'test', 'identified')",
            (company_id, domain, f"https://{domain}"),
        )
        cur.execute(
            "update companies set is_suppressed = true, suppression_reason = %s,"
            " suppression_date = clock_timestamp() where id = %s",
            ("H-01 verification probe", company_id),
        )
        cur.execute("select count(*) from suppression_registry where identifier = %s", (domain,))
        assert cur.fetchone()[0] == 1, "opt-out accepted but no ledger row was written"
        # The ledger row must carry the deployed semantic, not a re-labelled one.
        cur.execute("select source from suppression_registry where identifier = %s", (domain,))
        assert cur.fetchone()[0] == "manual_blacklist"
    conn.rollback()


# --- H-02 -----------------------------------------------------------------


def test_h02_agent_cannot_rewind_or_cancel_a_task(conn, dev_confirmed):
    with conn.cursor() as cur:
        _as_role(cur, "authenticated")
        _claims(cur, "agent", "hermes-researcher-v1", "researcher")
        cur.execute(
            "select id from tasks where assigned_actor_identity = 'hermes-researcher-v1' and status = 'in_progress' limit 1"
        )
        row = cur.fetchone()
        if row is None:
            pytest.skip("no in_progress researcher task available to exercise the guard")
        task_id = row[0]
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("update tasks set status = 'pending' where id = %s", (task_id,))
    conn.rollback()


# --- H-03 -----------------------------------------------------------------


def test_h03_drafting_requires_an_approved_qualification(conn, dev_confirmed):
    """A draft must be refused *because* there is no approved qualification.

    The insert policy has several predicates (a contact on the company, an active
    task for the drafter, a campaign belonging to that task, and — after H-03 — an
    approved non-disqualified qualification). If any *other* predicate is unmet the
    insert is still rejected, and `pytest.raises(InsufficientPrivilege)` would pass
    for the wrong reason. This test therefore skips unless the confounders are known
    to be satisfiable, and asserts two things once they are: the rejection is an RLS
    violation of this table, and no row was written.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from tasks where assigned_actor_identity = 'hermes-drafter-v1'"
            " and status = 'in_progress' limit 1"
        )
        if cur.fetchone() is None:
            pytest.skip(
                "no in_progress task for hermes-drafter-v1: every rejection here would be "
                "the task predicate, not H-03's qualification predicate"
            )
        cur.execute(
            "select c.id, ct.id from companies c join contacts ct on ct.company_id = c.id"
            " where not exists (select 1 from lead_qualifications lq"
            " where lq.company_id = c.id and lq.review_status = 'approved') limit 1"
        )
        row = cur.fetchone()
        if not row:
            pytest.skip("no company with a contact and no approved qualification to test against")
        assert row is not None  # for the type checker; pytest.skip above never returns
        company_id, contact_id = row

        _as_role(cur, "authenticated")
        _claims(cur, "agent", "hermes-drafter-v1", "drafter")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as excinfo:
            cur.execute(
                "insert into outreach_messages (company_id, contact_id, channel, approval_status, sending_status,"
                " drafted_by_agent_identity, subject, body, proposed_commercial_model)"
                " values (%s, %s, 'email', 'draft', 'draft', 'hermes-drafter-v1', 'x', 'y', 'project_based_wholesale')",
                (company_id, contact_id),
            )
        # Distinguish a policy refusal from a constraint/type error that merely shares
        # the InsufficientPrivilege ancestor.
        assert "row-level security" in str(excinfo.value).lower(), (
            f"rejected for the wrong reason: {excinfo.value}"
        )
    conn.rollback()


# --- H-04 -----------------------------------------------------------------


def test_h04_second_approved_scorecard_is_rejected(conn, dev_confirmed):
    """A second approved scorecard for one company must be impossible.

    The insert has to pass the approval gate first (`fn_enforce_lead_qualification_rules`
    requires a human operator JWT), otherwise it fails on identity and the index is
    never consulted — a rejection for the wrong reason.
    """
    with conn.cursor() as cur:
        cur.execute("select company_id from lead_qualifications where review_status = 'approved' limit 1")
        row = cur.fetchone()
        if not row:
            pytest.skip("no approved scorecard exists to duplicate")
        _as_role(cur, "authenticated")
        _claims(cur, "operator")
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "insert into lead_qualifications (company_id, evaluator_type, evaluator_identity, overall_score,"
                " qualification_tier, dimension_evidence, recommended_next_action, review_status,"
                " reviewed_by_human_id, reviewed_at)"
                " select company_id, evaluator_type, evaluator_identity, overall_score, qualification_tier,"
                " dimension_evidence, recommended_next_action, 'approved', reviewed_by_human_id, reviewed_at"
                " from lead_qualifications where company_id = %s and review_status = 'approved' limit 1",
                (row[0],),
            )
    conn.rollback()


# --- H-05 -----------------------------------------------------------------


def test_h05_domain_cannot_be_reparented(conn, dev_confirmed):
    """A taken domain must raise, not move to another company.

    The probe must use a *non-primary* domain row: reusing the owner's
    `companies.primary_domain` collides on its UNIQUE constraint (23505) before the
    trigger runs, which would look like a failure of H-05 while proving nothing.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select d.company_id, d.domain from company_domains d"
            " join companies c on c.id = d.company_id"
            " where d.domain <> c.primary_domain limit 1"
        )
        row = cur.fetchone()
        if not row:
            pytest.skip(
                "no non-primary company_domains row: a primary domain would collide on "
                "companies.primary_domain UNIQUE and never reach the trigger"
            )
        owner_id, domain = row
        other_id = str(uuid.uuid4())
        fresh_domain = f"reparent-probe-{other_id[:8]}.test"
        _as_role(cur, "authenticated")
        _claims(cur, "operator")
        cur.execute(
            "insert into companies (id, name, normalized_name, primary_domain, website_url, primary_segment, discovery_source, lifecycle_stage)"
            " values (%s, 'Domain Probe', 'domainprobe', %s, %s, 'webflow_studio', 'test', 'identified')",
            (other_id, fresh_domain, f"https://{fresh_domain}"),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("update companies set primary_domain = %s where id = %s", (domain, other_id))
        # And the guard must not have moved it as a side effect.
        cur.execute("select company_id from company_domains where domain = %s", (domain,))
        owner_after = cur.fetchone()
        assert owner_after and owner_after[0] == owner_id, "the domain was re-parented despite the guard"
    conn.rollback()


# --- Refokus regression ---------------------------------------------------


def test_refokus_history_is_untouched(conn, dev_confirmed):
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from research_findings where company_id = '1afd5547-ce67-45d3-8aaa-35619514f835'"
        )
        assert cur.fetchone()[0] == 13
        cur.execute(
            "select status, completed_at from tasks where id = 'cf41afa8-5b26-4353-b645-bd5524751e07'"
        )
        status, completed_at = cur.fetchone()
        assert status == "completed" and completed_at is not None
