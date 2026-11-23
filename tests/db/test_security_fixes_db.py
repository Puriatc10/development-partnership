"""Executable checks for the five security fixes — run against a real database.

Every test seeds the rows it needs *inside its own transaction* and asserts those rows are
gone after the rollback. That is deliberate on both counts:

* a database policy cannot be proven by reading SQL, and
* a fresh database (or a colleague's Development database) must not be a reason to skip —
  a skip hides exactly the regressions this module exists to catch.

The only remaining skip is "``ERYNDEX_DB_URL`` is not configured", which is an environment
absence rather than a missing precondition; ``pytest -rs`` reports it.

    ERYNDEX_DB_URL="postgresql://eryndex_owner:***@127.0.0.1:5433/eryndex" \\
        python -m pytest tests/db -q -rs

Each test is named after the finding it proves, so the run output doubles as the
verification evidence required by Directive 02 §7.6.
"""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import urlsplit

import pytest

psycopg = pytest.importorskip("psycopg")

DB_URL = os.environ.get("ERYNDEX_DB_URL", "")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "postgres", "db"}

# The JWT ``sub`` carried by the operator claim. Approval triggers require
# ``reviewed_by_human_id`` / ``approved_by_human_id`` to equal ``auth.uid()``, so a seeded
# approval has to be signed by this identity.
OPERATOR_ID = "00000000-0000-0000-0000-000000000001"

pytestmark = pytest.mark.skipif(
    not DB_URL,
    reason="ERYNDEX_DB_URL not set: database policy tests need a Development Postgres connection",
)


@pytest.fixture(scope="module")
def conn():
    host = (urlsplit(DB_URL).hostname or "").lower()
    if host not in LOCAL_HOSTS and os.environ.get("ERYNDEX_ENV") != "production":
        pytest.fail(f"Refusing to run: ERYNDEX_DB_URL points at a non-local host ({host!r})")
    with psycopg.connect(DB_URL, connect_timeout=15) as connection:
        connection.autocommit = False
        yield connection
        connection.rollback()


@pytest.fixture(scope="module")
def dev_confirmed(conn):
    with conn.cursor() as cur:
        cur.execute("select current_user, current_database()")
        user, _ = cur.fetchone()
    # The old check asserted the Supabase project role name; with a self-hosted database the
    # equivalent guarantee is that we are connected to the expected database as a superuser.
    assert (user or "").startswith("eryndex"), (
        f"connected as {user!r}; expected the platform owner role (eryndex_owner)"
    )
    return True


# --- role / claim plumbing --------------------------------------------------
#
# ``set local role`` is transaction-scoped, so ``rollback to savepoint`` and the test's final
# rollback both revert it. Every helper names the actor explicitly: the owner seeds fixtures,
# the operator signs human decisions, and an agent is the subject under test.


def _as_owner(cur) -> None:
    """Back to the login role with no JWT — fixture seeding, never an actor's action."""
    cur.execute("reset role")
    cur.execute("select set_config('request.jwt.claims', '', true)")


def _claims(cur, user_role: str, agent_id: str = "", subagent: str = "", sub: str = OPERATOR_ID) -> None:
    """Simulate a verified JWT for policy and trigger evaluation."""
    payload = {
        "sub": sub,
        "role": "authenticated",
        "app_metadata": {"user_role": user_role, "agent_id": agent_id, "subagent_type": subagent},
    }
    cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(payload),))


def _as_operator(cur) -> None:
    cur.execute("set local role authenticated")
    _claims(cur, "operator")


def _as_agent(cur, identity: str, subagent_type: str) -> None:
    cur.execute("set local role authenticated")
    _claims(cur, "agent", identity, subagent_type)


# --- fixture seeding (always as the owner, inside the test's transaction) ----


def _seed_company(cur, *, lifecycle_stage: str = "identified", name: str | None = None) -> tuple[str, str]:
    company_id = str(uuid.uuid4())
    domain = f"probe-{company_id.replace('-', '')[:12]}.test"
    label = name or f"Probe {company_id[:8]}"
    cur.execute(
        "insert into companies (id, name, normalized_name, primary_domain, website_url, primary_segment,"
        " discovery_source, lifecycle_stage) values (%s, %s, %s, %s, %s, 'webflow_studio', 'test', %s)",
        (company_id, label, label.lower().replace(" ", "-"), domain, f"https://{domain}", lifecycle_stage),
    )
    return company_id, domain


def _seed_contact(cur, company_id: str) -> str:
    contact_id = str(uuid.uuid4())
    cur.execute(
        "insert into contacts (id, company_id, first_name, last_name, role_type, is_decision_maker)"
        " values (%s, %s, 'Probe', 'Contact', 'founder_executive', true)",
        (contact_id, company_id),
    )
    return contact_id


def _seed_task(
    cur,
    *,
    identity: str,
    task_type: str = "research",
    status: str = "in_progress",
    company_id: str | None = None,
    contact_id: str | None = None,
) -> str:
    task_id = str(uuid.uuid4())
    cur.execute(
        "insert into tasks (id, title, task_type, status, assigned_actor_type, assigned_actor_identity,"
        " company_id, contact_id) values (%s, %s, %s, %s, 'agent', %s, %s, %s)",
        (task_id, f"Probe task {task_id[:8]}", task_type, status, identity, company_id, contact_id),
    )
    return task_id


# Seven dimensions, all positive, all evidenced: 15+15+10+10+5+5+5 = 65 -> tier_2_prospective.
_QUALIFICATION_EVIDENCE = {
    key: {"evidence": "seeded probe evidence"}
    for key in (
        "customer_flow",
        "development_demand",
        "repeatability_orders",
        "technical_fit",
        "business_size",
        "decision_maker_accessibility",
        "commercial_fit",
    )
}


def _insert_scorecard(cur, company_id: str, *, approved: bool) -> str:
    """Insert a valid scorecard. Must run with an operator's claims (the approval gate).

    Approved rows are signed by ``OPERATOR_ID`` because both the trigger and
    ``chk_lead_qual_review_human_gate`` demand a human reviewer equal to ``auth.uid()``.
    """
    scorecard_id = str(uuid.uuid4())
    cur.execute(
        "insert into lead_qualifications (id, company_id, evaluator_type, evaluator_identity,"
        " overall_score, customer_flow_score, development_demand_score, repeatability_orders_score,"
        " technical_fit_score, business_size_score, decision_maker_accessibility_score,"
        " commercial_fit_score, dimension_evidence, qualification_tier, recommended_next_action,"
        " review_status, reviewed_by_human_id, reviewed_at)"
        " values (%s, %s, 'agent', 'hermes-qualifier-v1', 65, 15, 15, 10, 10, 5, 5, 5, %s,"
        " 'tier_2_prospective', 'Draft the first outreach sequence.', %s, %s, %s)",
        (
            scorecard_id,
            company_id,
            json.dumps(_QUALIFICATION_EVIDENCE),
            "approved" if approved else "draft",
            OPERATOR_ID if approved else None,
            None,
        ),
    )
    return scorecard_id


# --- refusal assertions ----------------------------------------------------

_SAVEPOINT = "probe_expect_refused"


def _expect_refused(conn, sql: str, params, *, sqlstate: str, message: list[str], label: str):
    """Run one statement that must be refused, and pin *why* it was refused.

    Returns the error so callers can inspect driver-level diagnostics (constraint name).
    The statement runs inside a savepoint, so the transaction stays usable and neither the
    statement's writes nor the aborted subtransaction survive.
    """
    with conn.cursor() as cur:
        cur.execute(f"savepoint {_SAVEPOINT}")
        try:
            cur.execute(sql, params)
        except psycopg.Error as exc:
            cur.execute(f"rollback to savepoint {_SAVEPOINT}")
            cur.execute(f"release savepoint {_SAVEPOINT}")
            assert exc.sqlstate == sqlstate, (
                f"{label}: expected SQLSTATE {sqlstate}, got {exc.sqlstate}: {exc}"
            )
            for fragment in message:
                assert fragment in str(exc), f"{label}: expected {fragment!r} in {str(exc)!r}"
            return exc
        cur.execute(f"release savepoint {_SAVEPOINT}")
    raise AssertionError(
        f"{label}: the statement was ACCEPTED — the protection this test proves has regressed"
    )


def _assert_rolled_back(conn, *, label: str, checks: dict[str, tuple[str, tuple]]) -> None:
    """Roll back and prove none of the seeded rows survived.

    Without this a test could pass while leaving probe companies behind in a shared
    Development database.
    """
    conn.rollback()
    with conn.cursor() as cur:
        _as_owner(cur)
        for description, (sql, params) in checks.items():
            cur.execute(sql, params)
            remaining = cur.fetchone()[0]
            assert remaining == 0, f"{label}: {description} survived the rollback ({remaining} row(s))"


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
        _as_owner(cur)
        _as_operator(cur)
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

    _assert_rolled_back(
        conn,
        label="H-01",
        checks={
            "the probe company": ("select count(*) from companies where id = %s", (company_id,)),
            "the suppression ledger row": (
                "select count(*) from suppression_registry where identifier = %s",
                (domain,),
            ),
        },
    )


# --- H-02 -----------------------------------------------------------------


def test_h02_agent_cannot_rewind_or_cancel_a_task(conn, dev_confirmed):
    """An agent may only move ``pending -> in_progress -> completed``.

    Previously skipped on every fresh database ("no in_progress researcher task"), i.e. the
    guard was never exercised where it matters, and the assertion was a bare
    ``pytest.raises(RaiseException)`` — any trigger raising anything satisfied it. The task is
    now seeded in-transaction and the refusal is pinned to
    ``fn_enforce_agent_task_transitions`` by SQLSTATE and message.

    Controls: the same rewind is accepted for a human operator (so the refusal is about *who*
    is asking, not about the row), and the whitelisted ``in_progress -> completed`` edge is
    accepted for the agent (so the guard is selective, not a blanket block).
    """
    task_id: str
    with conn.cursor() as cur:
        _as_owner(cur)
        task_id = _seed_task(
            cur, identity="hermes-researcher-v1", task_type="research", status="in_progress"
        )

        _as_agent(cur, "hermes-researcher-v1", "researcher")
        cur.execute("select status, assigned_actor_identity from tasks where id = %s", (task_id,))
        assert cur.fetchone() == ("in_progress", "hermes-researcher-v1"), (
            "the seeded task is not visible to its assigned agent, so an RLS no-op could be "
            "mistaken for the transition guard"
        )

        # Control 1: the exact statement below, spoken by a human operator, is accepted.
        _as_operator(cur)
        cur.execute("savepoint h02_operator_control")
        cur.execute("update tasks set status = 'pending' where id = %s", (task_id,))
        assert cur.rowcount == 1, "operator could not rewind; the probe is not testing H-02"
        cur.execute("rollback to savepoint h02_operator_control")
        cur.execute("release savepoint h02_operator_control")

        _as_agent(cur, "hermes-researcher-v1", "researcher")
        _expect_refused(
            conn,
            "update tasks set status = 'pending' where id = %s",
            (task_id,),
            sqlstate="P0001",
            message=[
                "Lifecycle Violation: agents may only move pending -> in_progress -> completed",
                f"Attempted in_progress -> pending on task {task_id}",
            ],
            label="H-02 in_progress -> pending",
        )
        _expect_refused(
            conn,
            "update tasks set status = 'cancelled' where id = %s",
            (task_id,),
            sqlstate="P0001",
            message=[
                "Lifecycle Violation: agents may only move pending -> in_progress -> completed",
                f"Attempted in_progress -> cancelled on task {task_id}",
            ],
            label="H-02 in_progress -> cancelled",
        )

        # Control 2: the one allowed edge still works for the same agent — a whitelist, not a
        # blanket block on agent writes.
        cur.execute("savepoint h02_allowed_edge")
        cur.execute("update tasks set status = 'completed' where id = %s", (task_id,))
        assert cur.rowcount == 1, "the agent could not complete its own task; the guard over-blocks"
        cur.execute("rollback to savepoint h02_allowed_edge")
        cur.execute("release savepoint h02_allowed_edge")

        cur.execute("select status from tasks where id = %s", (task_id,))
        assert cur.fetchone()[0] == "in_progress", "a refused rewind still moved the task"

    _assert_rolled_back(
        conn,
        label="H-02",
        checks={"the seeded task": ("select count(*) from tasks where id = %s", (task_id,))},
    )


# --- H-03 -----------------------------------------------------------------

_OUTREACH_DRAFT_SQL = (
    "insert into outreach_messages (company_id, contact_id, channel, approval_status, sending_status,"
    " drafted_by_agent_identity, subject, body, personalization_rationale, proposed_commercial_model)"
    " values (%s, %s, 'email', 'draft', 'draft', %s, 'Probe subject', 'Probe body',"
    " 'Probe rationale', 'project_based_wholesale')"
)


def test_h03_drafting_requires_an_approved_qualification(conn, dev_confirmed):
    """A draft must be refused *because* there is no approved qualification.

    The insert policy has several predicates (an active task for the drafter, the drafter's
    identity and draft-only fields, a contact on the company, and — after H-03 — an approved
    non-disqualified qualification plus a late-enough lifecycle stage). A bare
    ``pytest.raises(InsufficientPrivilege)`` distinguishes none of them, so the old version
    skipped; even then its INSERT omitted the NOT NULL ``personalization_rationale``, so it could
    only ever have observed a refusal, never a valid draft.

    Proof structure:

    1. every other predicate is satisfiable and satisfied at the moment of the refusal — the
       drafter can read its own ``in_progress`` task and the company is at ``qualified``;
    2. the table genuinely holds no approved qualification for that company;
    3. the identical statement is refused by RLS on ``outreach_messages`` (42501);
    4. the identical statement succeeds once an approved, non-disqualified qualification exists —
       so the qualification predicate is the only thing that was missing.
    """
    with conn.cursor() as cur:
        _as_owner(cur)
        company_id, _domain = _seed_company(cur, lifecycle_stage="qualified")
        contact_id = _seed_contact(cur, company_id)
        task_id = _seed_task(
            cur,
            identity="hermes-drafter-v1",
            task_type="outreach_drafting",
            status="in_progress",
            company_id=company_id,
            contact_id=contact_id,
        )

        # (1) the task predicate is satisfied, from the drafter's own point of view
        _as_agent(cur, "hermes-drafter-v1", "drafter")
        cur.execute("select status from tasks where id = %s", (task_id,))
        assert cur.fetchone() == ("in_progress",), (
            "the drafter cannot see its own task, so a missing task — not H-03 — would explain "
            "the refusal below"
        )

        # (2) no approved qualification exists for this company
        _as_owner(cur)
        cur.execute(
            "select count(*) from lead_qualifications where company_id = %s and review_status = 'approved'",
            (company_id,),
        )
        assert cur.fetchone()[0] == 0, "the probe company already has an approved scorecard"

        # (3) refused, and refused by RLS on this table
        _as_agent(cur, "hermes-drafter-v1", "drafter")
        _expect_refused(
            conn,
            _OUTREACH_DRAFT_SQL,
            (company_id, contact_id, "hermes-drafter-v1"),
            sqlstate="42501",
            message=['new row violates row-level security policy for table "outreach_messages"'],
            label="H-03 draft without an approved qualification",
        )

        # (4) the same statement, now that a human approved a non-disqualified scorecard
        _as_operator(cur)
        _insert_scorecard(cur, company_id, approved=True)
        _as_agent(cur, "hermes-drafter-v1", "drafter")
        cur.execute(_OUTREACH_DRAFT_SQL, (company_id, contact_id, "hermes-drafter-v1"))
        assert cur.rowcount == 1, (
            "the identical insert was still refused with a qualification present — another "
            "predicate is unmet, so this test cannot attribute the refusal to H-03"
        )
        cur.execute(
            "select drafted_by_agent_identity, approval_status, sending_status"
            " from outreach_messages where company_id = %s",
            (company_id,),
        )
        assert cur.fetchall() == [("hermes-drafter-v1", "draft", "draft")]

    _assert_rolled_back(
        conn,
        label="H-03",
        checks={
            "the draft": ("select count(*) from outreach_messages where company_id = %s", (company_id,)),
            "the approved scorecard": (
                "select count(*) from lead_qualifications where company_id = %s",
                (company_id,),
            ),
            "the drafter task": ("select count(*) from tasks where id = %s", (task_id,)),
            "the contact": ("select count(*) from contacts where id = %s", (contact_id,)),
            "the company": ("select count(*) from companies where id = %s", (company_id,)),
        },
    )


# --- H-04 -----------------------------------------------------------------


def test_h04_second_approved_scorecard_is_rejected(conn, dev_confirmed):
    """A second approved scorecard for one company must be impossible.

    Previously skipped whenever the database held no approved row ("nothing to duplicate") —
    the state of every fresh database — and then asserted only ``UniqueViolation``, not which
    index fired. The index is now asserted by name, and a second *draft* scorecard is shown to
    still be legal: ``uq_lead_qualifications_approved`` is a partial index on approval, not a cap
    of one scorecard per company.
    """
    with conn.cursor() as cur:
        _as_owner(cur)
        company_id, _domain = _seed_company(cur, lifecycle_stage="qualified")

        _as_operator(cur)
        _insert_scorecard(cur, company_id, approved=True)
        cur.execute(
            "select count(*) from lead_qualifications where company_id = %s and review_status = 'approved'",
            (company_id,),
        )
        assert cur.fetchone()[0] == 1, "the seeded approval is missing; nothing to duplicate"

        exc = _expect_refused(
            conn,
            "insert into lead_qualifications (id, company_id, evaluator_type, evaluator_identity,"
            " overall_score, customer_flow_score, development_demand_score, repeatability_orders_score,"
            " technical_fit_score, business_size_score, decision_maker_accessibility_score,"
            " commercial_fit_score, dimension_evidence, qualification_tier, recommended_next_action,"
            " review_status, reviewed_by_human_id, reviewed_at)"
            " values (%s, %s, 'agent', 'hermes-qualifier-v1', 65, 15, 15, 10, 10, 5, 5, 5, %s,"
            " 'tier_2_prospective', 'Draft the first outreach sequence.', 'approved', %s,"
            " clock_timestamp())",
            (
                str(uuid.uuid4()),
                company_id,
                json.dumps(_QUALIFICATION_EVIDENCE),
                OPERATOR_ID,
            ),
            sqlstate="23505",
            message=["duplicate key value violates unique constraint", "uq_lead_qualifications_approved"],
            label="H-04 second approved scorecard",
        )
        assert exc.diag.constraint_name == "uq_lead_qualifications_approved", (
            f"refused on {exc.diag.constraint_name!r}, not on the H-04 index"
        )
        assert exc.diag.table_name == "lead_qualifications"

        # A second *draft* is still allowed: the index is partial by design.
        _insert_scorecard(cur, company_id, approved=False)
        cur.execute(
            "select count(*) filter (where review_status = 'approved'), count(*)"
            " from lead_qualifications where company_id = %s",
            (company_id,),
        )
        assert cur.fetchone() == (1, 2), "the partial index refused a draft scorecard"

    _assert_rolled_back(
        conn,
        label="H-04",
        checks={
            "the scorecards": (
                "select count(*) from lead_qualifications where company_id = %s",
                (company_id,),
            ),
            "the company": ("select count(*) from companies where id = %s", (company_id,)),
        },
    )


# --- H-05 -----------------------------------------------------------------


def test_h05_domain_cannot_be_reparented(conn, dev_confirmed):
    """A taken domain must raise, not move to another company.

    Previously skipped unless the database already had a non-primary ``company_domains`` row.
    Both companies and the secondary domain are seeded in-transaction, and the probe uses a
    *secondary* domain on purpose: pointing a company at another company's ``primary_domain``
    would hit ``companies.primary_domain`` UNIQUE (23505) before this trigger ran, and would look
    like H-05 while proving nothing.

    The control uses a company with no ``company_domains`` row at all: since
    ``uq_company_domains_one_primary`` (one primary row per company), the sync trigger's
    ``INSERT ... is_primary = TRUE`` collides with the company's *existing* primary row, so
    renaming the primary domain of an already-synced company fails with 23505 inside the trigger
    before any judgement about duplicates. That defect is not H-05 and must not be mistaken for
    it — it is reported separately, not asserted here.
    """
    with conn.cursor() as cur:
        _as_owner(cur)
        owner_id, owner_primary = _seed_company(cur, name="Domain Owner")
        other_id, other_primary = _seed_company(cur, name="Domain Target")
        secondary = f"alias-{owner_id.replace('-', '')[:12]}.test"
        cur.execute(
            "insert into company_domains (company_id, domain, is_primary) values (%s, %s, false)",
            (owner_id, secondary),
        )
        # Give the control company no domain row so the trigger's INSERT cannot collide with
        # uq_company_domains_one_primary.
        cur.execute("delete from company_domains where company_id = %s", (other_id,))
        assert secondary not in (owner_primary, other_primary)

        _as_operator(cur)
        # Control: the same UPDATE with a free domain is accepted.
        cur.execute("savepoint h05_control")
        cur.execute(
            "update companies set primary_domain = %s where id = %s",
            (f"free-{other_id.replace('-', '')[:12]}.test", other_id),
        )
        assert cur.rowcount == 1, "the UPDATE path is blocked before the domain guard can be judged"
        cur.execute("rollback to savepoint h05_control")
        cur.execute("release savepoint h05_control")

        _expect_refused(
            conn,
            "update companies set primary_domain = %s where id = %s",
            (secondary, other_id),
            sqlstate="P0001",
            message=[
                f"Duplicate domain: {secondary} is already registered to company {owner_id}",
                "Merge the records instead of re-parenting a domain",
            ],
            label="H-05 re-parenting a taken domain",
        )

        cur.execute("select company_id, is_primary from company_domains where domain = %s", (secondary,))
        owner_after, is_primary_after = cur.fetchone()
        assert (str(owner_after), is_primary_after) == (owner_id, False), (
            "the domain was re-parented despite the guard"
        )
        cur.execute("select primary_domain from companies where id = %s", (other_id,))
        assert cur.fetchone()[0] == other_primary, "the refused update still changed the company"

    _assert_rolled_back(
        conn,
        label="H-05",
        checks={
            "the probe companies": (
                "select count(*) from companies where id in (%s, %s)",
                (owner_id, other_id),
            ),
            "the domain rows": (
                "select count(*) from company_domains where domain in (%s, %s, %s)",
                (secondary, owner_primary, other_primary),
            ),
        },
    )


# --- Refokus regression ---------------------------------------------------


def test_historical_research_is_not_in_this_database(conn, dev_confirmed):
    """Refokus history was deliberately left in the retired Supabase project.

    The previous version of this test asserted 13 findings and a completed task in the live
    database — which was a check on the *Supabase* project it had been written against. Data was
    not migrated (owner's instruction), so the property that still matters is that the historical
    record survives as an artefact and that the fresh database does not carry a half-imported copy
    of it.

    The count is read as the owner: as ``authenticated`` with no operator claim, RLS would hide
    every row and the ``== 0`` assertion would pass for the wrong reason.

    Surviving artefact: agents/researcher/runs/refokus-research.json (13 findings).
    """
    from pathlib import Path

    artifact = Path(__file__).resolve().parents[2] / "agents" / "researcher" / "runs" / "refokus-research.json"
    assert artifact.exists(), "the historical research artifact must be kept in the repository"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    findings = payload.get("findings") or payload.get("research_payload", {}).get("findings") or []
    assert len(findings) == 13, f"the artefact should still hold 13 findings, found {len(findings)}"

    with conn.cursor() as cur:
        _as_owner(cur)
        # Scoped to the historical company, not "the table is empty": the database legitimately
        # holds research for the synthetic fixture company, and a global zero-count assertion
        # would fail the moment the research path is exercised — the opposite of the point.
        cur.execute(
            "select count(*) from research_findings f "
            "join companies c on c.id = f.company_id where c.primary_domain = 'refokus.com'"
        )
        assert cur.fetchone()[0] == 0, "no historical Refokus research may be half-imported"
