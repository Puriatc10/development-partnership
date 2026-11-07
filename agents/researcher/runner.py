"""
Researcher Agent Execution Runner.

Executes the Researcher vertical slice workflow end-to-end:
1. Authenticates as Researcher via Supabase Auth (scoped JWT, role=authenticated)
2. Verifies app_metadata claims (user_role=agent, subagent_type=researcher, agent_id)
3. Queries assigned active task from 'tasks' table under RLS
4. Reads assigned company under RLS
5. Transitions task to 'in_progress'
6. Persists structured research_reports record
7. Persists atomic research_findings records
8. Logs research completion in 'activities'
9. Transitions task to 'completed' (completed_at synchronized by database trigger)
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from .agent import ResearcherAgent
from .auth import SupabaseAuthClient
from .client import PostgrestClient
from .config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("researcher.runner")


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
    # 1. Authenticate with Supabase Auth using scoped credentials
    logger.info("Authenticating Researcher via Supabase GoTrue endpoint (%s)...", config.supabase_url)
    auth_client = SupabaseAuthClient(config.supabase_url, config.anon_key)
    auth_result = auth_client.sign_in_with_password(
        config.researcher_email,
        config.researcher_password,
    )
    claims = auth_result["claims"]

    # 2. Verify claims under Section 3
    auth_client.verify_researcher_claims(claims, config.researcher_agent_id)
    access_token = auth_result["access_token"]

    # 3. Initialize PostgREST client with authenticated agent JWT
    client = PostgrestClient(config.supabase_url, config.anon_key, access_token)
    agent = ResearcherAgent(client, config.researcher_agent_id)

    # 4. Acquire assigned active task
    task = agent.get_assigned_active_task(task_id)
    actual_task_id = task["id"]
    company_id = task["company_id"]
    logger.info("Acquired assigned task %s for company %s", actual_task_id, company_id)

    # 5. Read assigned company (RLS verifies active task scope)
    company = agent.read_assigned_company(company_id)
    domains = agent.read_company_domains(company_id)
    logger.info("Company verified: %s (Domain: %s)", company["name"], company["primary_domain"])

    # 6. Transition task to 'in_progress'
    if task["status"] == "pending":
        task = agent.transition_task_in_progress(actual_task_id)

    # If no custom findings provided, validate that findings are supplied
    if not findings:
        raise ValueError("Cannot persist research report without evidence-based research findings.")
    if not research_summary:
        raise ValueError("Research summary is required.")
    if not recommended_angle:
        raise ValueError("Recommended angle is required.")

    # 7. Persist structured research report
    report = agent.persist_research_report(
        company_id=company_id,
        summary=research_summary,
        recommended_angle=recommended_angle,
        overall_confidence=overall_confidence,
        gaps_and_unknowns=gaps_and_unknowns,
    )
    report_id = report["id"]
    logger.info("Persisted research report: %s", report_id)

    # 8. Persist atomic research findings
    persisted_findings = agent.persist_research_findings(
        report_id=report_id,
        company_id=company_id,
        findings=findings,
    )
    logger.info("Persisted %d atomic research findings", len(persisted_findings))

    # 9. Record completion activity
    agent.record_activity(
        company_id=company_id,
        subject=f"Company Research Completed: {company['name']}",
        description=f"Deep research synthesis completed with {len(persisted_findings)} findings.",
        metadata={
            "report_id": report_id,
            "task_id": actual_task_id,
            "findings_count": len(persisted_findings),
        },
    )

    # 10. Complete task (triggers automatic completed_at synchronization)
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
        "findings_count": len(persisted_findings),
        "findings": persisted_findings,
        "proposed_contacts_for_operator_review": proposed_contacts or [],
        "completed_task": completed_task,
    }
