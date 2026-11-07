import logging
from typing import Any, Dict, List, Optional
from .client import PostgrestClient

logger = logging.getLogger("researcher.agent")

VALID_FINDING_TYPES = {"verified_fact", "hypothesis", "information_gap_tbd"}
VALID_FINDING_CATEGORIES = {
    "business_model",
    "client_roster",
    "technology_stack",
    "scoping_maturity",
    "growth_signals",
    "pain_points",
    "pricing_rates",
    "team_structure",
    "other",
}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low", "unverified"}


class ResearcherAgent:
    """
    Evidence-based Research Agent adhering to:
    - Agent Operating Model v2.0.0 (Section 2.3)
    - Standard Agent Contract 3 (hermes-researcher)
    - Workflow 2 (Comprehensive Company Research)
    - Task Execution Protocol v2.0.0
    """

    def __init__(self, client: PostgrestClient, agent_id: str = "hermes-researcher-v1"):
        self.client = client
        self.agent_id = agent_id

    def get_assigned_active_task(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieves the assigned research task from the tasks table.
        Filtered strictly to tasks assigned to this agent with pending or in_progress status.
        """
        params = {
            "assigned_actor_type": "eq.agent",
            "assigned_actor_identity": f"eq.{self.agent_id}",
            "task_type": "eq.company_research",
            "status": "in.(pending,in_progress)",
        }
        if task_id:
            params["id"] = f"eq.{task_id}"

        tasks = self.client.get("tasks", params=params)
        if not tasks:
            raise LookupError(
                f"No active 'company_research' task found assigned to '{self.agent_id}'"
                + (f" with id '{task_id}'" if task_id else "")
            )
        return tasks[0]

    def transition_task_in_progress(self, task_id: str) -> Dict[str, Any]:
        """Transitions task from 'pending' to 'in_progress'."""
        logger.info("Transitioning task %s to 'in_progress'...", task_id)
        res = self.client.patch("tasks", {"status": "in_progress"}, params={"id": f"eq.{task_id}"})
        if not res:
            raise RuntimeError(f"Failed to update task {task_id} to in_progress")
        return res[0]

    def read_assigned_company(self, company_id: str) -> Dict[str, Any]:
        """
        Reads the assigned company record within active task scope.
        RLS policy 'agent_select_companies' grants SELECT while task is active.
        """
        logger.info("Reading assigned company %s...", company_id)
        companies = self.client.get("companies", params={"id": f"eq.{company_id}"})
        if not companies:
            raise LookupError(f"Assigned company {company_id} not visible or not accessible under RLS")
        return companies[0]

    def read_company_domains(self, company_id: str) -> List[Dict[str, Any]]:
        """Reads registered domains for the assigned company."""
        return self.client.get("company_domains", params={"company_id": f"eq.{company_id}"})

    def persist_research_report(
        self,
        company_id: str,
        summary: str,
        recommended_angle: str,
        overall_confidence: str = "medium",
        gaps_and_unknowns: Optional[str] = None,
        report_version: int = 1,
    ) -> Dict[str, Any]:
        """
        Inserts one structured research_reports record under RLS policy 'agent_insert_research_reports'.
        """
        if overall_confidence not in VALID_CONFIDENCE_LEVELS:
            raise ValueError(f"Invalid overall_confidence '{overall_confidence}'. Allowed: {VALID_CONFIDENCE_LEVELS}")

        report_payload = {
            "company_id": company_id,
            "research_agent_identity": self.agent_id,
            "report_version": report_version,
            "summary": summary,
            "recommended_angle": recommended_angle,
            "overall_confidence": overall_confidence,
            "gaps_and_unknowns": gaps_and_unknowns,
        }

        logger.info("Persisting research report for company %s...", company_id)
        res = self.client.post("research_reports", report_payload)
        if not res:
            raise RuntimeError("Failed to persist research report")
        return res[0]

    def persist_research_findings(
        self,
        report_id: str,
        company_id: str,
        findings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Inserts atomic research_findings records under RLS policy 'agent_insert_research_findings'.
        Validates enums, constraints, and evidence requirements before submission.
        """
        prepared_rows = []
        for i, finding in enumerate(findings):
            f_type = finding.get("finding_type")
            cat = finding.get("category")
            conf = finding.get("confidence", "unverified")
            statement = finding.get("statement")
            url = finding.get("evidence_url")
            excerpt = finding.get("evidence_excerpt")

            if f_type not in VALID_FINDING_TYPES:
                raise ValueError(f"Finding [{i}] invalid finding_type '{f_type}'. Allowed: {VALID_FINDING_TYPES}")
            if cat not in VALID_FINDING_CATEGORIES:
                raise ValueError(f"Finding [{i}] invalid category '{cat}'. Allowed: {VALID_FINDING_CATEGORIES}")
            if conf not in VALID_CONFIDENCE_LEVELS:
                raise ValueError(f"Finding [{i}] invalid confidence '{conf}'. Allowed: {VALID_CONFIDENCE_LEVELS}")
            if not statement:
                raise ValueError(f"Finding [{i}] missing statement")

            # Check constraint chk_verified_fact_requires_evidence
            if f_type == "verified_fact":
                if not url:
                    raise ValueError(f"Finding [{i}] of type 'verified_fact' strictly requires an 'evidence_url'")
                if conf not in ("high", "medium"):
                    raise ValueError(f"Finding [{i}] of type 'verified_fact' requires confidence 'high' or 'medium'")

            prepared_rows.append({
                "report_id": report_id,
                "company_id": company_id,
                "finding_type": f_type,
                "category": cat,
                "statement": statement,
                "evidence_url": url,
                "evidence_excerpt": excerpt,
                "confidence": conf,
            })

        logger.info("Persisting %d research findings for report %s...", len(prepared_rows), report_id)
        persisted = []
        for row in prepared_rows:
            res = self.client.post("research_findings", row)
            if res:
                persisted.append(res[0])
        return persisted

    def record_activity(
        self,
        company_id: str,
        subject: str,
        description: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Logs research completion activity under RLS policy 'agent_insert_activities'.
        """
        payload = {
            "company_id": company_id,
            "activity_type": "note",
            "subject": subject,
            "description": description,
            "performed_by_actor_type": "agent",
            "performed_by_actor_identity": self.agent_id,
            "metadata": metadata or {},
        }
        res = self.client.post("activities", payload)
        return res[0] if res else {}

    def complete_task(self, task_id: str) -> Dict[str, Any]:
        """
        Transitions task from 'in_progress' to 'completed'.
        Database trigger 'fn_enforce_task_governance' will synchronize completed_at timestamp.
        """
        logger.info("Completing task %s...", task_id)
        res = self.client.patch("tasks", {"status": "completed"}, params={"id": f"eq.{task_id}"})
        if not res:
            raise RuntimeError(f"Failed to complete task {task_id}")
        return res[0]
