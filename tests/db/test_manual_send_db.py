"""fn_record_manual_send against the real database: every refusal, then the one happy path.

The point of recording a manual send in SQL rather than in an UPDATE is that five conditions have to
hold together, and the record has to appear on the activity trail in the same transaction. A test
that only exercised the happy path would not show that; a test that only exercised the refusals
would not show that the function is usable at all. So this module does both, and then proves the
narrow trigger exception did not widen dispatch: a raw operator ``UPDATE … SET sending_status =
'sent'`` still fails, with and without the new columns filled in.

Every case runs in the module's connection and is rolled back; nothing is left behind.
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

DB_URL = os.environ.get("ERYNDEX_DB_URL") or load_env_file().get("ERYNDEX_DB_URL") or ""
pytestmark = pytest.mark.skipif(not DB_URL, reason="ERYNDEX_DB_URL is not configured")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
OPERATOR_ID = "c549b7f9-70a8-4919-b987-aff7d692ea1b"
EXTERNAL_ID = "<CAF1a2b3c4d5e6f7@mail.gmail.com>"
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


# ---------------------------------------------------------------------------
# identity helpers, taken from the conventions in tests/db/test_update_guards_db.py
# ---------------------------------------------------------------------------


def _as_owner(cursor) -> None:
    cursor.execute("reset role")
    cursor.execute("select set_config('request.jwt.claims', '', true)")


def _as_operator(cursor, sub: str = OPERATOR_ID) -> None:
    claims = {
        "sub": sub,
        "role": "authenticated",
        "app_metadata": {"user_role": "operator", "user_type": "human"},
    }
    cursor.execute("set local role authenticated")
    cursor.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


def _as_agent(cursor, agent_id: str = "hermes-drafter-v1") -> None:
    claims = {
        "sub": "99999999-9999-4999-8999-999999999999",
        "role": "authenticated",
        "app_metadata": {"user_role": "agent", "subagent_type": "drafter", "agent_id": agent_id},
    }
    cursor.execute("set local role authenticated")
    cursor.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


def _as_claims(cursor, claims: dict) -> None:
    cursor.execute("set local role authenticated")
    cursor.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _seed_company(cursor) -> tuple[str, str]:
    company_id = str(uuid.uuid4())
    domain = f"manual-{company_id.replace('-', '')[:12]}.test"
    label = f"Manual {company_id[:8]}"
    cursor.execute(
        "insert into companies (id, name, normalized_name, primary_domain, website_url,"
        " primary_segment, discovery_source, lifecycle_stage)"
        " values (%s, %s, %s, %s, %s, 'webflow_studio', 'test', 'qualified')",
        (company_id, label, label.lower(), domain, f"https://{domain}"),
    )
    return company_id, domain


def _seed_contact(cursor, company_id: str, domain: str) -> str:
    contact_id = str(uuid.uuid4())
    cursor.execute(
        "insert into contacts (id, company_id, first_name, last_name, role_type,"
        " is_decision_maker, email) values (%s, %s, 'Manual', 'Contact',"
        " 'founder_executive', true, %s)",
        (contact_id, company_id, f"manual@{domain}"),
    )
    return contact_id


def _approved_scorecard(cursor, company_id: str) -> None:
    _as_owner(cursor)
    cursor.execute(
        "insert into lead_qualifications (id, company_id, evaluator_type, evaluator_identity,"
        " overall_score, customer_flow_score, development_demand_score, repeatability_orders_score,"
        " technical_fit_score, business_size_score, decision_maker_accessibility_score,"
        " commercial_fit_score, dimension_evidence, qualification_tier, recommended_next_action,"
        " review_status) values (%s, %s, 'agent', 'hermes-qualifier-v1', 65, 10, 10, 10, 10, 10,"
        " 10, 5, %s::jsonb, 'tier_2_prospective', 'Draft the first sequence.', 'draft')",
        (str(uuid.uuid4()), company_id, json.dumps(EVIDENCE)),
    )
    _as_operator(cursor)
    cursor.execute(
        "update lead_qualifications set review_status = 'approved', reviewed_by_human_id = %s,"
        " reviewed_at = clock_timestamp() where company_id = %s",
        (OPERATOR_ID, company_id),
    )


def _message(cursor, company_id: str, contact_id: str, *, approved: bool) -> str:
    """A message in a known state: inserted as the owner, approved as the operator."""
    message_id = str(uuid.uuid4())
    _as_owner(cursor)
    cursor.execute(
        "insert into outreach_messages (id, company_id, contact_id, channel, subject, body,"
        " personalization_rationale, approval_status, sending_status, personalization_evidence_ids,"
        " drafted_by_agent_identity) values (%s, %s, %s, 'email', 'Hello', 'A short note about the"
        " backend layer.', 'manual send fixture', 'draft', 'draft', '{}', %s)",
        (message_id, company_id, contact_id, OPERATOR_ID),
    )
    if approved:
        _as_operator(cursor)
        cursor.execute(
            "update outreach_messages set approval_status = 'approved', approved_by_human_id = %s"
            " where id = %s",
            (OPERATOR_ID, message_id),
        )
    return message_id


def _ready_message(cursor) -> tuple[str, str, str]:
    company_id, domain = _seed_company(cursor)
    _approved_scorecard(cursor, company_id)
    contact_id = _seed_contact(cursor, company_id, domain)
    message_id = _message(cursor, company_id, contact_id, approved=True)
    return company_id, contact_id, message_id


def _call(cursor, message_id: str, external_id: str | None, note: str | None = None) -> dict:
    cursor.execute(
        "select * from fn_record_manual_send(%s, %s, %s)", (message_id, external_id, note)
    )
    names = [description.name for description in cursor.description]
    row = cursor.fetchone()
    assert row is not None, "fn_record_manual_send returned no row"
    return dict(zip(names, row))


def _refused(cursor, statement: str, params: tuple = ()) -> psycopg.Error:
    cursor.execute("savepoint manual_send_case")
    with pytest.raises(psycopg.Error) as excinfo:
        cursor.execute(statement, params)
    cursor.execute("rollback to savepoint manual_send_case")
    return excinfo.value


def _state(cursor, message_id: str) -> dict:
    cursor.execute(
        "select sending_status, sent_at, sent_via, external_message_id, sent_recorded_by"
        " from outreach_messages where id = %s",
        (message_id,),
    )
    row = cursor.fetchone()
    return dict(
        zip(
            ("sending_status", "sent_at", "sent_via", "external_message_id", "sent_recorded_by"),
            row,
        )
    )


def _last_activity(cursor, company_id: str) -> dict:
    cursor.execute(
        "select activity_type, subject, description, performed_by_actor_type,"
        " performed_by_actor_identity, metadata from activities"
        " where company_id = %s order by created_at desc limit 1",
        (company_id,),
    )
    row = cursor.fetchone()
    return dict(
        zip(
            (
                "activity_type",
                "subject",
                "description",
                "performed_by_actor_type",
                "performed_by_actor_identity",
                "metadata",
            ),
            row,
        )
    )


# ---------------------------------------------------------------------------
# Refusals — each with its own message
# ---------------------------------------------------------------------------


def test_an_agent_cannot_record_a_manual_send(cur):
    _, _, message_id = _ready_message(cur)
    _as_agent(cur)
    error = _refused(cur, "select fn_record_manual_send(%s, %s, null)", (message_id, EXTERNAL_ID))
    text = str(error)
    assert "human act" in text
    assert "agent" in text
    assert "nothing was recorded" in text.lower()

    _as_owner(cur)
    assert _state(cur, message_id)["sending_status"] == "draft"


def test_a_dispatcher_claim_without_a_human_role_is_refused(cur):
    """A dispatch identity is not a human, and this function is a human's claim about their work."""
    _, _, message_id = _ready_message(cur)
    _as_claims(
        cur,
        {"sub": OPERATOR_ID, "role": "authenticated", "app_metadata": {"user_role": "dispatcher"}},
    )
    error = _refused(cur, "select fn_record_manual_send(%s, %s, null)", (message_id, EXTERNAL_ID))
    assert "human act" in str(error)
    assert "dispatcher" in str(error)


def test_an_unapproved_message_cannot_be_recorded_as_sent(cur):
    company_id, domain = _seed_company(cur)
    _approved_scorecard(cur, company_id)
    contact_id = _seed_contact(cur, company_id, domain)
    message_id = _message(cur, company_id, contact_id, approved=False)

    _as_operator(cur)
    error = _refused(cur, "select fn_record_manual_send(%s, %s, null)", (message_id, EXTERNAL_ID))
    assert "only an approved message" in str(error)
    assert "draft" in str(error)
    assert "gate G3" in str(error)

    _as_owner(cur)
    assert _state(cur, message_id)["sending_status"] == "draft"


def test_a_blank_external_id_is_refused(cur):
    _, _, message_id = _ready_message(cur)
    _as_operator(cur)
    for blank in ("", "   ", None):
        error = _refused(cur, "select fn_record_manual_send(%s, %s, null)", (message_id, blank))
        assert "external message id is blank" in str(error)
        assert "Nothing was recorded" in str(error)

    _as_owner(cur)
    assert _state(cur, message_id)["sending_status"] == "draft"


def test_an_unknown_message_id_is_refused(cur):
    _as_operator(cur)
    error = _refused(
        cur, "select fn_record_manual_send(%s, %s, null)", (str(uuid.uuid4()), EXTERNAL_ID)
    )
    assert "does not exist" in str(error)


# ---------------------------------------------------------------------------
# The happy path, and the second attempt
# ---------------------------------------------------------------------------


def test_the_happy_path_sets_the_columns_and_writes_the_activity(cur):
    company_id, contact_id, message_id = _ready_message(cur)
    _as_operator(cur)

    returned = _call(cur, message_id, f"  {EXTERNAL_ID}  ", "sent it from the shared inbox")
    assert str(returned["id"]) == message_id
    assert returned["sending_status"] == "sent"
    assert returned["sent_via"] == "manual_gmail"
    assert returned["sent_recorded_by"] == uuid.UUID(OPERATOR_ID)
    assert returned["external_message_id"] == EXTERNAL_ID  # trimmed, not stored with padding
    assert returned["sent_at"] is not None

    state = _state(cur, message_id)
    assert state["sending_status"] == "sent"
    assert state["sent_via"] == "manual_gmail"
    assert state["external_message_id"] == EXTERNAL_ID
    assert str(state["sent_recorded_by"]) == OPERATOR_ID
    assert state["sent_at"] is not None

    _as_owner(cur)
    activity = _last_activity(cur, company_id)
    # 'email_sent' is the deployed activity_type_type label for this event; nothing is invented.
    assert activity["activity_type"] == "email_sent"
    assert activity["performed_by_actor_type"] == "human"
    assert activity["performed_by_actor_identity"] == OPERATOR_ID
    assert OPERATOR_ID in activity["description"]
    assert EXTERNAL_ID in activity["description"]
    assert "sent it from the shared inbox" in activity["description"]
    assert activity["metadata"]["message_id"] == message_id
    assert activity["metadata"]["external_message_id"] == EXTERNAL_ID
    assert activity["metadata"]["sent_via"] == "manual_gmail"
    assert activity["metadata"]["recorded_by_function"] == "fn_record_manual_send"

    # The contact's own row is untouched: recording a send is not a lifecycle change.
    cur.execute("select last_contacted_at from contacts where id = %s", (contact_id,))
    assert cur.fetchone()[0] is None


def test_a_second_call_is_refused_as_already_sent(cur):
    company_id, _, message_id = _ready_message(cur)
    _as_operator(cur)
    _call(cur, message_id, EXTERNAL_ID)
    _as_owner(cur)
    first_sent_at = _state(cur, message_id)["sent_at"]

    _as_operator(cur)
    error = _refused(
        cur, "select fn_record_manual_send(%s, %s, null)", (message_id, "<second@mail.gmail.com>")
    )
    assert "already has a recorded send" in str(error)
    assert "recorded once" in str(error)

    _as_owner(cur)
    state = _state(cur, message_id)
    assert state["sent_at"] == first_sent_at
    assert state["external_message_id"] == EXTERNAL_ID
    cur.execute("select count(*) from activities where company_id = %s", (company_id,))
    assert cur.fetchone()[0] == 1, "the refused second call left an activity row behind"


# ---------------------------------------------------------------------------
# The trigger exception did not widen dispatch
# ---------------------------------------------------------------------------


def test_a_raw_operator_update_to_sent_still_fails(cur):
    _, _, message_id = _ready_message(cur)
    _as_operator(cur)
    error = _refused(
        cur,
        "update outreach_messages set sending_status = 'sent', sent_at = now() where id = %s",
        (message_id,),
    )
    assert "transition" in str(error).lower()

    _as_owner(cur)
    assert _state(cur, message_id)["sending_status"] == "draft"


def test_a_raw_operator_update_with_every_manual_send_column_still_fails(cur):
    """Filling in the columns by hand is not the same act as the function called them did."""
    _, _, message_id = _ready_message(cur)
    _as_operator(cur)
    error = _refused(
        cur,
        "update outreach_messages set sending_status = 'sent', sent_at = now(),"
        " sent_via = 'manual_gmail', external_message_id = %s, sent_recorded_by = %s where id = %s",
        (EXTERNAL_ID, OPERATOR_ID, message_id),
    )
    assert "transition" in str(error).lower()

    _as_owner(cur)
    assert _state(cur, message_id)["sending_status"] == "draft"


def test_the_new_columns_do_not_open_the_sending_state_either(cur):
    """The exception is for 'sent' via the function, not for transport states in general."""
    _, _, message_id = _ready_message(cur)
    _as_operator(cur)
    cur.execute(
        "update outreach_messages set sending_status = 'queued', scheduled_for = now()"
        " where id = %s",
        (message_id,),
    )
    error = _refused(
        cur,
        "update outreach_messages set sending_status = 'sending', sent_via = 'manual_gmail',"
        " external_message_id = %s, sent_recorded_by = %s where id = %s",
        (EXTERNAL_ID, OPERATOR_ID, message_id),
    )
    assert "dispatch is disabled" in str(error).lower()


# ---------------------------------------------------------------------------
# The schema's own vocabulary
# ---------------------------------------------------------------------------


def test_sent_via_only_accepts_the_three_deployed_values(cur):
    _, _, message_id = _ready_message(cur)
    _as_owner(cur)
    error = _refused(
        cur,
        "update outreach_messages set sent_via = 'carrier_pigeon' where id = %s",
        (message_id,),
    )
    assert "chk_outreach_sent_via" in str(error)


def test_a_manual_gmail_record_must_carry_its_attribution(cur):
    _, _, message_id = _ready_message(cur)
    _as_owner(cur)
    error = _refused(
        cur,
        "update outreach_messages set sent_via = 'manual_gmail', external_message_id = null,"
        " sent_recorded_by = null where id = %s",
        (message_id,),
    )
    assert "chk_outreach_manual_send_attribution" in str(error)
