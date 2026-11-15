"""Discovery persistence contract, including interrupted-write recovery.

A stateful fake PostgREST client models the two tables and their unique
constraints, so the tests can interrupt a write (evidence fails after candidates
landed) and then retry to prove recovery completes without duplicating anything.

What these tests do NOT prove: live RLS, the real `(candidate_id, url)` index, or
PostgREST filter semantics. Those are read from the migration and the client, and
are called out as unverified in the directive report.
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
    CandidatePersistenceError,
    EvidencePersistenceError,
    JsonlOutboxSink,
    PartialRecoveryError,
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
MIGRATION = REPO / "supabase" / "migrations" / "20261115212728_discovery_candidates.sql"
ACTOR = "hermes-discovery-v1"
BATCH_CONSTRAINT = "uq_discovery_candidates_batch_domain"
DOMAIN_CONSTRAINT = "uq_discovery_candidates_pending_domain"


# --- fake infrastructure --------------------------------------------------


class FakeSource:
    name = "webflow_experts"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self, request):
        return list(self.rows)


class FakeClient:
    """Stateful PostgREST stand-in: enforces both constraints and supports GET filters."""

    def __init__(self, *, fail_evidence=False, reverse_insert_response=False):
        self.candidates: list[dict] = []
        self.evidence: list[dict] = []
        self.calls: list[tuple[str, str, object]] = []
        self.fail_evidence = fail_evidence
        self.reverse_insert_response = reverse_insert_response
        self._seq = 0

    # POST -----------------------------------------------------------------
    def post(self, table, rows):
        rows = [dict(r) for r in rows]
        self.calls.append(("POST", table, rows))
        if table == "discovery_candidates":
            for row in rows:
                if any(c["batch_key"] == row["batch_key"] and c["domain"] == row["domain"] for c in self.candidates):
                    raise RuntimeError(f'duplicate key value violates unique constraint "{BATCH_CONSTRAINT}"')
                if any(c["domain"] == row["domain"] and c["status"] == "pending_review" for c in self.candidates):
                    raise RuntimeError(f'duplicate key value violates unique constraint "{DOMAIN_CONSTRAINT}"')
            created = []
            for row in rows:
                self._seq += 1
                stored = {
                    "id": f"id-{self._seq}",
                    "domain": row["domain"],
                    "status": row.get("status", "pending_review"),
                    "batch_key": row["batch_key"],
                    "discovered_by_agent_identity": row["discovered_by_agent_identity"],
                }
                self.candidates.append(stored)
                created.append({"id": stored["id"], "domain": stored["domain"]})
            return list(reversed(created)) if self.reverse_insert_response else created

        if table == "discovery_candidate_evidence":
            if self.fail_evidence:
                raise RuntimeError("42501 permission denied for table discovery_candidate_evidence")
            for row in rows:
                if any(e["candidate_id"] == row["candidate_id"] and e["url"] == row["url"] for e in self.evidence):
                    raise RuntimeError('duplicate key value violates unique constraint "uq_discovery_candidate_evidence_url"')
            self.evidence.extend(rows)
            return rows
        raise AssertionError(f"unexpected table {table}")

    # GET ------------------------------------------------------------------
    def get(self, table, params=None):
        params = dict(params or {})
        self.calls.append(("GET", table, params))
        if table == "discovery_candidates":
            rows = list(self.candidates)
            if "batch_key" in params:
                rows = [r for r in rows if params["batch_key"] == f"eq.{r['batch_key']}"]
            if "discovered_by_agent_identity" in params:
                rows = [r for r in rows if params["discovered_by_agent_identity"] == f"eq.{r['discovered_by_agent_identity']}"]
            if "domain" in params:
                rows = [r for r in rows if params["domain"] == f"eq.{r['domain']}"]
            return [{"id": r["id"], "domain": r["domain"], "status": r["status"]} for r in rows]
        if table == "discovery_candidate_evidence":
            flt = str(params.get("candidate_id", ""))
            ids = flt[4:-1].split(",") if flt.startswith("in.(") else []
            return [{"candidate_id": e["candidate_id"], "url": e["url"]} for e in self.evidence if e["candidate_id"] in ids]
        raise AssertionError(f"unexpected table {table}")

    # helpers --------------------------------------------------------------
    def calls_for(self, method, table=None):
        return [c for c in self.calls if c[0] == method and (table is None or c[1] == table)]


def discovery_request(**over):
    base = {"segments": ["webflow_studio"], "sources": ["webflow_experts"], "limit": 10, "campaign_id": "camp-1"}
    base.update(over)
    return DiscoveryRequest(**base)


def candidate(name, domain, urls=None):
    urls = urls or [f"https://{domain}/work", f"https://{domain}/about"]
    return {
        "name": name,
        "domain": domain,
        "source_url": f"https://webflow.com/experts/{domain}",
        "evidence": [{"url": u, "excerpt": f"Evidence from {u}"} for u in urls],
    }


def two_candidates():
    return [candidate("Alpha Studio", "alpha.com"), candidate("Beta Studio", "beta.com")]


def run(client, rows, **kwargs):
    return run_discovery(
        discovery_request(), FakeSource(rows), SupabaseCandidateSink(client), actor=ACTOR, **kwargs
    )


# --- schema contract ------------------------------------------------------


def _migration_columns(table: str) -> set[str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split(f"CREATE TABLE {table} (", 1)[1].split("\n);", 1)[0]
    columns = set()
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(("CONSTRAINT", "PRIMARY", "UNIQUE", "REFERENCES", "--")):
            continue
        match = re.match(r"([a-z_][a-z0-9_]*)\s+[A-Za-z_]", line)
        if match and match.group(1) not in {"id", "created_at", "updated_at"}:
            columns.add(match.group(1))
    return columns


OPERATOR_ONLY = frozenset({"reviewed_by_human_id", "reviewed_at", "review_notes", "converted_company_id"})


def test_candidate_payload_matches_the_migration_columns():
    assert CANDIDATE_COLUMNS == _migration_columns("discovery_candidates") - OPERATOR_ONLY
    assert not (CANDIDATE_COLUMNS & OPERATOR_ONLY), "an agent must never write review columns"


def test_evidence_payload_matches_the_migration_columns():
    assert EVIDENCE_COLUMNS == _migration_columns("discovery_candidate_evidence")


def test_unknown_column_is_rejected_not_dropped():
    row = {key: "x" for key in CANDIDATE_COLUMNS}
    row["discovery_source"] = "webflow_experts"
    with pytest.raises(StagingContractError) as exc:
        reject_unknown_columns([row], CANDIDATE_COLUMNS, "discovery_candidates")
    assert "discovery_source" in str(exc.value)


# --- 1: first execution ---------------------------------------------------


def test_first_execution_stages_candidates_and_evidence():
    client = FakeClient()
    outcome = run(client, two_candidates())

    assert outcome.staged == 2 and outcome.recovered == 0
    assert outcome.evidence_staged == 4
    assert len(client.candidates) == 2 and len(client.evidence) == 4
    assert {e["candidate_id"] for e in client.evidence} == {c["id"] for c in client.candidates}


def test_batch_key_is_computed_before_persistence_and_is_order_independent():
    rows = two_candidates()
    first = FakeClient()
    second = FakeClient()
    a = run(first, rows)
    b = run(second, list(reversed(rows)))
    assert a.idempotency_key == b.idempotency_key
    posted = first.calls_for("POST", "discovery_candidates")[0][2]
    assert {r["batch_key"] for r in posted} == {a.idempotency_key}


# --- 2: evidence failure is never success --------------------------------


def test_evidence_failure_leaves_candidates_and_reports_failure():
    client = FakeClient(fail_evidence=True)
    with pytest.raises(EvidencePersistenceError) as exc:
        run(client, two_candidates())
    assert exc.value.staged_candidates == 2
    assert len(client.candidates) == 2, "staged candidates must not be rolled back"
    assert client.evidence == []


# --- 3/4/5/7: retry recovers the same batch ------------------------------


def test_retry_recovers_existing_candidate_ids_and_completes_evidence():
    client = FakeClient()
    first = run(client, two_candidates())
    client.evidence.clear()  # simulate: candidates landed, evidence never did

    second = run(client, two_candidates())
    assert second.staged == 0
    assert second.recovered == 2
    assert second.evidence_staged == 4
    assert second.idempotency_key == first.idempotency_key
    assert len(client.candidates) == 2, "recovery must not insert a second candidate set"

    # the recovery GET is scoped by batch_key AND the trusted actor
    get = [c for c in client.calls_for("GET", "discovery_candidates")][-1][2]
    assert get["batch_key"] == f"eq.{first.idempotency_key}"
    assert get["discovered_by_agent_identity"] == f"eq.{ACTOR}"


def test_third_execution_duplicates_neither_candidates_nor_evidence():
    client = FakeClient()
    run(client, two_candidates())
    run(client, two_candidates())
    third = run(client, two_candidates())
    assert third.staged == 0 and third.recovered == 2
    assert third.evidence_staged == 0, "every citation already exists"
    assert len(client.candidates) == 2
    assert len(client.evidence) == 4


def test_partial_evidence_is_completed_without_duplicating_the_rest():
    client = FakeClient()
    run(client, two_candidates())
    client.evidence = [e for e in client.evidence if e["url"].endswith("/work")]  # 2 of 4 survived

    outcome = run(client, two_candidates())
    assert outcome.recovered == 2
    assert outcome.evidence_staged == 2, "only the missing citations are inserted"
    assert len(client.evidence) == 4


def test_same_batch_constraint_does_not_prevent_evidence_recovery():
    client = FakeClient()
    run(client, two_candidates())
    client.evidence.clear()
    run(client, two_candidates())
    # the second run hit the batch constraint and still wrote evidence
    assert len(client.evidence) == 4


# --- 8: another batch owns the domain ------------------------------------


def test_pending_domain_from_another_batch_is_a_conflict_not_a_recovery():
    client = FakeClient()
    # someone else staged alpha.com under a different batch/actor
    client.candidates.append(
        {
            "id": "id-other",
            "domain": "alpha.com",
            "status": "pending_review",
            "batch_key": "some-other-batch",
            "discovered_by_agent_identity": "other-agent-v1",
        }
    )
    outcome = run(client, [candidate("Alpha Studio", "alpha.com")])

    assert outcome.staged == 0 and outcome.recovered == 0
    assert outcome.already_staged[0]["reason"] == "domain_staged_elsewhere"
    assert outcome.already_staged[0]["same_batch"] is False
    assert outcome.already_staged[0]["constraint"] == DOMAIN_CONSTRAINT
    assert client.evidence == [], "no evidence may be attached to another batch's candidate"
    assert len(client.candidates) == 1, "ownership must not be overwritten"


# --- 9/13: recovery never adopts foreign or partial rows -----------------


def test_recovery_never_attaches_evidence_to_another_actors_candidate():
    client = FakeClient()
    batch_key = compute_batch_key(discovery_request(), [{"domain": "alpha.com"}])
    client.candidates.append(
        {
            "id": "id-foreign",
            "domain": "alpha.com",
            "status": "pending_review",
            "batch_key": batch_key,
            "discovered_by_agent_identity": "other-agent-v1",
        }
    )
    with pytest.raises(StagingContractError) as exc:
        # same batch_key, different actor: the scoped GET returns nothing, so the
        # insert collides and recovery has nothing it is allowed to adopt
        run(client, [candidate("Alpha Studio", "alpha.com")])
    assert "no row is visible" in str(exc.value)
    assert client.evidence == []


def test_partial_same_batch_state_is_recovered_per_candidate():
    client = FakeClient()
    run(client, two_candidates())
    client.candidates = [c for c in client.candidates if c["domain"] == "alpha.com"]  # beta never landed
    client.evidence.clear()

    outcome = run(client, two_candidates())
    assert outcome.recovered == 1, "alpha.com is re-used, not re-inserted"
    assert outcome.staged == 1, "beta.com is newly staged by this retry"
    assert outcome.evidence_staged == 4, "both candidates end up with their citations"
    assert len(client.candidates) == 2, "no duplicate candidate rows"


def test_recovery_refuses_a_candidate_that_is_no_longer_pending():
    client = FakeClient()
    run(client, two_candidates())
    for row in client.candidates:
        row["status"] = "approved"
    client.evidence.clear()
    with pytest.raises(PartialRecoveryError) as exc:
        run(client, two_candidates())
    assert "status 'approved'" in str(exc.value)


# --- 11: response integrity ----------------------------------------------


def test_missing_ids_on_insert_fail_closed():
    class NoIdClient(FakeClient):
        def post(self, table, rows):
            result = super().post(table, rows)
            if table == "discovery_candidates":
                return [{"domain": r["domain"]} for r in result]
            return result

    client = NoIdClient()
    with pytest.raises(StagingContractError) as exc:
        run(client, [candidate("Alpha Studio", "alpha.com")])
    assert "id" in str(exc.value)


def test_insert_response_order_does_not_affect_evidence_association():
    client = FakeClient(reverse_insert_response=True)
    outcome = run(client, two_candidates())
    assert outcome.evidence_staged == 4
    by_id = {c["id"]: c["domain"] for c in client.candidates}
    for row in client.evidence:
        assert row["url"].startswith(f"https://{by_id[row['candidate_id']]}/")


# --- 10/14: no bypass, no unwanted writes --------------------------------


def test_staging_uses_only_the_injected_client_and_no_service_role():
    client = FakeClient()
    sink = SupabaseCandidateSink(client)
    run_discovery(discovery_request(), FakeSource([candidate("Alpha Studio", "alpha.com")]), sink, actor=ACTOR)
    assert sink.client is client
    source = (REPO / "discovery" / "ports.py").read_text(encoding="utf-8")
    assert "service_role" not in source, "no service-role bypass may exist in the staging path"


def test_existing_company_and_domain_conflict_are_never_written():
    existing = [
        {"id": "c1", "name": "Alpha Studio", "normalized_name": normalize_name("Alpha Studio"), "primary_domain": "alpha.com"},
        {"id": "c2", "name": "Beta Studio", "normalized_name": normalize_name("Beta Studio"), "primary_domain": "beta.com"},
    ]
    client = FakeClient()
    matched = run(client, [candidate("Alpha Studio", "alpha.com")], existing_companies=existing)
    assert matched.staged == 0 and matched.existing_matches and client.calls == []

    client = FakeClient()
    conflicted = run(
        client,
        [candidate("Beta Studio", "alpha.com")],
        existing_companies=existing,
    )
    assert conflicted.staged == 0 and conflicted.conflicts and client.calls == []


def test_invalid_identity_fails_before_any_write():
    client = FakeClient()
    with pytest.raises(DiscoveryIdentityError):
        run_discovery(discovery_request(), FakeSource(two_candidates()), SupabaseCandidateSink(client), actor="")
    assert client.calls == []
    assert ACTOR_PATTERN.match(ACTOR)


def test_duplicate_urls_in_one_input_are_collapsed():
    client = FakeClient()
    row = candidate("Alpha Studio", "alpha.com", urls=["https://alpha.com/work", "https://alpha.com/work"])
    outcome = run(client, [row])
    assert outcome.evidence_staged == 1
    assert len(client.evidence) == 1


# --- Directive 03.6.2: per-candidate conflict isolation ------------------


def three_candidates():
    return [
        candidate("Alpha Studio", "alpha.com"),
        candidate("Beta Studio", "beta.com"),
        candidate("Gamma Studio", "gamma.com"),
    ]


def seed_foreign_pending(client, domain, *, batch_key="other-batch", actor="other-agent-v1"):
    client.candidates.append(
        {
            "id": f"id-foreign-{domain}",
            "domain": domain,
            "status": "pending_review",
            "batch_key": batch_key,
            "discovered_by_agent_identity": actor,
        }
    )


def test_one_cross_batch_conflict_does_not_block_the_other_candidates():
    client = FakeClient()
    seed_foreign_pending(client, "alpha.com")

    outcome = run(client, three_candidates())

    assert outcome.staged == 2, "beta and gamma must still be staged"
    assert outcome.recovered == 0
    assert [c["domain"] for c in outcome.already_staged] == ["alpha.com"]
    assert outcome.evidence_staged == 4, "evidence only for beta and gamma"
    assert sorted(c["domain"] for c in client.candidates) == ["alpha.com", "beta.com", "gamma.com"]
    assert len(client.candidates) == 3, "the foreign row is not duplicated or overwritten"


def test_only_the_conflicting_candidate_is_reported():
    client = FakeClient()
    seed_foreign_pending(client, "alpha.com")
    outcome = run(client, three_candidates())
    reported = outcome.already_staged
    assert len(reported) == 1
    assert reported[0]["domain"] == "alpha.com"
    assert reported[0]["reason"] == "domain_staged_elsewhere"
    assert reported[0]["same_batch"] is False
    assert reported[0]["constraint"] == DOMAIN_CONSTRAINT


def test_staged_candidates_in_a_mixed_batch_have_verified_ids_and_evidence():
    client = FakeClient()
    seed_foreign_pending(client, "alpha.com")
    outcome = run(client, three_candidates())

    stored = {c["domain"]: c for c in client.candidates}
    assert len(client.evidence) == 4
    ids = {e["candidate_id"] for e in client.evidence}
    assert ids == {stored["beta.com"]["id"], stored["gamma.com"]["id"]}
    assert stored["alpha.com"]["id"] == "id-foreign-alpha.com", "foreign id untouched"
    assert outcome.stats["staged"] == 2 and outcome.stats["already_staged"] == 1


def test_no_evidence_is_attached_to_the_conflicting_candidate():
    client = FakeClient()
    seed_foreign_pending(client, "alpha.com")
    run(client, three_candidates())
    assert all(e["candidate_id"] != "id-foreign-alpha.com" for e in client.evidence)
    assert all("alpha.com" not in e["url"] for e in client.evidence)


def test_a_same_batch_collision_does_not_stop_remaining_candidates():
    client = FakeClient()
    run(client, three_candidates())  # everything already staged
    client.candidates = [c for c in client.candidates if c["domain"] != "gamma.com"]
    client.evidence.clear()

    outcome = run(client, three_candidates())
    assert outcome.recovered == 2 and outcome.staged == 1
    assert outcome.evidence_staged == 6, "all three end up with citations"
    assert outcome.ok is True


def test_counts_never_mix_conflict_with_staged_or_recovered():
    client = FakeClient()
    seed_foreign_pending(client, "alpha.com")
    outcome = run(client, three_candidates())
    assert outcome.staged + outcome.recovered + len(outcome.already_staged) == len(outcome.accepted)
    assert outcome.staged == 2 and outcome.recovered == 0


def test_unknown_insert_failure_raises_with_partial_counts():
    class Exploding(FakeClient):
        def post(self, table, rows):
            if table == "discovery_candidates" and rows[0]["domain"] == "beta.com":
                raise RuntimeError("57014 statement timeout")
            return super().post(table, rows)

    client = Exploding()
    with pytest.raises(CandidatePersistenceError) as exc:
        run(client, three_candidates())
    assert exc.value.staged == ("alpha.com",)
    assert "statement timeout" in str(exc.value)
    assert client.evidence == [], "no evidence is written for a failed batch"


def test_ok_is_false_when_input_was_rejected():
    client = FakeClient()
    bad = {"name": "No Domain", "source_url": "https://webflow.com/experts/x"}
    outcome = run(client, [candidate("Beta Studio", "beta.com"), bad])
    assert outcome.rejected and outcome.ok is False


def test_ok_is_true_when_every_accepted_candidate_settled():
    client = FakeClient()
    seed_foreign_pending(client, "alpha.com")
    outcome = run(client, three_candidates())
    assert outcome.ok is True, "2 staged + 1 reported conflict settles all three"


def test_jsonl_sink_reports_created_and_evidence(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    outcome = run_discovery(discovery_request(), FakeSource(two_candidates()), JsonlOutboxSink(outbox), actor=ACTOR)
    assert outcome.staged == 2 and outcome.evidence_staged == 4
    text = outbox.read_text(encoding="utf-8")
    assert ACTOR in text and "batch_key" in text


def test_already_staged_exception_keeps_its_domains():
    exc = AlreadyStaged(DOMAIN_CONSTRAINT, 2, domains=["a.com", "b.com"])
    assert exc.domains == ("a.com", "b.com")
    assert exc.same_batch is False
