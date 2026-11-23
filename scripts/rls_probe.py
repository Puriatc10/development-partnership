"""Adversarial RLS probes against the platform database, all rolled back per call.

Each case builds a client with the claims the policies read, attempts one action, and checks the
outcome the security model requires. This used to shell out to the Supabase CLI with temporary
SQL files and a mutable linked-project target; it now runs in process against our own database,
which is also why the old pre-flight file check is gone: the guard is the connection string.

Run: python scripts/rls_probe.py
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from contracts.environment import DatabaseConfig, assert_database_environment  # noqa: E402
from contracts.errors import EnvironmentGuardError  # noqa: E402
from integrations.postgres.client import DatabaseError, PostgresClient  # noqa: E402

OPERATOR_ID = "e1cd1ff2-080d-47c4-9589-477ce66258f3"
AGENT_A = "hermes-discovery-v1"
AGENT_B = "hermes-discovery-v2"

OPERATOR_CLAIMS = {"sub": OPERATOR_ID, "role": "authenticated", "app_metadata": {"user_role": "operator"}}
AGENT_A_CLAIMS = {
    "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-a")),
    "role": "authenticated",
    "app_metadata": {"user_role": "agent", "subagent_type": "discovery", "agent_id": AGENT_A},
}
AGENT_B_CLAIMS = {
    "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-b")),
    "role": "authenticated",
    "app_metadata": {"user_role": "agent", "subagent_type": "discovery", "agent_id": AGENT_B},
}
RESEARCHER_CLAIMS = {
    "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-r")),
    "role": "authenticated",
    "app_metadata": {"user_role": "agent", "subagent_type": "researcher", "agent_id": "hermes-researcher-v1"},
}


@dataclass
class Case:
    name: str
    run: Callable[[dict[str, PostgresClient]], Any]
    expect: str  # allow | deny | empty | unchanged | approved | rows
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _domain(tag: str) -> str:
    return f"probe-{tag}-{uuid.uuid4().hex[:6]}.example.com"


def stage(client: PostgresClient, *, identity: str, domain: str) -> dict[str, Any]:
    return client.post(
        "discovery_candidates",
        {
            "name": "Probe Co",
            "normalized_name": "probe co",
            "domain": domain,
            "source_name": "rls_probe",
            "status": "pending_review",
            "batch_key": f"probe-{uuid.uuid4().hex[:8]}",
            "discovered_by_agent_identity": identity,
        },
    )[0]


def _approve(client: PostgresClient, row_id: Any) -> None:
    client.patch(
        "discovery_candidates",
        {
            "status": "approved",
            "reviewed_by_human_id": OPERATOR_ID,
            "reviewed_at": "2026-11-20T00:00:00+00:00",
        },
        params={"id": f"eq.{row_id}"},
    )


def build_cases() -> list[Case]:
    def a(c):
        row = stage(c["agent_a"], identity=AGENT_A, domain=_domain("a"))
        assert row["status"] == "pending_review"
        c["operator"].delete("discovery_candidates", {"id": f"eq.{row['id']}"})
        return "staged"

    def b(c):
        stage(c["agent_a"], identity=AGENT_B, domain=_domain("b"))
        return "staged"

    def cc(c):
        stage(c["researcher"], identity="hermes-researcher-v1", domain=_domain("c"))
        return "staged"

    def d(c):
        row = stage(c["agent_a"], identity=AGENT_A, domain=_domain("d"))
        _approve(c["agent_a"], row["id"])
        status = c["operator"].get("discovery_candidates", {"select": "status", "id": f"eq.{row['id']}"})
        c["operator"].delete("discovery_candidates", {"id": f"eq.{row['id']}"})
        return status[0]["status"] if status else "missing"

    def e(c):
        row = stage(c["operator"], identity="operator-manual-v1", domain=_domain("e"))
        seen = c["agent_b"].get("discovery_candidates", {"select": "id", "id": f"eq.{row['id']}"})
        c["operator"].delete("discovery_candidates", {"id": f"eq.{row['id']}"})
        return seen

    def f(c):
        return c["anon"].get("discovery_candidates", {"select": "id"})

    def g(c):
        row = stage(c["operator"], identity="operator-manual-v1", domain=_domain("g"))
        patched = c["operator"].patch(
            "discovery_candidates",
            {
                "status": "approved",
                "reviewed_by_human_id": OPERATOR_ID,
                "reviewed_at": "2026-11-20T00:00:00+00:00",
            },
            params={"id": f"eq.{row['id']}"},
        )
        c["operator"].delete("discovery_candidates", {"id": f"eq.{row['id']}"})
        return patched[0]["status"] if patched else "missing"

    def h(c):
        row = stage(c["operator"], identity="operator-manual-v1", domain=_domain("h"))
        try:
            c["operator"].patch("discovery_candidates", {"status": "approved"}, params={"id": f"eq.{row['id']}"})
            return "patched"
        finally:
            c["operator"].delete("discovery_candidates", {"id": f"eq.{row['id']}"})

    def i(c):
        row = stage(c["operator"], identity=AGENT_A, domain=_domain("i"))
        _approve(c["operator"], row["id"])
        try:
            c["agent_a"].post(
                "discovery_candidate_evidence",
                {
                    "candidate_id": row["id"],
                    "url": "https://probe.example.com/x",
                    "excerpt": "excerpt",
                    "provider": "rls_probe",
                },
            )
            return "inserted"
        finally:
            c["operator"].delete("discovery_candidates", {"id": f"eq.{row['id']}"})

    def j(c):
        return c["agent_a"].post(
            "companies",
            {
                "name": "Probe Co",
                "normalized_name": "probe co",
                "primary_domain": _domain("j"),
                "website_url": "https://probe.example.com",
                "primary_segment": "digital_agency",
                "discovery_source": "rls_probe",
            },
        )

    def k(c):
        row = stage(c["operator"], identity="operator-manual-v1", domain=_domain("k"))
        seen = c["operator"].get("discovery_candidates", {"select": "id", "id": f"eq.{row['id']}"})
        c["operator"].delete("discovery_candidates", {"id": f"eq.{row['id']}"})
        return len(seen)

    return [
        Case("A agent stages its own candidate", a, "allow", "the ordinary staging write must succeed"),
        Case("B agent forges another agent's identity", b, "deny", "discovered_by_agent_identity must equal current_agent_identity()"),
        Case("C researcher stages a discovery candidate", cc, "deny", "only subagent_type='discovery' may stage"),
        Case("D agent approves its own candidate", d, "unchanged", "an agent must not be able to satisfy Gate 1"),
        Case("E agent reads another agent's candidate", e, "empty", "a discovery agent sees only its own rows"),
        Case("F anon reads discovery candidates", f, "deny", "anon holds no grant and no policy"),
        Case("G operator approves a pending candidate", g, "approved", "the human gate is reachable only by a human"),
        Case("H operator approves without the review pair", h, "deny", "chk_discovery_candidate_review_pair refuses a reviewer-less approval"),
        Case("I agent attaches evidence to an approved candidate", i, "deny", "evidence may only attach while the candidate is pending_review"),
        Case("J agent writes to companies", j, "deny", "Discovery must never create CRM companies"),
        Case("K operator sees the row it is about to review", k, "rows", "control: the operator policy grants visibility"),
    ]


def judge(case: Case, outcome: Any) -> tuple[bool, str]:
    if isinstance(outcome, Exception):
        if case.expect == "deny":
            return isinstance(outcome, DatabaseError), f"raised {outcome.__class__.__name__}"
        return False, f"raised {outcome!r}"
    if case.expect == "deny":
        return False, f"allowed ({outcome!r})"
    if case.expect == "allow":
        return True, "allowed"
    if case.expect == "empty":
        return outcome == [], f"{len(outcome)} row(s) visible"
    if case.expect == "unchanged":
        return outcome == "pending_review", f"status={outcome}"
    if case.expect == "approved":
        return outcome == "approved", f"status={outcome}"
    if case.expect == "rows":
        return outcome == 1, f"{outcome} row(s) visible"
    return False, f"unknown expectation {case.expect!r}"


def main() -> int:
    try:
        config = DatabaseConfig.load()
        assert_database_environment(config)
    except EnvironmentGuardError as exc:
        print(f"environment refused: {exc}", file=sys.stderr)
        return 2

    print(f"target verified: {config.database} on {config.host} ({config.env})\n")
    clients = {
        "operator": PostgresClient(config.dsn, role="authenticated", claims=OPERATOR_CLAIMS),
        "agent_a": PostgresClient(config.dsn, role="authenticated", claims=AGENT_A_CLAIMS),
        "agent_b": PostgresClient(config.dsn, role="authenticated", claims=AGENT_B_CLAIMS),
        "researcher": PostgresClient(config.dsn, role="authenticated", claims=RESEARCHER_CLAIMS),
        "anon": PostgresClient(config.dsn, role="anon", claims={"role": "anon"}),
    }

    rows: list[tuple[str, str, str, bool]] = []
    try:
        for case in build_cases():
            try:
                outcome: Any = case.run(clients)
            except Exception as exc:  # noqa: BLE001 - a refusal is a result, not a crash
                outcome = exc
            passed, observed = judge(case, outcome)
            rows.append((case.name, case.expect, observed, passed))
            if not passed:
                print(f"MISMATCH {case.name}\n  expected {case.expect}, observed {observed}")
                print(f"  why it matters: {case.detail}")
    finally:
        for client in clients.values():
            client.close()

    width = max(len(name) for name, _, _, _ in rows)
    print(f"\n{'case'.ljust(width)}  expected   observed            result")
    print("-" * (width + 40))
    for name, expect, observed, passed in rows:
        print(f"{name.ljust(width)}  {expect:<10} {observed:<19} {'PASS' if passed else 'FAIL'}")

    failures = sum(1 for *_, passed in rows if not passed)
    print(f"\n{len(rows) - failures}/{len(rows)} cases behaved as the security model requires")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
