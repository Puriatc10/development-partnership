"""Regression tests for the guards the adversarial audit found inert.

The defect these cover was subtle: eleven policies were written ``IF OLD IS NOT NULL AND …``, and
in PostgreSQL ``OLD IS NOT NULL`` is false for any row containing a NULL — which is every row in
these tables. The blocks never ran, so an approved message could jump to ``sent`` and an approved
scorecard could be edited in place. These tests fail against that build and pass against the
migrations 20261123140000 / …150000 / …160000.

They run against the real database and leave nothing behind: every case is rolled back.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from contracts.environment import load_env_file  # noqa: E402

psycopg = pytest.importorskip("psycopg")

DB_URL = os.environ.get("ERYNDEX_DB_URL") or load_env_file() or ""
pytestmark = pytest.mark.skipif(not DB_URL, reason="ERYNDEX_DB_URL is not configured")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
OPERATOR_ID = "c549b7f9-70a8-4919-b987-aff7d692ea1b"
EVIDENCE = {
    key: {"score": 10, "max": 20, "evidence": "probe", "finding_ids": [], "excerpt": "probe"}
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


@pytest.fixture(scope="module")
def conn():
    host = (urlsplit(DB_URL).hostname or "").lower()
    if host not in LOCAL_HOSTS and os.environ.get("ERYNDEX_ENV") != "production":
        pytest.fail(f"Refusing to run: ERYNDEX_DB_URL points at a non-local host ({host!r})")
    with psycopg.connect(DB_URL, connect_timeout=15) as connection:
        connection.autocommit = False
        yield connection
        connection.rollback()


@pytest.fixture()
def cur(conn):
    with conn.cursor() as cursor:
        yield cursor
        conn.rollback()


def _as_owner(cursor) -> None:
    cursor.execute("reset role")
    cursor.execute("select set_config('request.jwt.claims', '', true)")


def _as_operator(cursor, sub: str = OPERATOR_ID) -> None:
    claims = {
        "sub": sub,
        "role": "authenticated",
        "app_metadata": {"user_role": "operator", "user_type": "human"},
        "user_metadata": {"display_name": "operator"},
    }
    cursor.execute("set local role authenticated")
    cursor.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


def _as_agent(cursor, agent_id: str = "hermes-drafter-v1", subagent: str = "drafter") -> None:
    claims = {
        "sub": "99999999-9999-4999-8999-999999999999",
        "role": "authenticated",
        "app_metadata": {"user_role": "agent", "subagent_type": subagent, "agent_id": agent_id},
    }
    cursor.execute("set local role authenticated")
    cursor.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


def _as_dispatcher(cursor) -> None:
    claims = {
        "sub": OPERATOR_ID,
        "role": "authenticated",
        "app_metadata": {"user_role": "dispatcher"},
    }
    cursor.execute("set local role authenticated")
    cursor.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


def _seed_company(cursor, *, lifecycle_stage: str = "qualified") -> tuple[str, str]:
    company_id = str(uuid.uuid4())
    domain = f"guard-{company_id.replace('-', '')[:12]}.test"
    label = f"Guard {company_id[:8]}"
    cursor.execute(
        "insert into companies (id, name, normalized_name, primary_domain, website_url,"
        " primary_segment, discovery_source, lifecycle_stage)"
        " values (%s, %s, %s, %s, %s, 'webflow_studio', 'test', %s)",
        (company_id, label, label.lower(), domain, f"https://{domain}", lifecycle_stage),
    )
    return company_id, domain


def _seed_contact(cursor, company_id: str) -> str:
    contact_id = str(uuid.uuid4())
    cursor.execute(
        "insert into contacts (id, company_id, first_name, last_name, role_type, is_decision_maker)"
        " values (%s, %s, 'Guard', 'Contact', 'founder_executive', true)",
        (contact_id, company_id),
    )
    return contact_id


def _approved_scorecard(cursor, company_id: str) -> str:
    """A draft inserted by the owner, then approved by the operator: the only legal path."""
    scorecard_id = str(uuid.uuid4())
    _as_owner(cursor)
    cursor.execute(
        "insert into lead_qualifications (id, company_id, evaluator_type, evaluator_identity,"
        " overall_score, customer_flow_score, development_demand_score, repeatability_orders_score,"
        " technical_fit_score, business_size_score, decision_maker_accessibility_score,"
        " commercial_fit_score, dimension_evidence, qualification_tier, recommended_next_action,"
        " review_status, reviewed_by_human_id, reviewed_at)"
        " values (%s, %s, 'agent', 'hermes-qualifier-v1', 65, 10, 10, 10, 10, 10, 10, 5, %s::jsonb,"
        " 'tier_2_prospective', 'Draft the first outreach sequence.', 'draft', NULL, NULL)",
        (scorecard_id, company_id, json.dumps(EVIDENCE)),
    )
    _as_operator(cursor)
    cursor.execute(
        "update lead_qualifications set review_status = 'approved',"
        " reviewed_by_human_id = %s, reviewed_at = clock_timestamp() where id = %s",
        (OPERATOR_ID, scorecard_id),
    )
    assert cursor.rowcount == 1, "the fixture approval did not reach the row"
    cursor.execute("select review_status from lead_qualifications where id = %s", (scorecard_id,))
    assert cursor.fetchone()[0] == "approved", "the fixture approval did not stick"
    return scorecard_id


def _approved_draft(cursor, company_id: str) -> str:
    """A message in the state the product leaves it: approved, still a draft.

    The row is seeded as the owner and approved as the operator. The insert-time precondition
    trigger is exercised by its own case below; here the interesting invariants are the UPDATE-time
    ones, and seeding through it would make every other case depend on that trigger's privileges.
    """
    contact_id = _seed_contact(cursor, company_id)
    message_id = str(uuid.uuid4())
    _as_owner(cursor)
    cursor.execute(
        "insert into outreach_messages (id, company_id, contact_id, channel, subject, body,"
        " personalization_rationale, approval_status, sending_status, personalization_evidence_ids,"
        " drafted_by_agent_identity)"
        " values (%s, %s, %s, 'email', 'Hello', 'A short note about the backend layer.',"
        " 'guard fixture', 'draft', 'draft', '{}', %s)",
        (message_id, company_id, contact_id, OPERATOR_ID),
    )
    _as_operator(cursor)
    cursor.execute(
        "update outreach_messages set approval_status = 'approved', approved_by_human_id = %s"
        " where id = %s",
        (OPERATOR_ID, message_id),
    )
    return message_id


def _refused(cursor, statement: str, params: tuple = ()) -> psycopg.Error:
    cursor.execute("savepoint guard_case")
    with pytest.raises(psycopg.Error) as excinfo:
        cursor.execute(statement, params)
    cursor.execute("rollback to savepoint guard_case")
    return excinfo.value


# ---------------------------------------------------------------------------
# The dead guards, now alive
# ---------------------------------------------------------------------------


def test_a_draft_cannot_jump_straight_to_sent(cur):
    company_id, _ = _seed_company(cur)
    message_id = _approved_draft(cur, company_id)
    _as_operator(cur)
    error = _refused(
        cur,
        "update outreach_messages set sending_status = 'sent', sent_at = now() where id = %s",
        (message_id,),
    )
    assert "transition" in str(error).lower()


def test_the_body_of_an_approved_message_is_immutable(cur):
    company_id, _ = _seed_company(cur)
    message_id = _approved_draft(cur, company_id)
    _as_operator(cur)
    error = _refused(
        cur, "update outreach_messages set body = body || ' edited' where id = %s", (message_id,)
    )
    assert "immutable" in str(error).lower()


def test_the_approver_of_an_approved_message_cannot_be_replaced(cur):
    company_id, _ = _seed_company(cur)
    message_id = _approved_draft(cur, company_id)
    _as_operator(cur)
    error = _refused(
        cur,
        "update outreach_messages set approved_by_human_id = %s where id = %s",
        ("00000000-0000-4000-8000-000000000002", message_id),
    )
    assert "approval" in str(error).lower()


def test_an_approved_scorecard_cannot_be_edited_without_demotion(cur):
    """Either the edit is refused, or it drops the approval. It cannot leave an approval standing.

    The trigger demotes to ``draft`` rather than raising for substantive edits, which is the right
    product behaviour: a corrected scorecard goes back to a human, it does not stay signed.
    """
    company_id, _ = _seed_company(cur)
    scorecard_id = _approved_scorecard(cur, company_id)
    _as_operator(cur)
    cur.execute("savepoint score_edit")
    refused = False
    status = None
    try:
        cur.execute(
            "update lead_qualifications set overall_score = 99 where id = %s", (scorecard_id,)
        )
        assert cur.rowcount == 1, "the operator could not see the row: the case proves nothing"
        cur.execute(
            "select review_status from lead_qualifications where id = %s", (scorecard_id,)
        )
        status = cur.fetchone()[0]
    except psycopg.Error:
        refused = True
    cur.execute("rollback to savepoint score_edit")
    assert refused or status == "draft", f"an edit left the approval in place ({status!r})"


def test_the_reviewer_of_an_approved_scorecard_cannot_be_replaced(cur):
    company_id, _ = _seed_company(cur)
    scorecard_id = _approved_scorecard(cur, company_id)
    _as_operator(cur)
    _refused(
        cur,
        "update lead_qualifications set reviewed_by_human_id = %s where id = %s",
        ("00000000-0000-4000-8000-000000000003", scorecard_id),
    )


# ---------------------------------------------------------------------------
# Insert paths the audit walked through
# ---------------------------------------------------------------------------


def test_a_scorecard_cannot_be_created_already_approved(cur):
    company_id, _ = _seed_company(cur)
    _as_operator(cur)
    error = _refused(
        cur,
        "insert into lead_qualifications (id, company_id, evaluator_type, evaluator_identity,"
        " overall_score, customer_flow_score, development_demand_score, repeatability_orders_score,"
        " technical_fit_score, business_size_score, decision_maker_accessibility_score,"
        " commercial_fit_score, dimension_evidence, qualification_tier, recommended_next_action,"
        " review_status, reviewed_by_human_id, reviewed_at)"
        " values (gen_random_uuid(), %s, 'agent', 'hermes-qualifier-v1', 65, 10, 10, 10, 10, 10, 10,"
        " 5, %s::jsonb, 'tier_2_prospective', 'x', 'approved', %s, now())",
        (company_id, json.dumps(EVIDENCE), OPERATOR_ID),
    )
    assert "must be inserted as a draft" in str(error)


def test_a_message_cannot_be_inserted_pre_approved_or_pre_queued(cur):
    company_id, _ = _seed_company(cur)
    _approved_scorecard(cur, company_id)
    contact_id = _seed_contact(cur, company_id)
    _as_operator(cur)
    error = _refused(
        cur,
        "insert into outreach_messages (id, company_id, contact_id, channel, body,"
        " personalization_rationale, approval_status, approved_by_human_id, sending_status,"
        " personalization_evidence_ids, drafted_by_agent_identity)"
        " values (gen_random_uuid(), %s, %s, 'email', 'hello', 'guard fixture',"
        " 'approved', %s, 'queued', '{}', %s)",
        (company_id, contact_id, OPERATOR_ID, OPERATOR_ID),
    )
    assert "must be a draft" in str(error)


def test_a_message_cannot_be_drafted_for_a_company_without_an_approval(cur):
    company_id, _ = _seed_company(cur)
    contact_id = _seed_contact(cur, company_id)
    _as_operator(cur)
    error = _refused(
        cur,
        "insert into outreach_messages (id, company_id, contact_id, channel, body,"
        " personalization_rationale, approval_status, sending_status,"
        " personalization_evidence_ids, drafted_by_agent_identity)"
        " values (gen_random_uuid(), %s, %s, 'email', 'hello', 'guard fixture',"
        " 'draft', 'draft', '{}', %s)",
        (company_id, contact_id, OPERATOR_ID),
    )
    assert "no approved, non-disqualified scorecard" in str(error)


# ---------------------------------------------------------------------------
# Suppression and dispatch
# ---------------------------------------------------------------------------


def test_a_suppressed_secondary_domain_blocks_the_queue_step(cur):
    company_id, _ = _seed_company(cur)
    message_id = _approved_draft(cur, company_id)
    alias = f"alias-{company_id[:8]}.example.com"

    _as_owner(cur)
    cur.execute(
        "insert into company_domains (id, company_id, domain, is_primary)"
        " values (gen_random_uuid(), %s, %s, false)",
        (company_id, alias),
    )
    cur.execute(
        "insert into suppression_registry (id, target_type, identifier, reason, source,"
        " created_by_actor_type, created_by_actor_identity)"
        " values (gen_random_uuid(), 'domain', %s, 'guard test', 'manual', 'system', 'guard-test')",
        (alias,),
    )
    _as_operator(cur)
    error = _refused(
        cur,
        "update outreach_messages set sending_status = 'queued', scheduled_for = now() where id = %s",
        (message_id,),
    )
    assert "suppression" in str(error).lower()


def test_dispatch_states_require_a_dispatcher_identity(cur):
    company_id, _ = _seed_company(cur)
    message_id = _approved_draft(cur, company_id)
    _as_operator(cur)
    cur.execute(
        "update outreach_messages set sending_status = 'queued', scheduled_for = now() where id = %s",
        (message_id,),
    )
    error = _refused(
        cur, "update outreach_messages set sending_status = 'sending' where id = %s", (message_id,)
    )
    assert "dispatch is disabled" in str(error).lower()

    # The gate is the claim, not a blanket refusal: the same statement passes for a session that
    # carries the dispatcher role. `authenticated` has no policy for that role, so this control runs
    # as the owner — which is exactly how a future dispatch identity would be provisioned: with a
    # policy and a claim, deliberately, in one migration.
    _as_owner(cur)
    cur.execute(
        "select set_config('request.jwt.claims', %s::text, true)",
        (json.dumps({"sub": OPERATOR_ID, "role": "authenticated",
                     "app_metadata": {"user_role": "dispatcher"}}),),
    )
    cur.execute(
        "update outreach_messages set sending_status = 'sending' where id = %s", (message_id,)
    )
    cur.execute("select sending_status from outreach_messages where id = %s", (message_id,))
    assert cur.fetchone()[0] == "sending"


def test_a_drafter_agent_cannot_approve_the_draft_it_wrote(cur):
    """Drafting and approving must not be the same principal.

    The drafter needs an active task to see the row at all, which is why one is seeded: without it
    the UPDATE would match zero rows for the uninteresting reason that the row is invisible.
    """
    company_id, _ = _seed_company(cur)
    message_id = _approved_draft(cur, company_id)

    # A fresh draft in the same company, written by the drafter's own identity.
    _as_owner(cur)
    draft_id = str(uuid.uuid4())
    cur.execute(
        "select contact_id from outreach_messages where id = %s", (message_id,)
    )
    contact_id = cur.fetchone()[0]
    cur.execute(
        "insert into outreach_messages (id, company_id, contact_id, channel, subject, body,"
        " personalization_rationale, approval_status, sending_status,"
        " personalization_evidence_ids, drafted_by_agent_identity)"
        " values (%s, %s, %s, 'email', 'Hello', 'note', 'guard fixture', 'draft', 'draft', '{}',"
        " 'hermes-drafter-v1')",
        (draft_id, company_id, contact_id),
    )
    cur.execute(
        "insert into tasks (id, title, task_type, assigned_actor_type, assigned_actor_identity,"
        " company_id, contact_id, status) values (gen_random_uuid(), 'draft', 'outreach_drafting',"
        " 'agent', 'hermes-drafter-v1', %s, %s, 'in_progress')",
        (company_id, contact_id),
    )

    _as_agent(cur)
    cur.execute(
        "update outreach_messages set approval_status = 'approved', approved_by_human_id = %s"
        " where id = %s",
        (OPERATOR_ID, draft_id),
    )
    assert cur.rowcount == 0, "a drafter agent approved its own draft"

    _as_owner(cur)
    cur.execute("select approval_status from outreach_messages where id = %s", (draft_id,))
    assert cur.fetchone()[0] == "draft"


def test_deleting_a_reviewed_candidate_leaves_an_audit_row(cur):
    candidate_id = str(uuid.uuid4())
    _as_owner(cur)
    cur.execute(
        "insert into discovery_candidates (id, batch_key, domain, name, normalized_name,"
        " source_name, discovered_by_agent_identity, status)"
        " values (%s, %s, %s, 'Guard Candidate', 'guard-candidate', 'discovery_candidate',"
        " 'hermes-researcher-v1', 'pending_review')",
        (candidate_id, f"guard-{candidate_id[:8]}", f"guard-{candidate_id[:8]}.test"),
    )
    cur.execute(
        "select count(*) from audit_logs where table_name = 'discovery_candidates'"
        " and record_id = %s and action = 'DELETE'",
        (candidate_id,),
    )
    before = cur.fetchone()[0]
    cur.execute("delete from discovery_candidates where id = %s", (candidate_id,))
    cur.execute(
        "select count(*) from audit_logs where table_name = 'discovery_candidates'"
        " and record_id = %s and action = 'DELETE'",
        (candidate_id,),
    )
    assert cur.fetchone()[0] == before + 1
