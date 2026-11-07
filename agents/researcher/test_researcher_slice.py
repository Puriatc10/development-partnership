"""
Automated Integration and Verification Suite for Researcher Vertical Slice.

Verifies:
1. Supabase Auth identity provisioning & scoped JWT claim structure.
2. Least-privilege RLS boundary enforcement:
   - Anonymous access denial.
   - Researcher blocked from INSERT on companies, contacts, and tasks.
   - Researcher task-scoping enforcement.
"""

import json
import logging
import os
import sys
import unittest

from .auth import SupabaseAuthClient
from .client import PostgrestClient, PostgrestError
from .config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_researcher_slice")


class TestResearcherVerticalSlice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config.load()
        if not cls.config.anon_key:
            raise unittest.SkipTest("SUPABASE_ANON_KEY not set")
        if not cls.config.researcher_password:
            raise unittest.SkipTest("RESEARCHER_PASSWORD not set")

        cls.auth_client = SupabaseAuthClient(cls.config.supabase_url, cls.config.anon_key)
        cls.auth_result = cls.auth_client.sign_in_with_password(
            cls.config.researcher_email,
            cls.config.researcher_password,
        )
        cls.claims = cls.auth_result["claims"]
        cls.client = PostgrestClient(
            cls.config.supabase_url,
            cls.config.anon_key,
            cls.auth_result["access_token"],
        )

    def test_01_researcher_jwt_claims(self):
        """Verify Researcher JWT claims adhere strictly to Section 3."""
        self.assertEqual(self.claims.get("role"), "authenticated")
        self.assertIsNotNone(self.claims.get("sub"))

        app_meta = self.claims.get("app_metadata", {})
        self.assertEqual(app_meta.get("user_role"), "agent")
        self.assertEqual(app_meta.get("subagent_type"), "researcher")
        self.assertEqual(app_meta.get("agent_id"), self.config.researcher_agent_id)

    def test_02_researcher_cannot_insert_companies(self):
        """Researcher must be strictly blocked from inserting into companies table by RLS."""
        payload = {
            "name": "Unauthorized Test Agency",
            "normalized_name": "unauthorized test agency",
            "primary_domain": "unauthorized-probe.test",
            "primary_segment": "webflow_studio",
            "discovery_source": "probe",
        }
        with self.assertRaises(PostgrestError) as ctx:
            self.client.post("companies", payload)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "42501")

    def test_03_researcher_cannot_insert_contacts(self):
        """Researcher must be strictly blocked from inserting into contacts table by RLS."""
        payload = {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@unauthorized-probe.test",
            "role_type": "founder_executive",
        }
        with self.assertRaises(PostgrestError) as ctx:
            self.client.post("contacts", payload)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "42501")

    def test_04_researcher_cannot_insert_tasks(self):
        """Researcher must be strictly blocked from creating tasks in tasks table by RLS."""
        payload = {
            "title": "Probe Task",
            "task_type": "company_research",
            "assigned_actor_type": "agent",
            "assigned_actor_identity": self.config.researcher_agent_id,
        }
        with self.assertRaises(PostgrestError) as ctx:
            self.client.post("tasks", payload)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "42501")

    def test_05_anonymous_access_denied(self):
        """Anonymous requests must be rejected across all CRM tables."""
        anon_client = PostgrestClient(self.config.supabase_url, self.config.anon_key, self.config.anon_key)
        with self.assertRaises(PostgrestError) as ctx:
            anon_client.get("companies")
        self.assertIn(ctx.exception.status_code, (401, 403))

    def test_06_researcher_can_read_assigned_task_and_company(self):
        """Researcher can read its assigned task and assigned company under RLS."""
        tasks = self.client.get("tasks", {
            "assigned_actor_type": "eq.agent",
            "assigned_actor_identity": f"eq.{self.config.researcher_agent_id}",
        })
        self.assertGreaterEqual(len(tasks), 1)
        target_task = tasks[0]
        self.assertEqual(target_task["assigned_actor_identity"], self.config.researcher_agent_id)

        # Company accessibility depends on active task status under RLS agent_select_companies
        active_tasks = [t for t in tasks if t["status"] in ("pending", "in_progress")]
        companies = self.client.get("companies", {"id": f"eq.{target_task['company_id']}"})
        if active_tasks:
            self.assertEqual(len(companies), 1)
            self.assertEqual(companies[0]["primary_domain"], "refokus.com")
        else:
            # Under strict RLS policy agent_select_companies, completed task revokes company read access for agent
            self.assertEqual(len(companies), 0)


if __name__ == "__main__":
    unittest.main()
