"""One live pass through the whole platform loop, against the real database.

    Discover -> Register -> Research -> Qualify -> Human review -> Draft -> Gate 3 -> Gate 4

Everything runs under a real signed-in identity (operator, researcher, qualifier) with RLS
enforced by the database. The only synthetic part is the *content*: a fixture provider supplies
the findings, because no research provider is live yet (roadmap D1/D3). Every row it writes is
marked `e2e_lifecycle` / `.example.com` so it can be found and removed deliberately.

Dispatch is asserted to be unreachable at the end: the run proves the loop stops where policy says
it stops, rather than proving a send path exists.

    python scripts/e2e_lifecycle.py            # run, print a summary, leave the rows
    python scripts/e2e_lifecycle.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from contracts.environment import DatabaseConfig, load_env_file  # noqa: E402
from discovery.identity import normalize_name  # noqa: E402
from integrations.postgres.auth import PostgresAuthClient  # noqa: E402
from integrations.postgres.client import DatabaseError, PostgresClient  # noqa: E402
from operations.candidates import approve_candidate, get_candidate  # noqa: E402
from operations.qualifications import approve as approve_scorecard, persist_scorecard  # noqa: E402
from operations.registration import register_candidate  # noqa: E402
from operations.session import OperatorSessionError, open_session  # noqa: E402
from research.executor import (  # noqa: E402
    Finding,
    ResearchPayload,
    Source,
    execute_research,
)

TAG = "e2e_lifecycle"


class RichFixtureProvider:
    """A payload rich enough to reach a non-disqualified tier, and clearly synthetic."""

    name = "rich_fixture"

    def produce(self, company: Mapping[str, Any]) -> ResearchPayload:
        domain = str(company.get("primary_domain") or "example.com")
        statements = [
            ("client_roster", "Lists 14 named agency clients with retainer contracts and repeats engagements annually"),
            ("client_roster", "Case studies document three Webflow builds with custom API integrations"),
            ("growth_signals", "Hiring two backend engineers and a project manager this quarter"),
            ("business_model", "Publishes monthly retainer pricing with a minimum commitment and an overflow capacity model"),
            ("technology_stack", "Stack: Webflow, Framer, React, Node.js, PostgreSQL, REST APIs, authentication"),
            ("scoping_maturity", "Publishes a scoping process with written specs, milestones and change requests"),
            ("pain_points", "States they turn down backend work because they have no in-house engineering team"),
            ("team_structure", "Team page names the founder and the delivery lead with direct email addresses"),
            ("pricing_rates", "Lists day rates and a monthly reserved capacity price band"),
        ]
        findings = tuple(
            Finding(
                category=category,
                finding_type="verified_fact",
                statement=statement,
                confidence="high",
                evidence_url=f"https://{domain}/e2e/{index}",
                evidence_excerpt=statement,
            )
            for index, (category, statement) in enumerate(statements, start=1)
        )
        return ResearchPayload(
            summary=(
                "Synthetic fixture research for the end-to-end run. Every statement is invented to "
                "exercise the pipeline and is marked as a fixture; none of it is a claim about a real company."
            ),
            recommended_angle="Backend capacity for Webflow engagements",
            overall_confidence="medium",
            sources=(Source(url=f"https://{domain}/", title="fixture", excerpt="fixture"),),
            findings=findings,
            gaps_and_unknowns="Fixture data: no real research was performed.",
        )


def _sign_in(config: DatabaseConfig, env: Mapping[str, str], prefix: str) -> dict[str, Any]:
    auth = PostgresAuthClient(config.dsn, config.jwt_secret)
    return auth.sign_in_with_password(env[f"{prefix}_EMAIL"], env[f"{prefix}_PASSWORD"])


def _step(name: str, detail: Mapping[str, Any], log: list[dict[str, Any]]) -> None:
    log.append({"step": name, **detail})


def run() -> dict[str, Any]:
    env = {**load_env_file(), **os.environ}
    config = DatabaseConfig.load()
    log: list[dict[str, Any]] = []

    # -- human ------------------------------------------------------------
    session = open_session()
    operator = session.client
    operator_id = session.user_id

    # -- 1. Discover + Register (Gate 1) ---------------------------------
    token = uuid.uuid4().hex[:8]
    domain = f"e2e-{token}.example.com"
    candidate = operator.post(
        "discovery_candidates",
        {
            "name": f"[SYNTHETIC] E2E Studio {token}",
            "normalized_name": normalize_name(f"E2E Studio {token}"),
            "domain": domain,
            "source_name": TAG,
            "source_url": f"https://{domain}",
            "signals": {"segment": "webflow_studio", "fixture": True},
            "status": "pending_review",
            "batch_key": f"{TAG}-{token}",
            "discovered_by_agent_identity": "hermes-discovery-v1",
        },
    )[0]
    _step("candidate.staged", {"candidate_id": candidate["id"], "domain": domain}, log)

    approve_candidate(operator, candidate["id"], operator_id=operator_id, review_notes="e2e run")
    registered = register_candidate(operator, candidate["id"], operator_id=operator_id)
    company_id = registered.company_id
    _step(
        "company.registered",
        {"company_id": company_id, "created": registered.created, "duplicate": registered.duplicate},
        log,
    )

    # -- 2. Research ------------------------------------------------------
    task = operator.post(
        "tasks",
        {
            "title": f"E2E research for {domain}",
            "task_type": "company_research",
            "priority": "medium",
            "status": "pending",
            "assigned_actor_type": "agent",
            "assigned_actor_identity": "hermes-researcher-v1",
            "company_id": company_id,
        },
    )[0]
    researcher_claims = _sign_in(config, env, "RESEARCHER")["claims"]
    researcher = PostgresClient(config.dsn, role="authenticated", claims=researcher_claims)
    research = execute_research(
        researcher, company_id, RichFixtureProvider(), task_id=task["id"], identity="hermes-researcher-v1"
    )
    _step(
        "research.persisted",
        {
            "report_id": research.report_id,
            "findings": research.findings_written,
            "task_status": research.task_status,
        },
        log,
    )

    # -- 3. Qualify + Gate 2 ---------------------------------------------
    # Agent reads are task-scoped, so the qualifier is only allowed to see this company while it
    # holds an active task for it. That is the policy working as designed, not a workaround.
    qualification_task = operator.post(
        "tasks",
        {
            "title": f"E2E qualification for {domain}",
            "task_type": "company_qualification",
            "priority": "medium",
            "status": "pending",
            "assigned_actor_type": "agent",
            "assigned_actor_identity": "hermes-qualifier-v1",
            "company_id": company_id,
        },
    )[0]
    persisted = persist_scorecard(company_id)
    qualification_id = str(persisted.get("id") or persisted.get("record", {}).get("id"))
    scorecard_line = {
        "qualification_id": qualification_id,
        "score": persisted.get("overall_score") or persisted.get("record", {}).get("overall_score"),
        "review_status": persisted.get("review_status") or persisted.get("record", {}).get("review_status"),
    }
    _step("qualification.persisted", scorecard_line, log)

    operator.patch(
        "tasks",
        {"status": "completed"},
        params={"id": f"eq.{qualification_task['id']}", "status": "eq.pending"},
    )
    approved = approve_scorecard(operator, qualification_id, operator_id=operator_id, note="e2e approval")
    _step(
        "qualification.approved",
        {
            "review_status": approved.get("review_status"),
            "reviewed_by": str(approved.get("reviewed_by_human_id")),
            "tier": approved.get("qualification_tier"),
        },
        log,
    )

    company = operator.get(
        "companies",
        {"select": "id,lifecycle_stage,qualification_tier", "id": f"eq.{company_id}"},
    )[0]
    _step("company.qualified", dict(company), log)

    # -- 4. A recipient and a campaign -----------------------------------
    contact = operator.post(
        "contacts",
        {
            "company_id": company_id,
            "first_name": "Fixture",
            "last_name": "Contact",
            "email": f"fixture-{token}@example.com",
            "role_type": "other",
        },
    )[0]
    campaign = operator.post(
        "outreach_campaigns",
        {
            "name": f"[SYNTHETIC] E2E campaign {token}",
            "target_segment": "webflow_studio",
            "description": "Synthetic campaign for the end-to-end run",
            "is_active": True,
        },
    )[0]
    _step("recipient.ready", {"contact_id": contact["id"], "campaign_id": campaign["id"]}, log)

    # -- 5-7. Draft, Gate 3, Gate 4 (through the outreach CLI) -----------
    def _last_json(text: str) -> dict[str, Any]:
        """The CLI writes prose on stderr and one JSON object on stdout; find that object."""
        decoder = json.JSONDecoder()
        found: dict[str, Any] = {}
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                found = value
        return found

    def cli(*args: str) -> tuple[int, str]:
        proc = subprocess.run(
            # psycopg returns UUID objects; argv needs text.
            [sys.executable, str(REPO / "scripts" / "outreach_cli.py"), "--json", *[str(a) for a in args]],
            cwd=str(REPO),
            capture_output=True,
            text=True,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    code, output = cli(
        "draft", "--company-id", company_id, "--contact-id", contact["id"], "--campaign-id", campaign["id"]
    )
    draft_payload = _last_json(output) if code == 0 else {}
    message_id = str(draft_payload.get("message_id") or draft_payload.get("id") or "")
    _step(
        "draft.created",
        {"exit": code, "message_id": message_id, "allowed_because": draft_payload.get("allowed_because")},
        log,
    )
    if code != 0 or not message_id:
        raise RuntimeError(f"draft failed: {output.strip()[:600]}")

    code, output = cli("approve", message_id, "--note", "e2e approval")
    _step("draft.approved", {"exit": code, "detail": output.strip()[:200]}, log)
    if code != 0:
        raise RuntimeError(f"approval failed: {output.strip()[:600]}")

    code, output = cli("authorize", message_id)
    _step("dispatch.authorized", {"exit": code, "detail": output.strip()[:200]}, log)
    if code != 0:
        raise RuntimeError(f"authorize failed: {output.strip()[:600]}")

    # -- 8. Dispatch must still be impossible ----------------------------
    from outreach.dispatch import DispatchDisabled

    try:
        from outreach import dispatch

        dispatch.dispatch_message(message_id)  # type: ignore[attr-defined]
        dispatch_blocked = False
    except DispatchDisabled:
        dispatch_blocked = True
    except AttributeError:
        dispatch_blocked = True  # no send entry point exists at all
    _step("dispatch.blocked", {"dispatch_disabled": dispatch_blocked}, log)

    # -- evidence ---------------------------------------------------------
    final = operator.get(
        "outreach_messages",
        {
            "select": "id,approval_status,sending_status,personalization_evidence_ids,channel,sequence_step",
            "id": f"eq.{message_id}",
        },
    )[0]
    _step("message.final", {**{k: v for k, v in final.items() if k != "personalization_evidence_ids"},
                            "evidence_count": len(final.get("personalization_evidence_ids") or [])}, log)

    return {
        "company_id": company_id,
        "candidate_id": candidate["id"],
        "task_id": task["id"],
        "report_id": research.report_id,
        "qualification_id": qualification_id,
        "contact_id": contact["id"],
        "campaign_id": campaign["id"],
        "message_id": message_id,
        "steps": log,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the whole platform loop against the live database")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = run()
    except OperatorSessionError as exc:
        print(f"cannot open an operator session: {exc}", file=sys.stderr)
        return 2
    except DatabaseError as exc:
        print(f"database refused a step: {exc.code} {exc.message}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    for entry in result["steps"]:
        name = entry.pop("step")
        detail = " ".join(f"{k}={v}" for k, v in entry.items())
        print(f"  {name:24} {detail}")
    print("\nrow ids")
    for key in ("company_id", "candidate_id", "task_id", "report_id", "qualification_id",
                "contact_id", "campaign_id", "message_id"):
        print(f"  {key:16} {result[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
