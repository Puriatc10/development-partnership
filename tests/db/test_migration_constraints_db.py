"""Constraint-level regression tests for the migrations added on 2026-11-23.

These cover behaviour that the security suite does not: the reviewer-binding trigger, the audit
triggers on the Gate 1 tables, the H-06 foreign keys, and the one-primary-domain index together
with the trigger ordering that index forced.

Each test seeds inside its own transaction and rolls back, so the database is left unchanged. The
suite skips only when ``ERYNDEX_DB_URL`` is absent — never because a prerequisite row is missing.
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

#: A stable operator identity for seeded approvals; the DB binds reviews to auth.uid().
OPERATOR_ID = "00000000-0000-0000-0000-000000000001"

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="ERYNDEX_DB_URL not set: these tests need the platform database"
)


@pytest.fixture()
def conn():
    host = (urlsplit(DB_URL).hostname or "").lower()
    if host not in LOCAL_HOSTS and os.environ.get("ERYNDEX_ENV") != "production":
        pytest.fail(f"Refusing to run: ERYNDEX_DB_URL points at a non-local host ({host!r})")
    with psycopg.connect(DB_URL, connect_timeout=15) as connection:
        connection.autocommit = False
        yield connection
        connection.rollback()


def _as_operator(cur, sub: str = OPERATOR_ID) -> None:
    cur.execute("set local role authenticated")
    cur.execute(
        "select set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": sub, "role": "authenticated", "app_metadata": {"user_role": "operator"}}),),
    )


def _company(cur, tag: str) -> tuple[str, str]:
    company_id, domain = str(uuid.uuid4()), f"{tag}-{uuid.uuid4().hex[:8]}.example.com"
    cur.execute(
        """insert into companies (id, name, normalized_name, primary_domain, website_url,
                                  primary_segment, discovery_source)
           values (%s, %s, %s, %s, %s, 'digital_agency', 'constraint_test')""",
        (company_id, f"{tag} Co", f"{tag} co", domain, f"https://{domain}"),
    )
    return company_id, domain


def _candidate(cur) -> str:
    candidate_id = str(uuid.uuid4())
    cur.execute(
        """insert into discovery_candidates
               (id, name, normalized_name, domain, source_name, status, batch_key,
                discovered_by_agent_identity)
           values (%s, 'Review Co', 'review co', %s, 'constraint_test', 'pending_review',
                   'constraint', 'hermes-discovery-v1')""",
        (candidate_id, f"review-{uuid.uuid4().hex[:8]}.example.com"),
    )
    return candidate_id


def test_a_review_must_be_attributed_to_the_signing_operator(conn):
    with conn.cursor() as cur:
        candidate_id = _candidate(cur)
        _as_operator(cur)

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc:
            cur.execute(
                """update discovery_candidates
                      set status = 'approved', reviewed_by_human_id = %s, reviewed_at = now()
                    where id = %s""",
                (str(uuid.uuid4()), candidate_id),
            )
        assert exc.value.sqlstate == "42501"
        assert "Reviewer attribution violation" in str(exc.value)

    conn.rollback()


def test_the_signing_operator_can_review_and_the_decision_is_audited(conn):
    with conn.cursor() as cur:
        candidate_id = _candidate(cur)
        _as_operator(cur)
        cur.execute(
            """update discovery_candidates
                  set status = 'approved', reviewed_by_human_id = %s, reviewed_at = now()
                where id = %s""",
            (OPERATOR_ID, candidate_id),
        )
        cur.execute("select status::text from discovery_candidates where id = %s", (candidate_id,))
        assert cur.fetchone()[0] == "approved"

        cur.execute(
            "select actor_type::text, actor_identity, action from audit_logs "
            "where table_name = 'discovery_candidates' and record_id = %s and action = 'UPDATE'",
            (candidate_id,),
        )
        rows = cur.fetchall()
        assert rows, "a Gate 1 review must leave an audit row"
        assert rows[-1][0] == "human" and rows[-1][1] == OPERATOR_ID

    conn.rollback()


def test_company_creation_is_audited(conn):
    with conn.cursor() as cur:
        _as_operator(cur)
        company_id, _ = _company(cur, "audit")
        cur.execute(
            "select count(*) from audit_logs where table_name = 'companies' and record_id = %s",
            (company_id,),
        )
        assert cur.fetchone()[0] == 1

    conn.rollback()


def test_deleting_a_contact_keeps_the_activity_and_its_company(conn):
    """H-06: ON DELETE SET NULL without a column list would have tried to null company_id."""
    with conn.cursor() as cur:
        company_id, _ = _company(cur, "h6")
        contact_id, activity_id = str(uuid.uuid4()), str(uuid.uuid4())
        cur.execute(
            "insert into contacts (id, company_id, first_name, email) values (%s, %s, 'Ada', %s)",
            (contact_id, company_id, f"{uuid.uuid4().hex[:8]}@h6.example.com"),
        )
        cur.execute(
            """insert into activities (id, company_id, contact_id, activity_type, subject,
                                       performed_by_actor_type, performed_by_actor_identity)
               values (%s, %s, %s, 'note', 'probe', 'human', 'probe')""",
            (activity_id, company_id, contact_id),
        )
        cur.execute("delete from contacts where id = %s", (contact_id,))
        cur.execute(
            "select company_id is not null, contact_id is null from activities where id = %s",
            (activity_id,),
        )
        kept_company, nulled_contact = cur.fetchone()
        assert kept_company is True and nulled_contact is True

    conn.rollback()


def test_a_primary_domain_can_be_changed_and_leaves_exactly_one_primary(conn):
    """Regression: the one-primary index made a rename fail because the trigger inserted first."""
    with conn.cursor() as cur:
        company_id, first_domain = _company(cur, "rename")
        new_domain = f"renamed-{uuid.uuid4().hex[:8]}.example.com"

        cur.execute("update companies set primary_domain = %s where id = %s", (new_domain, company_id))

        cur.execute(
            "select domain, is_primary from company_domains where company_id = %s order by domain",
            (company_id,),
        )
        rows = cur.fetchall()
        primaries = [domain for domain, is_primary in rows if is_primary]
        assert primaries == [new_domain]
        assert first_domain in [domain for domain, _ in rows], "the old domain stays as a secondary"

    conn.rollback()


def test_a_second_primary_domain_is_refused_by_the_index(conn):
    with conn.cursor() as cur:
        company_id, _ = _company(cur, "oneprim")
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            cur.execute(
                "insert into company_domains (company_id, domain, is_primary) values (%s, %s, true)",
                (company_id, f"second-{uuid.uuid4().hex[:8]}.example.com"),
            )
        assert exc.value.sqlstate == "23505"
        assert "uq_company_domains_one_primary" in str(exc.value)

    conn.rollback()


def test_two_reports_of_the_same_version_are_refused(conn):
    with conn.cursor() as cur:
        company_id, _ = _company(cur, "versions")
        cur.execute(
            """insert into research_reports (company_id, research_agent_identity, report_version, summary)
               values (%s, 'hermes-researcher-v1', 1, 'first')""",
            (company_id,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation) as exc:
            cur.execute(
                """insert into research_reports (company_id, research_agent_identity, report_version, summary)
                   values (%s, 'hermes-researcher-v1', 1, 'second')""",
                (company_id,),
            )
        assert exc.value.sqlstate == "23505"
        assert "uq_research_reports_company_version" in str(exc.value)

    conn.rollback()
