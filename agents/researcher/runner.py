"""
Researcher Agent Execution Runner.

Executes the Researcher vertical slice workflow end-to-end:
1. Validates research payload strictly (summary, recommended_angle, confidence, findings, evidence URLs)
2. Authenticates as Researcher via Supabase Auth (scoped JWT, role=authenticated)
3. Verifies app_metadata claims (user_role=agent, subagent_type=researcher, agent_id)
4. Queries assigned active task from 'tasks' table under RLS
5. Reads assigned company under RLS
6. Transitions task from 'pending' to 'in_progress' only after preflight validation succeeds
7. Persists structured research_reports record
8. Persists atomic research_findings records (asserting all succeed)
9. Logs research completion in 'activities'
10. Transitions task to 'completed' (completed_at synchronized and verified)
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from .agent import (
    ResearcherAgent,
    VALID_CONFIDENCE_LEVELS,
    VALID_FINDING_CATEGORIES,
    VALID_FINDING_TYPES,
)
from .auth import SupabaseAuthClient
from .client import PostgrestClient
from .config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("researcher.runner")


def validate_research_payload(
    research_summary: Optional[str],
    recommended_angle: Optional[str],
    overall_confidence: str,
    findings: Optional[List[Dict[str, Any]]],
) -> None:
    """
    Preflight validation of research payload before any database mutations occur.
    Guarantees that an invalid payload will never cause a task to transition to in_progress.
    """
    if not research_summary or not str(research_summary).strip():
        raise ValueError("Research summary is required and cannot be empty.")
    if not recommended_angle or not str(recommended_angle).strip():
        raise ValueError("Recommended angle is required and cannot be empty.")
    if overall_confidence not in VALID_CONFIDENCE_LEVELS:
        raise ValueError(f"Invalid overall_confidence '{overall_confidence}'. Allowed: {VALID_CONFIDENCE_LEVELS}")
    if not findings or not isinstance(findings, list) or len(findings) == 0:
        raise ValueError("Findings must be a non-empty list of research finding objects.")

    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise ValueError(f"Finding [{i}] must be a dictionary.")

        statement = finding.get("statement")
        if not statement or not str(statement).strip():
            raise ValueError(f"Finding [{i}] missing required 'statement'.")

        f_type = finding.get("finding_type")
        if f_type not in VALID_FINDING_TYPES:
            raise ValueError(f"Finding [{i}] invalid finding_type '{f_type}'. Allowed: {VALID_FINDING_TYPES}")

        cat = finding.get("category")
        if cat not in VALID_FINDING_CATEGORIES:
            raise ValueError(f"Finding [{i}] invalid category '{cat}'. Allowed: {VALID_FINDING_CATEGORIES}")

        conf = finding.get("confidence", "unverified")
        if conf not in VALID_CONFIDENCE_LEVELS:
            raise ValueError(f"Finding [{i}] invalid confidence '{conf}'. Allowed: {VALID_CONFIDENCE_LEVELS}")

        url = finding.get("evidence_url")
        if f_type == "verified_fact":
            if not url or not str(url).strip():
                raise ValueError(f"Finding [{i}] of type 'verified_fact' strictly requires a non-empty 'evidence_url'.")
            if conf not in ("high", "medium"):
                raise ValueError(f"Finding [{i}] of type 'verified_fact' requires confidence 'high' or 'medium'.")


def run_researcher_vertical_slice(
    config: Config,
    task_id: Optional[str] = None,
    research_summary: Optional[str] = None,
    recommended_angle: Optional[str] = None,
    overall_confidence: str = "high",
    gaps_and_unknowns: Optional[str] = None,
    findings: Optional[List[Dict[str, Any]]] = None,
    proposed_contacts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    # 1. Preflight input validation BEFORE ANY database or task mutation!
    validate_research_payload(
        research_summary=research_summary,
        recommended_angle=recommended_angle,
        overall_confidence=overall_confidence,
        findings=findings,
    )
    submitted_findings_count = len(findings)  # type: ignore[arg-type]

    # 2. Authenticate with Supabase Auth using scoped credentials
    logger.info("Authenticating Researcher via Supabase GoTrue endpoint (%s)...", config.supabase_url)
    auth_client = SupabaseAuthClient(config.supabase_url, config.anon_key)
    auth_result = auth_client.sign_in_with_password(
        config.researcher_email,
        config.researcher_password,
    )
    claims = auth_result["claims"]

    # 3. Verify claims under Section 3
    auth_client.verify_researcher_claims(claims, config.researcher_agent_id)
    access_token = auth_result["access_token"]

    # 4. Initialize PostgREST client with authenticated agent JWT
    client = PostgrestClient(config.supabase_url, config.anon_key, access_token)
    agent = ResearcherAgent(client, config.researcher_agent_id)

    # 5. Acquire assigned active task
    task = agent.get_assigned_active_task(task_id)
    actual_task_id = task["id"]
    company_id = task["company_id"]
    logger.info("Acquired assigned task %s for company %s", actual_task_id, company_id)

    # 6. Read assigned company (RLS verifies active task scope)
    company = agent.read_assigned_company(company_id)
    domains = agent.read_company_domains(company_id)
    company_name = company.get("name", company_id)
    company_domain = company.get("primary_domain", "unknown")
    logger.info("Company verified: %s (Domain: %s)", company_name, company_domain)

    # 7. Transition task to 'in_progress' only after preflight validation succeeds
    if task["status"] == "pending":
        task = agent.transition_task_in_progress(actual_task_id)

    # 8. Persist structured research report
    report = agent.persist_research_report(
        company_id=company_id,
        summary=research_summary,  # type: ignore[arg-type]
        recommended_angle=recommended_angle,  # type: ignore[arg-type]
        overall_confidence=overall_confidence,
        gaps_and_unknowns=gaps_and_unknowns,
    )
    report_id = report.get("id")
    if not report_id:
        raise RuntimeError("Failed to persist research report: no report id returned")
    logger.info("Persisted research report: %s", report_id)

    # 9. Persist atomic research findings
    persisted_findings = agent.persist_research_findings(
        report_id=report_id,
        company_id=company_id,
        findings=findings,  # type: ignore[arg-type]
    )
    persisted_count = len(persisted_findings)
    logger.info("Persisted %d atomic research findings", persisted_count)

    # Assert persistence integrity
    if persisted_count != submitted_findings_count:
        raise RuntimeError(
            f"Finding count mismatch: submitted {submitted_findings_count}, but persisted {persisted_count}. "
            "Halting workflow before task completion."
        )

    # 10. Record completion activity
    agent.record_activity(
        company_id=company_id,
        subject=f"Company Research Completed: {company['name']}",
        description=f"Deep research synthesis completed with {persisted_count} findings.",
        metadata={
            "report_id": report_id,
            "task_id": actual_task_id,
            "findings_count": persisted_count,
        },
    )

    # 11. Complete task (triggers automatic completed_at synchronization)
    completed_task = agent.complete_task(actual_task_id)
    logger.info("Task %s completed at: %s", actual_task_id, completed_task.get("completed_at"))

    # Assemble Contract 3 compliant output payload
    return {
        "task_id": actual_task_id,
        "company_id": company_id,
        "research_report_id": report_id,
        "research_summary": research_summary,
        "recommended_angle": recommended_angle,
        "overall_confidence": overall_confidence,
        "gaps_and_unknowns": gaps_and_unknowns,
        "findings_count": persisted_count,
        "findings": persisted_findings,
        "proposed_contacts_for_operator_review": proposed_contacts or [],
        "completed_task": completed_task,
    }


def main():
    parser = argparse.ArgumentParser(description="Researcher Agent Vertical Slice Runner")
    parser.add_argument("--payload-file", required=True, help="Path to JSON file containing research payload")
    parser.add_argument("--task-id", help="Optional specific task UUID to acquire")
    args = parser.parse_args()

    config = Config.load()

    with open(args.payload_file, "r", encoding="utf-8") as f:
        payload = json.load(f)

    result = run_researcher_vertical_slice(
        config=config,
        task_id=args.task_id or payload.get("task_id"),
        research_summary=payload.get("research_summary"),
        recommended_angle=payload.get("recommended_angle"),
        overall_confidence=payload.get("overall_confidence", "high"),
        gaps_and_unknowns=payload.get("gaps_and_unknowns"),
        findings=payload.get("findings"),
        proposed_contacts=payload.get("proposed_contacts_for_operator_review"),
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
