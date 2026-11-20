"""Adversarial RLS probes against Development, each inside a rolled-back transaction.

Every case sets the JWT claims the policies read (``request.jwt.claims``), switches to the
PostgREST role (``authenticated`` or ``anon``), attempts one action, and rolls back. Nothing
is persisted, no RLS is weakened, and no service_role is used.

A case passes when the database behaves as the security model requires: the allowed action
succeeds, and the forbidden one fails with the error the policy is supposed to produce.

Run: python scripts/rls_probe.py            (prints a matrix, exits non-zero on a mismatch)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEV_REF = "vyfqmrdznmocnmzyjjcj"
PROD_REF = "vqnamszywsuqjxuqseas"

AGENT_A = "hermes-discovery-v1"
AGENT_B = "hermes-discovery-v2"
RESEARCHER = "hermes-researcher-v1"
OPERATOR_ID = "e1cd1ff2-080d-47c4-9589-477ce66258f3"
CANDIDATE = "11111111-1111-4111-8111-111111111111"
CANDIDATE_B = "22222222-2222-4222-8222-222222222222"


def claims(*, role: str, user_role: str | None = None, subagent_type: str | None = None,
           agent_id: str | None = None, sub: str = "subject") -> str:
    app_metadata: dict[str, str] = {}
    if user_role:
        app_metadata["user_role"] = user_role
    if subagent_type:
        app_metadata["subagent_type"] = subagent_type
    if agent_id:
        app_metadata["agent_id"] = agent_id
    return json.dumps({"sub": sub, "role": role, "app_metadata": app_metadata})


DISCOVERY_AGENT_A = claims(role="authenticated", user_role="agent", subagent_type="discovery", agent_id=AGENT_A, sub="agent-a")
DISCOVERY_AGENT_B = claims(role="authenticated", user_role="agent", subagent_type="discovery", agent_id=AGENT_B, sub="agent-b")
RESEARCH_AGENT = claims(role="authenticated", user_role="agent", subagent_type="researcher", agent_id=RESEARCHER, sub="agent-r")
OPERATOR = claims(role="authenticated", user_role="operator", sub=f"{OPERATOR_ID}")


@dataclass(frozen=True)
class Case:
    name: str
    expect: str          # "allow" or a SQLSTATE / error fragment the denial must contain
    role: str            # postgres role to switch to
    claims_json: str     # JWT claims for the transaction ("" = none)
    sql: str
    why: str


def _txn(case: Case) -> str:
    body = [f"set local role {case.role};"]
    if case.claims_json:
        body.append(f"select set_config('request.jwt.claims', $jwt${case.claims_json}$jwt$, true);")
    body.append(case.sql)
    return "begin;\n" + "\n".join(body) + "\nrollback;\n"


CANDIDATE_INSERT = """
insert into discovery_candidates
    (id, name, normalized_name, domain, source_name, status, batch_key, discovered_by_agent_identity)
values
    ('{id}', 'Probe Co', 'probe co', '{domain}', 'rls_probe', 'pending_review', 'probe-batch', '{actor}');
select count(*) as visible from discovery_candidates where id = '{id}';
"""

CASES: list[Case] = [
    Case(
        "A agent stages its own candidate",
        "allow",
        "authenticated",
        DISCOVERY_AGENT_A,
        CANDIDATE_INSERT.format(id=CANDIDATE, domain="probe-a.example.com", actor=AGENT_A),
        "the ordinary staging write must succeed",
    ),
    Case(
        "B agent forges another agent's identity",
        "42501",
        "authenticated",
        DISCOVERY_AGENT_A,
        CANDIDATE_INSERT.format(id=CANDIDATE_B, domain="probe-b.example.com", actor=AGENT_B),
        "discovered_by_agent_identity must equal current_agent_identity()",
    ),
    Case(
        "C researcher stages a discovery candidate",
        "42501",
        "authenticated",
        RESEARCH_AGENT,
        CANDIDATE_INSERT.format(id=CANDIDATE_B, domain="probe-c.example.com", actor=RESEARCHER),
        "only subagent_type='discovery' may stage",
    ),
    Case(
        "D agent approves its own candidate",
        "unchanged:pending_review",
        "authenticated",
        DISCOVERY_AGENT_A,
        f"""
insert into discovery_candidates
    (id, name, normalized_name, domain, source_name, status, batch_key, discovered_by_agent_identity)
values ('{CANDIDATE}', 'Probe Co', 'probe co', 'probe-d.example.com', 'rls_probe', 'pending_review', 'probe-batch', '{AGENT_A}');
update discovery_candidates
   set status = 'approved', reviewed_by_human_id = '{OPERATOR_ID}', reviewed_at = now()
 where id = '{CANDIDATE}';
select status::text as status from discovery_candidates where id = '{CANDIDATE}';
""",
        "an agent must not be able to satisfy Gate 1 — and because RLS filters UPDATE rows "
        "instead of raising, the proof is that the row is still pending, not that it errored",
    ),
    Case(
        "E agent reads another agent's candidate",
        "0",
        "authenticated",
        DISCOVERY_AGENT_B,
        f"""
insert into discovery_candidates
    (id, name, normalized_name, domain, source_name, status, batch_key, discovered_by_agent_identity)
values ('{CANDIDATE}', 'Probe Co', 'probe co', 'probe-e.example.com', 'rls_probe', 'pending_review', 'probe-batch', '{AGENT_B}');
set local role postgres;
insert into discovery_candidates
    (id, name, normalized_name, domain, source_name, status, batch_key, discovered_by_agent_identity)
values ('{CANDIDATE_B}', 'Other Co', 'other co', 'probe-e2.example.com', 'rls_probe', 'pending_review', 'probe-batch', '{AGENT_A}');
set local role authenticated;
select count(*) as visible from discovery_candidates where discovered_by_agent_identity = '{AGENT_A}';
""",
        "a discovery agent sees only its own rows",
    ),
    Case(
        "F anon reads discovery candidates",
        "42501",
        "anon",
        "",
        "select count(*) from discovery_candidates;",
        "anon holds no grant and no policy",
    ),
    Case(
        "G operator approves a pending candidate",
        "allow",
        "authenticated",
        OPERATOR,
        f"""
insert into discovery_candidates
    (id, name, normalized_name, domain, source_name, status, batch_key, discovered_by_agent_identity)
values ('{CANDIDATE}', 'Probe Co', 'probe co', 'probe-g.example.com', 'rls_probe', 'pending_review', 'probe-batch', '{AGENT_A}');
update discovery_candidates
   set status = 'approved', reviewed_by_human_id = '{OPERATOR_ID}', reviewed_at = now()
 where id = '{CANDIDATE}';
select status::text as status from discovery_candidates where id = '{CANDIDATE}';
""",
        "the human gate is reachable only by a human",
    ),
    Case(
        "H operator updates a candidate without the review pair",
        "23514",
        "authenticated",
        OPERATOR,
        f"""
insert into discovery_candidates
    (id, name, normalized_name, domain, source_name, status, batch_key, discovered_by_agent_identity)
values ('{CANDIDATE}', 'Probe Co', 'probe co', 'probe-h.example.com', 'rls_probe', 'pending_review', 'probe-batch', '{AGENT_A}');
update discovery_candidates set status = 'approved' where id = '{CANDIDATE}';
""",
        "chk_discovery_candidate_review_pair must refuse a reviewer-less approval",
    ),
    Case(
        "I agent attaches evidence to an approved candidate",
        "42501",
        "authenticated",
        DISCOVERY_AGENT_A,
        f"""
insert into discovery_candidates
    (id, name, normalized_name, domain, source_name, status, batch_key, discovered_by_agent_identity)
values ('{CANDIDATE}', 'Probe Co', 'probe co', 'probe-i.example.com', 'rls_probe', 'pending_review', 'probe-batch', '{AGENT_A}');
set local role postgres;
update discovery_candidates
   set status = 'approved', reviewed_by_human_id = '{OPERATOR_ID}', reviewed_at = now()
 where id = '{CANDIDATE}';
set local role authenticated;
insert into discovery_candidate_evidence (candidate_id, url, excerpt, retrieved_at, provider)
values ('{CANDIDATE}', 'https://probe-i.example.com/x', 'excerpt', now(), 'rls_probe');
""",
        "evidence may only attach while the candidate is still pending_review",
    ),
    Case(
        "J agent writes to companies",
        "42501",
        "authenticated",
        DISCOVERY_AGENT_A,
        """
insert into companies
    (name, normalized_name, primary_domain, website_url, primary_segment, discovery_source)
values ('Probe Co', 'probe co', 'probe-j.example.com', 'https://probe-j.example.com',
        'digital_agency', 'rls_probe');
""",
        "Discovery must never create CRM companies",
    ),
    Case(
        "K operator reviews a candidate it cannot see is not possible (control)",
        "allow",
        "authenticated",
        OPERATOR,
        "select count(*) as visible from discovery_candidates;",
        "control: the operator policy grants visibility",
    ),
]


#: The repo's CLI is an npm shim, so it must run in a POSIX shell — but this host's
#: ``bash`` resolves to WSL, which cannot see Windows paths. Git Bash is the shell the
#: project actually uses, so it is addressed explicitly.
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")


def _run_sql(statement: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as handle:
        handle.write(statement)
        path = handle.name
    posix_path = path.replace("\\", "/")
    shell = GIT_BASH if GIT_BASH.exists() else Path("bash")
    try:
        proc = subprocess.run(
            [str(shell), "-c", f'supabase db query -f "{posix_path}" --remote --yes'],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    if PROD_REF in output:
        raise SystemExit("refusing to continue: the CLI reported the Production project ref")
    return proc.returncode == 0, output


def main() -> int:
    failures = 0
    rows: list[tuple[str, str, str, bool]] = []
    for case in CASES:
        ok, output = _run_sql(_txn(case))
        if case.expect == "allow":
            passed = ok
        elif case.expect == "0":
            passed = ok and '"visible": 0' in output.replace("\n", " ")
        elif case.expect.startswith("unchanged:"):
            expected_value = case.expect.split(":", 1)[1]
            passed = ok and f'"status": "{expected_value}"' in output.replace("\n", " ")
        else:
            passed = (not ok) and (case.expect in output)
        rows.append((case.name, case.expect, "allowed" if ok else "refused", passed))
        if not passed:
            failures += 1
            print(f"MISMATCH  {case.name}\n  expected {case.expect}, got {'allowed' if ok else 'refused'}")
            print(f"  why it matters: {case.why}")
            print("  " + "\n  ".join(line for line in output.splitlines() if line.strip())[:1200])
    width = max(len(name) for name, _, _, _ in rows)
    print(f"\n{'case'.ljust(width)}  expected  actual   result")
    print("-" * (width + 30))
    for name, expected, actual, passed in rows:
        print(f"{name.ljust(width)}  {expected:<8}  {actual:<7}  {'PASS' if passed else 'FAIL'}")
    print(f"\n{len(rows) - failures}/{len(rows)} cases behaved as the security model requires")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
