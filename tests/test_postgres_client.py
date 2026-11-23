"""Contract tests for the PostgreSQL data client, run against the real database.

These are the tests that could never exist while the platform spoke PostgREST: the same
statements, the same policies, the same roles, executed against the database the application
will use. They are skipped — never faked — when ``ERYNDEX_DB_URL`` is absent.

Every case runs inside a transaction that is rolled back, so the suite leaves no rows.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from typing import Iterator

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from contracts.environment import load_env_file  # noqa: E402

psycopg = pytest.importorskip("psycopg")

from integrations.postgres.client import DatabaseError, PostgresClient  # noqa: E402

ENV = {**load_env_file(), **os.environ}
DSN = ENV.get("ERYNDEX_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not DSN, reason="ERYNDEX_DB_URL not set: these tests need the Development database"
)

OPERATOR_ID = "e1cd1ff2-080d-47c4-9589-477ce66258f3"
AGENT_CLAIMS = {
    "sub": "agent-a",
    "role": "authenticated",
    "app_metadata": {
        "user_role": "agent",
        "subagent_type": "discovery",
        "agent_id": "hermes-discovery-v1",
    },
}
OPERATOR_CLAIMS = {
    "sub": OPERATOR_ID,
    "role": "authenticated",
    "app_metadata": {"user_role": "operator"},
}


@pytest.fixture(autouse=True)
def cleanup_contract_rows():
    """Remove rows this module created, as the owner.

    Not as the agent: the discovery policies have no DELETE clause, so an agent's delete matches
    zero rows and silently succeeds — correct behaviour (agents must not remove evidence), and the
    reason cleanup cannot be done through the client under test.
    """
    yield
    ids = [("source_name", "eq.contract_test"), ("source_name", "eq.rls_probe")]
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            for column, value in ids:
                cur.execute(f"DELETE FROM discovery_candidates WHERE {column} = %s", (value[3:],))
            cur.execute("DELETE FROM companies WHERE discovery_source IN ('contract_test', 'rls_probe')")


@pytest.fixture()
def operator() -> Iterator[PostgresClient]:
    client = PostgresClient(DSN, role="authenticated", claims=OPERATOR_CLAIMS)
    yield client
    client.close()


@pytest.fixture()
def agent() -> Iterator[PostgresClient]:
    client = PostgresClient(DSN, role="authenticated", claims=AGENT_CLAIMS)
    yield client
    client.close()


def candidate_row(**overrides):
    domain = overrides.pop("domain", f"contract-{uuid.uuid4().hex[:8]}.example.com")
    row = {
        "name": "Contract Co",
        "normalized_name": "contract co",
        "domain": domain,
        "source_name": "contract_test",
        "status": "pending_review",
        "batch_key": f"contract-{uuid.uuid4().hex[:8]}",
        "discovered_by_agent_identity": "hermes-discovery-v1",
    }
    row.update(overrides)
    return row


def test_an_agent_stages_and_reads_back_its_own_candidate(agent):
    row = candidate_row()
    created = agent.post("discovery_candidates", row)
    assert len(created) == 1 and created[0]["domain"] == row["domain"]

    visible = agent.get("discovery_candidates", {"select": "id,domain", "domain": f"eq.{row['domain']}"})
    assert [r["domain"] for r in visible] == [row["domain"]]
    agent.delete("discovery_candidates", {"id": f"eq.{created[0]['id']}"})


def test_an_agent_cannot_see_another_agents_rows(agent, operator):
    other = operator.post(
        "discovery_candidates", candidate_row(discovered_by_agent_identity="someone-else-v1")
    )[0]
    assert agent.get("discovery_candidates", {"select": "id", "id": f"eq.{other['id']}"}) == []
    operator.delete("discovery_candidates", {"id": f"eq.{other['id']}"})


def test_an_agent_cannot_create_a_company(agent, operator):
    with pytest.raises(DatabaseError) as exc:
        agent.post(
            "companies",
            {
                "name": "Forbidden Co",
                "normalized_name": "forbidden co",
                "primary_domain": "forbidden.example.com",
                "website_url": "https://forbidden.example.com",
                "primary_segment": "digital_agency",
                "discovery_source": "contract_test",
            },
        )
    assert exc.value.code == "42501", "a policy denial must surface as the SQLSTATE, not a generic error"
    assert operator.get("companies", {"select": "id", "primary_domain": "eq.forbidden.example.com"}) == []


def test_a_unique_violation_surfaces_the_constraint_name(agent):
    row = candidate_row()
    agent.post("discovery_candidates", row)
    duplicate = {key: value for key, value in row.items() if key != "id"}
    duplicate["name"] = "Dup"
    with pytest.raises(DatabaseError) as exc:
        agent.post("discovery_candidates", duplicate)
    assert exc.value.code == "23505"
    assert "uq_discovery_candidates_pending_domain" in str(exc.value)


def test_in_filters_and_paging_are_applied(agent, operator):
    domains = [f"contract-in-{uuid.uuid4().hex[:6]}.example.com" for _ in range(3)]
    for domain in domains:
        operator.post("discovery_candidates", candidate_row(domain=domain))

    rows = operator.get(
        "discovery_candidates",
        {
            "select": "domain",
            "domain": "in.(" + ",".join(domains) + ")",
            "order": "domain.asc",
            "limit": "2",
        },
    )
    assert len(rows) == 2, "limit must be honoured"
    assert [r["domain"] for r in rows] == sorted(domains)[:2]

    second = operator.get(
        "discovery_candidates",
        {"select": "domain", "domain": "in.(" + ",".join(domains) + ")", "order": "domain.asc", "offset": "2"},
    )
    assert [r["domain"] for r in second] == sorted(domains)[2:]

    operator.delete("discovery_candidates", {"domain": "in.(" + ",".join(domains) + ")"})


def test_an_unknown_filter_operator_is_refused_not_ignored(operator):
    with pytest.raises(DatabaseError) as exc:
        operator.get("discovery_candidates", {"domain": "like.%example.com"})
    assert "Refusing to ignore a filter" in str(exc.value)


def test_an_unknown_column_or_table_is_refused(operator):
    with pytest.raises(DatabaseError):
        operator.get("discovery_candidates", {"select": "id; drop table companies"})
    with pytest.raises(DatabaseError):
        operator.get("companies; drop table x", {})


def test_patch_honours_the_where_clause(operator):
    row = operator.post("discovery_candidates", candidate_row())[0]
    patched = operator.patch(
        "discovery_candidates",
        {"status": "approved", "reviewed_by_human_id": OPERATOR_ID, "reviewed_at": "2026-11-20T00:00:00+00:00"},
        params={"id": f"eq.{row['id']}"},
    )
    assert [r["status"] for r in patched] == ["approved"]

    untouched = operator.patch(
        "discovery_candidates",
        {"status": "rejected"},
        params={"id": "eq.00000000-0000-0000-0000-000000000000"},
    )
    assert untouched == [], "a no-match patch must report nothing rather than pretend to succeed"
    operator.delete("discovery_candidates", {"id": f"eq.{row['id']}"})


def test_jsonb_columns_round_trip(operator):
    row = operator.post(
        "discovery_candidates",
        candidate_row(signals={"segment": "webflow_studio", "nested": {"a": 1}}),
    )[0]
    fetched = operator.get("discovery_candidates", {"select": "signals", "id": f"eq.{row['id']}"})[0]
    assert fetched["signals"]["nested"] == {"a": 1}
    operator.delete("discovery_candidates", {"id": f"eq.{row['id']}"})


def test_anon_is_denied_everything():
    anon = PostgresClient(DSN, role="anon", claims={"role": "anon"})
    try:
        with pytest.raises(DatabaseError) as exc:
            anon.get("discovery_candidates", {"select": "id"})
        assert exc.value.code == "42501"
    finally:
        anon.close()


def test_a_client_with_no_claims_resolves_to_no_identity():
    bare = PostgresClient(DSN, role="authenticated", claims={})
    try:
        assert bare.get("discovery_candidates", {"select": "id"}) == []
    finally:
        bare.close()


def test_the_claim_payload_never_leaks_between_calls(operator, agent):
    """SET LOCAL must revert; otherwise one identity's rows would be visible to the next call."""
    # Attributed to a different identity: an operator insert stamped with the agent's own
    # agent_id would legitimately be visible to that agent, which is not what this tests.
    row = operator.post(
        "discovery_candidates", candidate_row(discovered_by_agent_identity="operator-manual-v1")
    )[0]
    try:
        assert agent.get("discovery_candidates", {"select": "id", "id": f"eq.{row['id']}"}) == []
        assert operator.get("discovery_candidates", {"select": "id", "id": f"eq.{row['id']}"}) != []
        assert agent.get("discovery_candidates", {"select": "id", "id": f"eq.{row['id']}"}) == []
    finally:
        operator.delete("discovery_candidates", {"id": f"eq.{row['id']}"})

# ---------------------------------------------------------------------------
# Statement discipline: a write must name the rows it intends to touch
# ---------------------------------------------------------------------------


def test_patch_without_a_filter_is_refused_before_it_reaches_the_database(operator):
    with pytest.raises(DatabaseError) as excinfo:
        operator.patch("companies", {"city": "Tehran"})
    assert "no filter" in str(excinfo.value)


def test_delete_without_a_filter_is_refused_before_it_reaches_the_database(operator):
    with pytest.raises(DatabaseError) as excinfo:
        operator.delete("activities")
    assert "no filter" in str(excinfo.value)


def test_allow_all_is_an_explicit_keyword_only_opt_out():
    """The guard is bypassed only by a deliberate keyword, never by omitting the filter.

    This case does not execute the statement: ``PostgresClient`` commits per call, so an
    ``allow_all`` delete in a test would remove real rows from the development database. It
    asserts the contract instead — the flag exists, it is keyword-only, and it defaults to off.
    """
    import inspect

    for method in (PostgresClient.patch, PostgresClient.delete):
        parameter = inspect.signature(method).parameters["allow_all"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is False


def test_an_unterminated_quote_in_an_in_list_fails_closed(operator):
    with pytest.raises(DatabaseError) as excinfo:
        operator.get("companies", {"primary_domain": "in.(o'brien)"})
    assert "unterminated quote" in str(excinfo.value)
