"""Discovery persistence contract (Directive 03.6).

The staging write must match the pending `discovery_candidates` migration column
for column, carry a pre-computed batch key and a trusted execution identity, and
attach evidence to the ids the database actually returned. These tests use a fake
PostgREST client and parse the migration SQL for the schema facts, so a change to
either side fails here rather than at insert time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from discovery.identity import normalize_name
from discovery.ports import (
    CANDIDATE_COLUMNS,
    EVIDENCE_COLUMNS,
    AlreadyStaged,
    EvidencePersistenceError,
    JsonlOutboxSink,
    StagingContractError,
    SupabaseCandidateSink,
    reject_unknown_columns,
)
from discovery.service import (
    ACTOR_PATTERN,
    DiscoveryIdentityError,
    DiscoveryRequest,
    compute_batch_key,
    run_discovery,
)

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "supabase" / "migrations" / "pending" / "20261113_discovery_candidates.sql"
ACTOR = "hermes-discovery-v1"


# --- fixtures -------------------------------------------------------------


class FakeSource:
    name = "webflow_experts"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self, request):
        return list(self.rows)


class FakeClient:
    """PostgREST stand-in; records calls and returns ids like the real client."""

    def __init__(self, *, candidate_error=None, evidence_error=None, omit_ids=False):
        self.calls: list[tuple[str, list[dict]]] = []
        self.candidate_error = candidate_error
        self.evidence_error = evidence_error
        self.omit_ids = omit_ids

    def post(self, table, rows):
        self.calls.append((table, [dict(r) for r in rows]))
        if table == "discovery_candidates":
            if self.candidate_error:
                raise RuntimeError(self.candidate_error)
            if self.omit_ids:
                return [{"domain": r["domain"]} for r in rows]
            return [{"id": f"id-{r['domain']}", "domain": r["domain"]} for r in rows]
        if self.evidence_error:
            raise RuntimeError(self.evidence_error)
        return [dict(r) for r in rows]

    def table_calls(self, table):
        return [rows for name, rows in self.calls if name == table]


def discovery_request(**over):
    base = {"segments": ["webflow_studio"], "sources": ["webflow_experts"], "limit": 10, "campaign_id": "camp-1"}
    base.update(over)
    return DiscoveryRequest(**base)


def candidate(name="Northwind Studio", domain="northwind.studio", **over):
    row = {
        "name": name,
        "domain": domain,
        "source_url": f"https://webflow.com/experts/{domain}",
        "evidence": [
            {"url": f"https://{domain}/work", "excerpt": "Four commercial client case studies."},
            {"url": f"https://{domain}/about", "excerpt": "Webflow with custom JavaScript."},
        ],
    }
    row.update(over)
    return row


# --- 1: payload matches the migration ------------------------------------


def _migration_columns(table: str) -> set[str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split(f"CREATE TABLE {table} (", 1)[1].split("\n);", 1)[0]
    columns = set()
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(("CONSTRAINT", "PRIMARY", "UNIQUE", "REFERENCES", "--")):
            continue
        # a column line is "<name> <type...>"; types may be own enums (lowercase)
        match = re.match(r"([a-z_][a-z0-9_]*)\s+[A-Za-z_]", line)
        if match and match.group(1) not in {"id", "created_at", "updated_at"}:
            columns.add(match.group(1))
    return columns


#: Written only by the operator at Human Gate 1, never by a discovery agent.
OPERATOR_ONLY_COLUMNS = frozenset(
    {"reviewed_by_human_id", "reviewed_at", "review_notes", "converted_company_id"}
)


def test_candidate_payload_matches_the_migration_columns():
    declared = _migration_columns("discovery_candidates")
    assert CANDIDATE_COLUMNS == declared - OPERATOR_ONLY_COLUMNS
    assert not (CANDIDATE_COLUMNS & OPERATOR_ONLY_COLUMNS), "an agent must never write review columns"


def test_evidence_payload_matches_the_migration_columns():
    declared = _migration_columns("discovery_candidate_evidence")
    assert EVIDENCE_COLUMNS == declared


# --- 2/3/4: batch key and trusted identity -------------------------------


def test_batch_key_is_computed_before_persistence_and_carried_on_every_row():
    client = FakeClient()
    sink = SupabaseCandidateSink(client)
    outcome = run_discovery(discovery_request(), FakeSource([candidate()]), sink, actor=ACTOR)

    rows = client.table_calls("discovery_candidates")[0]
    assert rows[0]["batch_key"] == outcome.idempotency_key
    assert rows[0]["batch_key"] == compute_batch_key(discovery_request(), outcome.accepted)
    assert re.fullmatch(r"[0-9a-f]{64}", rows[0]["batch_key"])


def test_batch_key_is_order_independent_within_the_same_candidate_set():
    rows = [candidate("Alpha", "alpha.com"), candidate("Beta", "beta.com")]
    first = run_discovery(discovery_request(), FakeSource(rows), SupabaseCandidateSink(FakeClient()), actor=ACTOR)
    second = run_discovery(
        discovery_request(), FakeSource(list(reversed(rows))), SupabaseCandidateSink(FakeClient()), actor=ACTOR
    )
    assert first.idempotency_key == second.idempotency_key


def test_agent_identity_comes_from_the_execution_context_not_the_payload():
    sneaky = candidate(discovered_by_agent_identity="someone-else", source_name="reflected_by_provider")
    client = FakeClient()
    run_discovery(discovery_request(), FakeSource([sneaky]), SupabaseCandidateSink(client), actor=ACTOR)
    row = client.table_calls("discovery_candidates")[0][0]
    assert row["discovered_by_agent_identity"] == ACTOR


@pytest.mark.parametrize("bad_actor", ["", "  ", "Bad Identity", "UPPER", "a"])
def test_missing_or_invalid_identity_fails_closed(bad_actor):
    client = FakeClient()
    with pytest.raises(DiscoveryIdentityError) as exc:
        run_discovery(discovery_request(), FakeSource([candidate()]), SupabaseCandidateSink(client), actor=bad_actor)
    assert "trusted execution identity" in str(exc.value)
    assert client.calls == [], "nothing may be staged without a trusted identity"
    assert ACTOR_PATTERN.match(ACTOR)


# --- 5/6: two-stage write and evidence association -----------------------


def test_evidence_is_associated_with_the_ids_the_insert_returned():
    client = FakeClient()
    rows = [candidate("Alpha", "alpha.com"), candidate("Beta", "beta.com")]
    run_discovery(discovery_request(), FakeSource(rows), SupabaseCandidateSink(client), actor=ACTOR)

    evidence = client.table_calls("discovery_candidate_evidence")
    assert len(evidence) == 1, "the two candidates' evidence goes in one insert"
    pairs = {(r["candidate_id"], r["url"]) for r in evidence[0]}
    assert pairs == {
        ("id-alpha.com", "https://alpha.com/work"),
        ("id-alpha.com", "https://alpha.com/about"),
        ("id-beta.com", "https://beta.com/work"),
        ("id-beta.com", "https://beta.com/about"),
    }
    assert all(set(r) == EVIDENCE_COLUMNS for r in evidence[0])


def test_insert_without_ids_fails_instead_of_guessing():
    client = FakeClient(omit_ids=True)
    with pytest.raises(StagingContractError) as exc:
        run_discovery(discovery_request(), FakeSource([candidate()]), SupabaseCandidateSink(client), actor=ACTOR)
    assert "id" in str(exc.value)
    assert client.table_calls("discovery_candidate_evidence") == []


def test_evidence_write_failure_is_not_reported_as_success():
    client = FakeClient(evidence_error="42501 permission denied for table discovery_candidate_evidence")
    with pytest.raises(EvidencePersistenceError) as exc:
        run_discovery(discovery_request(), FakeSource([candidate()]), SupabaseCandidateSink(client), actor=ACTOR)
    assert exc.value.staged_candidates == 1
    assert "permission denied" in str(exc.value)


# --- 7: retry semantics --------------------------------------------------


def test_retry_of_the_same_batch_is_reported_as_already_staged_not_staged():
    for constraint in ("uq_discovery_candidates_batch_domain", "uq_discovery_candidates_pending_domain"):
        client = FakeClient(candidate_error=f'duplicate key value violates unique constraint "{constraint}"')
        outcome = run_discovery(discovery_request(), FakeSource([candidate()]), SupabaseCandidateSink(client), actor=ACTOR)
        assert outcome.staged == 0, "a unique violation is not a staged row"
        assert outcome.stats["already_staged"] == 1
        assert outcome.already_staged[0]["constraint"] == constraint
        assert outcome.already_staged[0]["same_batch"] is ("batch" in constraint)


# --- 8/9: never insert an existing company or a conflict -----------------


def test_existing_company_match_is_never_inserted():
    client = FakeClient()
    existing = [{"id": "c1", "name": "Northwind Studio", "normalized_name": normalize_name("Northwind Studio"),
                 "primary_domain": "northwind.studio"}]
    outcome = run_discovery(
        discovery_request(), FakeSource([candidate()]), SupabaseCandidateSink(client),
        actor=ACTOR, existing_companies=existing,
    )
    assert outcome.staged == 0
    assert outcome.existing_matches[0]["matched_company_id"] == "c1"
    assert client.calls == [], "no write may be attempted for a known company"


def test_domain_conflict_is_never_inserted():
    client = FakeClient()
    existing = [
        {"id": "c1", "name": "Alpha", "normalized_name": "alpha", "primary_domain": "alpha.com"},
        {"id": "c2", "name": "Beta", "normalized_name": "beta", "primary_domain": "beta.com"},
    ]
    outcome = run_discovery(
        discovery_request(), FakeSource([candidate("Beta", "alpha.com")]), SupabaseCandidateSink(client),
        actor=ACTOR, existing_companies=existing,
    )
    assert outcome.staged == 0
    assert outcome.conflicts and "domain owned by one company" in outcome.conflicts[0]["reason"]
    assert client.calls == []


# --- 10/11: RLS roles in the migration ----------------------------------


def _policies() -> dict[str, str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    found = {}
    for match in re.finditer(
        r"CREATE POLICY (\w+) ON (\w+) FOR (\w+) TO (\w+)(.*?);", sql, re.S
    ):
        found[match.group(1)] = f"{match.group(2)} FOR {match.group(3)} TO {match.group(4)}"
    return found


def test_agents_cannot_approve_reject_or_convert_candidates():
    policies = _policies()
    agent_policies = {name: body for name, body in policies.items() if name.startswith("agent_")}
    assert agent_policies, "agent staging policies must exist"
    for name, body in agent_policies.items():
        assert "FOR INSERT" in body or "FOR SELECT" in body, f"{name} grants more than insert/select: {body}"
        assert "FOR ALL" not in body
        assert "FOR UPDATE" not in body and "FOR DELETE" not in body
    # the candidate INSERT policy is the one that must pin the review columns shut
    sql = MIGRATION.read_text(encoding="utf-8")
    candidates_insert = sql.split("CREATE POLICY agent_insert_discovery_candidates", 1)[1].split(";", 1)[0]
    assert "reviewed_by_human_id IS NULL" in candidates_insert
    assert "converted_company_id IS NULL" in candidates_insert
    assert "status = 'pending_review'" in candidates_insert


def test_operator_human_gate_1_path_is_retained():
    policies = _policies()
    operator = [n for n, b in policies.items() if b.startswith("discovery_candidates") and "TO authenticated" in b]
    assert "human_operator_all_discovery_candidates" in policies
    assert "FOR ALL" in policies["human_operator_all_discovery_candidates"]
    assert operator, "an operator policy must exist on the staging table"
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "is_human_operator()" in sql


# --- 12: unknown columns cannot slip in ----------------------------------


def test_unknown_column_in_a_candidate_row_is_rejected_not_dropped():
    client = FakeClient()
    sink = SupabaseCandidateSink(client)
    row = {k: "x" for k in CANDIDATE_COLUMNS}
    row["discovery_source"] = "webflow_experts"  # the column this directive removed
    with pytest.raises(StagingContractError) as exc:
        sink.write(discovery_request(), [row], [])
    assert "discovery_source" in str(exc.value)
    assert client.calls == []


def test_reject_unknown_columns_allows_a_clean_row():
    reject_unknown_columns([{k: "x" for k in CANDIDATE_COLUMNS}], CANDIDATE_COLUMNS, "discovery_candidates")


# --- the JSONL sink keeps working for local exercises --------------------


def test_jsonl_sink_still_accepts_the_new_payload(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    outcome = run_discovery(discovery_request(), FakeSource([candidate()]), JsonlOutboxSink(outbox), actor=ACTOR)
    assert outcome.staged == 1
    line = outbox.read_text(encoding="utf-8").strip()
    assert ACTOR in line and "batch_key" in line
