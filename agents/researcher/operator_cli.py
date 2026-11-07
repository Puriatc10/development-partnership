"""
Operator CLI Tool for Authenticated Human Operator Sessions.

Allows a human operator to authenticate via Supabase Auth and execute:
- Company registration (Workflow 1 & Human Gate 1)
- Task creation & assignment to Hermes Researcher (Workflow 2)
- Inspection of completed research deliverables
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

from .auth import SupabaseAuthClient
from .client import PostgrestClient
from .config import Config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("operator.cli")


def get_operator_client(
    supabase_url: str,
    anon_key: str,
    email: str,
    password: str,
) -> PostgrestClient:
    auth_client = SupabaseAuthClient(supabase_url, anon_key)
    auth_result = auth_client.sign_in_with_password(email, password)
    claims = auth_result["claims"]

    app_meta = claims.get("app_metadata", {})
    user_role = app_meta.get("user_role")
    if user_role not in ("operator", "admin", "founder"):
        raise PermissionError(
            f"User '{email}' does not carry human operator claims (app_metadata.user_role='{user_role}')"
        )

    logger.info("Human Operator authenticated: %s (UUID: %s, role: %s)", email, claims.get("sub"), user_role)
    return PostgrestClient(supabase_url, anon_key, auth_result["access_token"])


def register_company(
    client: PostgrestClient,
    name: str,
    primary_domain: str,
    website_url: Optional[str] = None,
    segment: str = "webflow_studio",
    discovery_source: str = "operator_registration",
) -> Dict[str, Any]:
    norm_name = name.strip().lower()
    norm_domain = primary_domain.strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if not website_url:
        website_url = f"https://{norm_domain}"

    payload = {
        "name": name.strip(),
        "normalized_name": norm_name,
        "primary_domain": norm_domain,
        "website_url": website_url,
        "primary_segment": segment,
        "discovery_source": discovery_source,
        "lifecycle_stage": "identified",
    }

    res = client.post("companies", payload)
    if not res:
        raise RuntimeError("Failed to register company")
    company = res[0]
    logger.info("Registered company '%s' (UUID: %s)", company["name"], company["id"])
    return company


def create_research_task(
    client: PostgrestClient,
    company_id: str,
    agent_id: str = "hermes-researcher-v1",
    priority: str = "medium",
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    if not title:
        title = f"Deep Company Research: {company_id}"
    if not description:
        description = "Conduct evidence-based research on technical stack, client portfolio, and decision-makers."

    payload = {
        "company_id": company_id,
        "title": title,
        "description": description,
        "task_type": "company_research",
        "priority": priority,
        "status": "pending",
        "assigned_actor_type": "agent",
        "assigned_actor_identity": agent_id,
    }

    res = client.post("tasks", payload)
    if not res:
        raise RuntimeError("Failed to create task")
    task = res[0]
    logger.info("Created research task (UUID: %s) assigned to '%s'", task["id"], agent_id)
    return task


def inspect_results(client: PostgrestClient, company_id: str) -> None:
    companies = client.get("companies", {"id": f"eq.{company_id}"})
    if not companies:
        print(f"Company {company_id} not found.")
        return
    company = companies[0]

    tasks = client.get("tasks", {"company_id": f"eq.{company_id}", "order": "created_at.desc"})
    reports = client.get("research_reports", {"company_id": f"eq.{company_id}", "order": "created_at.desc"})
    findings = client.get("research_findings", {"company_id": f"eq.{company_id}", "order": "created_at.asc"})
    activities = client.get("activities", {"company_id": f"eq.{company_id}", "order": "created_at.asc"})

    print("==================================================")
    print(f"COMPANY: {company['name']} ({company['id']})")
    print(f"Domain: {company['primary_domain']} | Segment: {company['primary_segment']} | Stage: {company['lifecycle_stage']}")
    print("==================================================")

    print(f"\n--- TASKS ({len(tasks)}) ---")
    for t in tasks:
        print(f"  [{t['status'].upper()}] Task: {t['title']} | ID: {t['id']} | Completed At: {t.get('completed_at')}")

    print(f"\n--- RESEARCH REPORTS ({len(reports)}) ---")
    for r in reports:
        print(f"  Report ID: {r['id']} | Agent: {r['research_agent_identity']} | Confidence: {r['overall_confidence']}")
        print(f"  Summary: {r['summary']}")
        print(f"  Recommended Angle: {r.get('recommended_angle')}")
        print(f"  Gaps & Unknowns (TBD): {r.get('gaps_and_unknowns')}")

    print(f"\n--- ATOMIC FINDINGS ({len(findings)}) ---")
    for i, f in enumerate(findings, 1):
        print(f"  {i}. [{f['finding_type'].upper()}] [{f['category']}] ({f['confidence']} confidence)")
        print(f"     Statement: {f['statement']}")
        if f.get("evidence_url"):
            print(f"     Evidence URL: {f['evidence_url']}")
        if f.get("evidence_excerpt"):
            print(f"     Excerpt: \"{f['evidence_excerpt']}\"")

    print(f"\n--- ACTIVITIES ({len(activities)}) ---")
    for a in activities:
        print(f"  [{a['activity_type']}] {a['subject']} by {a['performed_by_actor_type']}:{a['performed_by_actor_identity']}")
    print("==================================================")


def main():
    parser = argparse.ArgumentParser(description="Human Operator CLI for Supabase CRM")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # register-company
    p_reg = subparsers.add_parser("register-company")
    p_reg.add_argument("--name", required=True)
    p_reg.add_argument("--domain", required=True)
    p_reg.add_argument("--website")
    p_reg.add_argument("--segment", default="webflow_studio", choices=["digital_agency", "webflow_studio", "framer_studio"])

    # create-task
    p_task = subparsers.add_parser("create-task")
    p_task.add_argument("--company-id", required=True)
    p_task.add_argument("--agent-id", default="hermes-researcher-v1")
    p_task.add_argument("--priority", default="medium", choices=["low", "medium", "high", "urgent"])
    p_task.add_argument("--title")
    p_task.add_argument("--description")

    # inspect
    p_insp = subparsers.add_parser("inspect")
    p_insp.add_argument("--company-id", required=True)

    # list-companies
    subparsers.add_parser("list-companies")

    # Common auth options
    for p in (p_reg, p_task, p_insp, subparsers.choices["list-companies"]):
        p.add_argument("--email", default=None)
        p.add_argument("--password", default=None)

    args = parser.parse_args()
    config = Config.load()

    email = args.email or os.environ.get("OPERATOR_EMAIL")
    password = args.password or os.environ.get("OPERATOR_PASSWORD")

    if not email or not password:
        logger.error("Operator credentials (--email and --password or OPERATOR_EMAIL/OPERATOR_PASSWORD) required")
        sys.exit(1)

    client = get_operator_client(config.supabase_url, config.anon_key, email, password)

    if args.subcommand == "register-company":
        company = register_company(client, args.name, args.domain, args.website, args.segment)
        print(json.dumps(company, indent=2))
    elif args.subcommand == "create-task":
        task = create_research_task(client, args.company_id, args.agent_id, args.priority, args.title, args.description)
        print(json.dumps(task, indent=2))
    elif args.subcommand == "inspect":
        inspect_results(client, args.company_id)
    elif args.subcommand == "list-companies":
        comps = client.get("companies", {"order": "created_at.desc"})
        print(json.dumps(comps, indent=2))


if __name__ == "__main__":
    main()
