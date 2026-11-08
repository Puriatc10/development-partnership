"""
Unit Tests for Researcher Runner Input Validation & Integrity Controls.

Verifies:
1. Missing findings are rejected before task transition.
2. Missing summary is rejected before task transition.
3. Missing recommended angle is rejected before task transition.
4. Invalid finding data is rejected before task transition.
5. A failed finding insert prevents task completion.
6. A finding-count mismatch prevents task completion.
7. Failed activity logging prevents task completion.
8. Successful execution follows the correct order.
9. Unauthorized project ref is rejected by fail-closed Development environment guard.
10. Unauthorized Supabase URL is rejected by fail-closed Development environment guard.
11. Missing project ref or URL is rejected by fail-closed Development environment guard.
12. Missing expected company ID is rejected before authentication or task mutation.
13. Mismatched company ID is rejected before ANY task mutation or database write.
14. Malformed URL containing credentials is rejected and credentials are not leaked in the error.
"""

import unittest
from unittest.mock import MagicMock, call, patch

from .agent import ResearcherAgent
from .config import Config
from .runner import run_researcher_vertical_slice, validate_research_payload


class TestRunnerValidationAndIntegrity(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            project_ref="vyfqmrdznmocnmzyjjcj",
            supabase_url="https://vyfqmrdznmocnmzyjjcj.supabase.co",
            anon_key="mock_anon_key",
            researcher_email="researcher.v1@agents.internal",
            researcher_password="mock_password",
            researcher_agent_id="hermes-researcher-v1",
        )
        self.valid_findings = [
            {
                "finding_type": "verified_fact",
                "category": "technology_stack",
                "statement": "Refokus specializes in Webflow and custom tools.",
                "evidence_url": "https://refokus.com/about",
                "evidence_excerpt": "We are a Webflow enterprise partner.",
                "confidence": "high",
            },
            {
                "finding_type": "hypothesis",
                "category": "pain_points",
                "statement": "May experience backend development bottlenecks.",
                "confidence": "medium",
            },
            {
                "finding_type": "information_gap_tbd",
                "category": "pricing_rates",
                "statement": "Client project minimum budget is unstated (TBD).",
                "confidence": "unverified",
            },
        ]

    def test_01_missing_findings_rejected_before_transition(self):
        """Missing or empty findings must raise ValueError before any network or task call."""
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=self.config,
                    research_summary="Valid summary",
                    recommended_angle="Valid angle",
                    findings=[],
                )
            self.assertIn("non-empty list", str(ctx.exception))
            mock_auth.assert_not_called()

    def test_02_missing_summary_rejected_before_transition(self):
        """Missing summary must raise ValueError before any network or task call."""
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=self.config,
                    research_summary="",
                    recommended_angle="Valid angle",
                    findings=self.valid_findings,
                )
            self.assertIn("Research summary is required", str(ctx.exception))
            mock_auth.assert_not_called()

    def test_03_missing_recommended_angle_rejected_before_transition(self):
        """Missing recommended angle must raise ValueError before any network or task call."""
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=self.config,
                    research_summary="Valid summary",
                    recommended_angle=None,
                    findings=self.valid_findings,
                )
            self.assertIn("Recommended angle is required", str(ctx.exception))
            mock_auth.assert_not_called()

    def test_04_invalid_finding_data_rejected_before_transition(self):
        """Verified fact without evidence_url must be rejected during preflight."""
        invalid_findings = [
            {
                "finding_type": "verified_fact",
                "category": "technology_stack",
                "statement": "Missing evidence URL",
                "confidence": "high",
                # evidence_url missing!
            }
        ]
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=self.config,
                    research_summary="Valid summary",
                    recommended_angle="Valid angle",
                    findings=invalid_findings,
                )
            self.assertIn("strictly requires a non-empty 'evidence_url'", str(ctx.exception))
            mock_auth.assert_not_called()

    @patch("agents.researcher.runner.ResearcherAgent")
    @patch("agents.researcher.runner.PostgrestClient")
    @patch("agents.researcher.runner.SupabaseAuthClient")
    def test_05_failed_finding_insert_prevents_task_completion(self, mock_auth_cls, mock_client_cls, mock_agent_cls):
        """A failure during findings persistence must halt execution and NOT complete the task."""
        mock_auth = mock_auth_cls.return_value
        mock_auth.sign_in_with_password.return_value = {
            "access_token": "mock_jwt",
            "claims": {"sub": "mock_sub"},
        }

        mock_agent = mock_agent_cls.return_value
        mock_agent.get_assigned_active_task.return_value = {
            "id": "task-uuid-1",
            "company_id": "comp-uuid-1",
            "status": "pending",
        }
        mock_agent.read_assigned_company.return_value = {
            "id": "comp-uuid-1",
            "name": "Refokus",
            "primary_domain": "refokus.com",
        }
        mock_agent.read_company_domains.return_value = []
        mock_agent.transition_task_in_progress.return_value = {"id": "task-uuid-1", "status": "in_progress"}
        mock_agent.persist_research_report.return_value = {"id": "report-uuid-1"}
        mock_agent.persist_research_findings.side_effect = RuntimeError("PostgREST 500 DB Error on finding")

        with self.assertRaises(RuntimeError) as ctx:
            run_researcher_vertical_slice(
                config=self.config,
                expected_company_id="comp-uuid-1",
                research_summary="Valid summary",
                recommended_angle="Valid angle",
                findings=self.valid_findings,
            )
        self.assertIn("DB Error on finding", str(ctx.exception))
        mock_agent.complete_task.assert_not_called()

    @patch("agents.researcher.runner.ResearcherAgent")
    @patch("agents.researcher.runner.PostgrestClient")
    @patch("agents.researcher.runner.SupabaseAuthClient")
    def test_06_finding_count_mismatch_prevents_task_completion(self, mock_auth_cls, mock_client_cls, mock_agent_cls):
        """If persisted findings count != submitted count, task must NOT complete."""
        mock_auth = mock_auth_cls.return_value
        mock_auth.sign_in_with_password.return_value = {
            "access_token": "mock_jwt",
            "claims": {"sub": "mock_sub"},
        }

        mock_agent = mock_agent_cls.return_value
        mock_agent.get_assigned_active_task.return_value = {
            "id": "task-uuid-1",
            "company_id": "comp-uuid-1",
            "status": "pending",
        }
        mock_agent.read_assigned_company.return_value = {
            "id": "comp-uuid-1",
            "name": "Refokus",
            "primary_domain": "refokus.com",
        }
        mock_agent.read_company_domains.return_value = []
        mock_agent.transition_task_in_progress.return_value = {"id": "task-uuid-1", "status": "in_progress"}
        mock_agent.persist_research_report.return_value = {"id": "report-uuid-1"}
        # Return only 2 out of 3 findings
        mock_agent.persist_research_findings.return_value = [{"id": "f1"}, {"id": "f2"}]

        with self.assertRaises(RuntimeError) as ctx:
            run_researcher_vertical_slice(
                config=self.config,
                expected_company_id="comp-uuid-1",
                research_summary="Valid summary",
                recommended_angle="Valid angle",
                findings=self.valid_findings,
            )
        self.assertIn("Finding count mismatch", str(ctx.exception))
        mock_agent.complete_task.assert_not_called()

    @patch("agents.researcher.runner.ResearcherAgent")
    @patch("agents.researcher.runner.PostgrestClient")
    @patch("agents.researcher.runner.SupabaseAuthClient")
    def test_07_failed_activity_logging_prevents_task_completion(self, mock_auth_cls, mock_client_cls, mock_agent_cls):
        """Failed activity logging must raise error and prevent task completion."""
        mock_auth = mock_auth_cls.return_value
        mock_auth.sign_in_with_password.return_value = {
            "access_token": "mock_jwt",
            "claims": {"sub": "mock_sub"},
        }

        mock_agent = mock_agent_cls.return_value
        mock_agent.get_assigned_active_task.return_value = {
            "id": "task-uuid-1",
            "company_id": "comp-uuid-1",
            "status": "pending",
        }
        mock_agent.read_assigned_company.return_value = {
            "id": "comp-uuid-1",
            "name": "Refokus",
            "primary_domain": "refokus.com",
        }
        mock_agent.read_company_domains.return_value = []
        mock_agent.transition_task_in_progress.return_value = {"id": "task-uuid-1", "status": "in_progress"}
        mock_agent.persist_research_report.return_value = {"id": "report-uuid-1"}
        mock_agent.persist_research_findings.return_value = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]
        mock_agent.record_activity.side_effect = RuntimeError("Failed to record activity")

        with self.assertRaises(RuntimeError) as ctx:
            run_researcher_vertical_slice(
                config=self.config,
                expected_company_id="comp-uuid-1",
                research_summary="Valid summary",
                recommended_angle="Valid angle",
                findings=self.valid_findings,
            )
        self.assertIn("Failed to record activity", str(ctx.exception))
        mock_agent.complete_task.assert_not_called()

    @patch("agents.researcher.runner.ResearcherAgent")
    @patch("agents.researcher.runner.PostgrestClient")
    @patch("agents.researcher.runner.SupabaseAuthClient")
    def test_08_successful_execution_order(self, mock_auth_cls, mock_client_cls, mock_agent_cls):
        """Execution must strictly follow: read -> transition to in_progress -> report -> findings -> activity -> complete."""
        mock_auth = mock_auth_cls.return_value
        mock_auth.sign_in_with_password.return_value = {
            "access_token": "mock_jwt",
            "claims": {"sub": "mock_sub"},
        }

        mock_agent = mock_agent_cls.return_value
        mock_agent.get_assigned_active_task.return_value = {
            "id": "task-uuid-1",
            "company_id": "comp-uuid-1",
            "status": "pending",
        }
        mock_agent.read_assigned_company.return_value = {"id": "comp-uuid-1", "name": "Refokus", "primary_domain": "refokus.com"}
        mock_agent.read_company_domains.return_value = []
        mock_agent.transition_task_in_progress.return_value = {"id": "task-uuid-1", "status": "in_progress"}
        mock_agent.persist_research_report.return_value = {"id": "report-uuid-1"}
        mock_agent.persist_research_findings.return_value = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]
        mock_agent.record_activity.return_value = {"id": "act-uuid-1"}
        mock_agent.complete_task.return_value = {"id": "task-uuid-1", "status": "completed", "completed_at": "2026-11-07T20:00:00Z"}

        res = run_researcher_vertical_slice(
            config=self.config,
            task_id="task-uuid-1",
            expected_company_id="comp-uuid-1",
            research_summary="Valid summary",
            recommended_angle="Valid angle",
            overall_confidence="high",
            findings=self.valid_findings,
        )

        self.assertEqual(res["task_id"], "task-uuid-1")
        self.assertEqual(res["company_id"], "comp-uuid-1")
        self.assertEqual(res["findings_count"], 3)
        self.assertEqual(res["completed_task"]["status"], "completed")

        # Verify call ordering
        mock_agent.assert_has_calls([
            call.get_assigned_active_task("task-uuid-1"),
            call.read_assigned_company("comp-uuid-1"),
            call.read_company_domains("comp-uuid-1"),
            call.transition_task_in_progress("task-uuid-1"),
            call.persist_research_report(
                company_id="comp-uuid-1",
                summary="Valid summary",
                recommended_angle="Valid angle",
                overall_confidence="high",
                gaps_and_unknowns=None,
            ),
            call.persist_research_findings(
                report_id="report-uuid-1",
                company_id="comp-uuid-1",
                findings=self.valid_findings,
            ),
            call.record_activity(
                company_id="comp-uuid-1",
                subject="Company Research Completed: Refokus",
                description="Deep research synthesis completed with 3 findings.",
                metadata={"report_id": "report-uuid-1", "task_id": "task-uuid-1", "findings_count": 3},
            ),
            call.complete_task("task-uuid-1"),
        ])

    def test_09_wrong_project_ref_rejected_before_any_mutation(self):
        """Runner must reject unexpected/unauthorized project refs before any network or mutation."""
        bad_config = Config(
            project_ref="vqnamszywsuqjxuqseas",  # Production ref!
            supabase_url="https://vyfqmrdznmocnmzyjjcj.supabase.co",
            anon_key="mock_key",
            researcher_email="researcher.v1@agents.internal",
            researcher_password="mock_pw",
            researcher_agent_id="hermes-researcher-v1",
        )
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=bad_config,
                    expected_company_id="comp-uuid-1",
                    research_summary="Valid summary",
                    recommended_angle="Valid angle",
                    findings=self.valid_findings,
                )
            self.assertIn("Unauthorized project ref", str(ctx.exception))
            mock_auth.assert_not_called()

    def test_10_wrong_supabase_url_rejected_before_any_mutation(self):
        """Runner must reject unexpected/unauthorized Supabase URLs before any network or mutation."""
        bad_config = Config(
            project_ref="vyfqmrdznmocnmzyjjcj",
            supabase_url="https://vqnamszywsuqjxuqseas.supabase.co",  # Production URL!
            anon_key="mock_key",
            researcher_email="researcher.v1@agents.internal",
            researcher_password="mock_pw",
            researcher_agent_id="hermes-researcher-v1",
        )
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=bad_config,
                    expected_company_id="comp-uuid-1",
                    research_summary="Valid summary",
                    recommended_angle="Valid angle",
                    findings=self.valid_findings,
                )
            self.assertIn("Supabase URL does not match the approved Development endpoint", str(ctx.exception))
            mock_auth.assert_not_called()

    def test_11_missing_project_ref_or_url_rejected_before_mutation(self):
        """Missing project_ref or supabase_url must fail closed immediately."""
        bad_config1 = Config(
            project_ref="",
            supabase_url="https://vyfqmrdznmocnmzyjjcj.supabase.co",
            anon_key="mock_key",
            researcher_email="researcher.v1@agents.internal",
            researcher_password="mock_pw",
            researcher_agent_id="hermes-researcher-v1",
        )
        with self.assertRaises(ValueError) as ctx:
            run_researcher_vertical_slice(
                config=bad_config1,
                expected_company_id="comp-uuid-1",
                research_summary="Valid summary",
                recommended_angle="Valid angle",
                findings=self.valid_findings,
            )
        self.assertIn("SUPABASE_PROJECT_REF is missing", str(ctx.exception))

        bad_config2 = Config(
            project_ref="vyfqmrdznmocnmzyjjcj",
            supabase_url="",
            anon_key="mock_key",
            researcher_email="researcher.v1@agents.internal",
            researcher_password="mock_pw",
            researcher_agent_id="hermes-researcher-v1",
        )
        with self.assertRaises(ValueError) as ctx:
            run_researcher_vertical_slice(
                config=bad_config2,
                expected_company_id="comp-uuid-1",
                research_summary="Valid summary",
                recommended_angle="Valid angle",
                findings=self.valid_findings,
            )
        self.assertIn("SUPABASE_URL is missing", str(ctx.exception))

    def test_12_missing_expected_company_id_rejected_before_mutation(self):
        """Missing expected_company_id must raise ValueError before any auth or task mutation."""
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=self.config,
                    expected_company_id=None,
                    research_summary="Valid summary",
                    recommended_angle="Valid angle",
                    findings=self.valid_findings,
                )
            self.assertIn("Missing required 'expected_company_id'", str(ctx.exception))
            mock_auth.assert_not_called()

    @patch("agents.researcher.runner.ResearcherAgent")
    @patch("agents.researcher.runner.PostgrestClient")
    @patch("agents.researcher.runner.SupabaseAuthClient")
    def test_13_mismatched_company_id_rejected_before_mutation(self, mock_auth_cls, mock_client_cls, mock_agent_cls):
        """Mismatched expected_company_id and task.company_id must fail before ANY task mutation or db write."""
        mock_auth = mock_auth_cls.return_value
        mock_auth.sign_in_with_password.return_value = {
            "access_token": "mock_jwt",
            "claims": {"sub": "mock_sub"},
        }

        mock_agent = mock_agent_cls.return_value
        # Task belongs to company-999, but expected is comp-uuid-1
        mock_agent.get_assigned_active_task.return_value = {
            "id": "task-uuid-1",
            "company_id": "unexpected-comp-999",
            "status": "pending",
        }

        with self.assertRaises(ValueError) as ctx:
            run_researcher_vertical_slice(
                config=self.config,
                task_id="task-uuid-1",
                expected_company_id="comp-uuid-1",
                research_summary="Valid summary",
                recommended_angle="Valid angle",
                findings=self.valid_findings,
            )

        self.assertIn("Company/Task consistency mismatch", str(ctx.exception))

        # Crucial verification: Zero task state transitions or writes were performed!
        mock_agent.read_assigned_company.assert_not_called()
        mock_agent.transition_task_in_progress.assert_not_called()
        mock_agent.persist_research_report.assert_not_called()
        mock_agent.persist_research_findings.assert_not_called()
        mock_agent.record_activity.assert_not_called()
        mock_agent.complete_task.assert_not_called()

    def test_14_malformed_url_with_credentials_sanitized_in_error(self):
        """Guard rejects malformed URL with userinfo and does NOT leak credentials in the error."""
        sensitive_url = "https://dummy-user:dummy-password@unexpected.example.com"
        bad_config = Config(
            project_ref="vyfqmrdznmocnmzyjjcj",
            supabase_url=sensitive_url,
            anon_key="mock_key",
            researcher_email="researcher.v1@agents.internal",
            researcher_password="mock_pw",
            researcher_agent_id="hermes-researcher-v1",
        )
        with patch("agents.researcher.runner.SupabaseAuthClient") as mock_auth:
            with self.assertRaises(ValueError) as ctx:
                run_researcher_vertical_slice(
                    config=bad_config,
                    expected_company_id="comp-uuid-1",
                    research_summary="Valid summary",
                    recommended_angle="Valid angle",
                    findings=self.valid_findings,
                )
            error_msg = str(ctx.exception)
            self.assertIn("Supabase URL does not match the approved Development endpoint", error_msg)
            self.assertNotIn("dummy-user", error_msg)
            self.assertNotIn("dummy-password", error_msg)
            self.assertNotIn("unexpected.example.com", error_msg)
            mock_auth.assert_not_called()


if __name__ == "__main__":
    unittest.main()
