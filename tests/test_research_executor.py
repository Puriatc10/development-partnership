"""Research executor contract: persisted research, idempotent retries, and refusals.

A stateful fake client stands in for the RLS-bound PostgreSQL client. It models the parts of
the deployed schema the executor depends on — the ``(company_id, report_version)`` unique
index, the findings foreign key, ``chk_verified_fact_requires_evidence``, the agent policies
that deny a write with 42501, and the task governance trigger — so the tests can interrupt a
write and then retry for real.

What these tests do NOT prove: live RLS, the actual trigger bodies, or psycopg behaviour
against PostgreSQL 17. Those are read from the migrations and called out in the report; the
one test that touches the real database is a read-only enum check, skipped when there is no
reachable database.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

import pytest

from contracts.provider import Evidence, ProviderInfo, ProviderResult
from integrations.postgres.client import DatabaseError
from research.executor import (
    CATEGORIES,
    CONFIDENCES,
    FINDING_TYPES,
    VERIFIED_FACT_CONFIDENCES,
    CompanyNotVisibleError,
    CompanySuppressedError,
    DuplicateReportVersion,
    Finding,
    FixtureResearchProvider,
    IntegrationResearchProvider,
    ResearchPayload,
    ResearchPersistenceError,
    ResearchProviderError,
    ResearchValidationError,
    ResearchWriteDenied,
    TaskScopeError,
    execute_research,
    validate_payload,
)

REPO = Path(__file__).resolve().parent.parent
EXECUTOR = REPO / "research" / "executor.py"
MIGRATION = REPO / "db" / "migrations" / "20261107174129_core_crm_schema.sql"
INTEGRITY = REPO / "db" / "migrations" / "20261123121000_integrity_constraints.sql"

COMPANY_ID = "11111111-1111-1111-1111-111111111111"
TASK_ID = "22222222-2222-2222-2222-222222222222"
OTHER_COMPANY_ID = "33333333-3333-3333-3333-333333333333"
OPERATOR_ID = "44444444-4444-4444-4444-444444444444"
AGENT_ID = "hermes-researcher-v1"

HUMAN_CLAIMS = {
    "sub": OPERATOR_ID,
    "role": "authenticated",
    "app_metadata": {"user_role": "operator"},
}


def claims_for(subagent_type: str = "researcher") -> dict[str, Any]:
    return {
        "sub": "55555555-5555-5555-5555-555555555555",
        "role": "authenticated",
        "app_metadata": {
            "user_role": "agent",
            "subagent_type": subagent_type,
            "agent_id": AGENT_ID,
        },
    }


AGENT_CLAIMS = claims_for("researcher")
DRAFTER_CLAIMS = claims_for("drafter")


# -----------------------------------------------------------------------------
# Fake infrastructure
# -----------------------------------------------------------------------------


def company_row(**over: Any) -> dict[str, Any]:
    row = {
        "id": COMPANY_ID,
        "name": "Northwind Studio",
        "primary_domain": "northwind.example",
        "primary_segment": "webflow_studio",
        "lifecycle_stage": "identified",
        "is_suppressed": False,
        "research_status": "not_started",
        "website_url": "https://northwind.example",
    }
    row.update(over)
    return row


def task_row(**over: Any) -> dict[str, Any]:
    row = {
        "id": TASK_ID,
        "status": "pending",
        "company_id": COMPANY_ID,
        "assigned_actor_type": "agent",
        "assigned_actor_identity": AGENT_ID,
        "completed_at": None,
    }
    row.update(over)
    return row


class FakeClient:
    """Stateful RLS-bound client stand-in.

    Refusals mirror the deployed policies by SQLSTATE, not by class, so the executor's
    ``except`` paths are exercised the same way a live database would exercise them.
    """

    def __init__(
        self,
        *,
        claims: dict[str, Any] | None = None,
        companies: list[dict[str, Any]] | None = None,
        tasks: list[dict[str, Any]] | None = None,
        fail_findings_after: int | None = None,
    ) -> None:
        self.claims = dict(claims if claims is not None else HUMAN_CLAIMS)
        self.companies = [dict(c) for c in (companies if companies is not None else [company_row()])]
        self.tasks = [dict(t) for t in (tasks if tasks is not None else [task_row()])]
        self.research_reports: list[dict[str, Any]] = []
        self.research_findings: list[dict[str, Any]] = []
        self.activities: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_findings_after = fail_findings_after
        self._seq = 0

    # -- helpers -----------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _app(self) -> dict[str, Any]:
        app = self.claims.get("app_metadata")
        return dict(app) if isinstance(app, dict) else {}

    def _active_task(self, company_id: str) -> bool:
        return any(
            t.get("company_id") == company_id and t.get("status") in ("pending", "in_progress")
            for t in self.tasks
        )

    def _authorize_research_write(self, table: str, company_id: str | None) -> None:
        app = self._app()
        if app.get("user_role") != "agent":
            return
        # agent_* policies: subagent_type must be 'researcher' and a task must be active.
        if app.get("subagent_type") != "researcher":
            raise DatabaseError(
                "new row violates row-level security policy for table " + table, code="42501"
            )
        if company_id is None or not self._active_task(company_id):
            raise DatabaseError(
                "new row violates row-level security policy for table " + table, code="42501"
            )

    def _visible_companies(self) -> list[dict[str, Any]]:
        """agent_select_companies: an agent sees only companies with its active task."""
        app = self._app()
        if app.get("user_role") != "agent":
            return self.companies
        agent_id = app.get("agent_id")
        scoped = {
            t["company_id"]
            for t in self.tasks
            if t.get("status") in ("pending", "in_progress")
            and t.get("assigned_actor_identity") == agent_id
        }
        return [c for c in self.companies if c["id"] in scoped]

    def _select(self, rows: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
        out = list(rows)
        for key, value in params.items():
            if key in {"select", "order", "limit", "offset"}:
                continue
            if not isinstance(value, str):
                continue
            if value.startswith("eq."):
                wanted = value[3:]
                out = [r for r in out if str(r.get(key)) == wanted]
            elif value.startswith("in.(") and value.endswith(")"):
                wanted = {v.strip().strip("'") for v in value[4:-1].split(",")}
                out = [r for r in out if str(r.get(key)) in wanted]
            elif value == "not.is.null":
                out = [r for r in out if r.get(key) is not None]
            elif value == "is.null":
                out = [r for r in out if r.get(key) is None]
        ordering = str(params.get("order") or "")
        if ordering:
            column = ordering.split(".")[0]
            reverse = ordering.endswith(".desc")
            out.sort(key=lambda r: r.get(column) or 0, reverse=reverse)
        if params.get("limit") is not None:
            out = out[: int(params["limit"])]
        return [dict(r) for r in out]

    # -- GET ---------------------------------------------------------------
    def get(self, table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        self.calls.append(("GET", table, params))
        if table == "companies":
            return self._select(self._visible_companies(), params)
        if table == "tasks":
            return self._select(self.tasks, params)
        if table == "research_reports":
            return self._select(self.research_reports, params)
        if table == "research_findings":
            return self._select(self.research_findings, params)
        if table == "activities":
            return self._select(self.activities, params)
        raise AssertionError(f"unexpected table {table!r}")

    # -- POST --------------------------------------------------------------
    def post(self, table: str, data: Any) -> list[dict[str, Any]]:
        rows = [dict(data)] if isinstance(data, dict) else [dict(r) for r in data]
        self.calls.append(("POST", table, rows))

        if table == "research_reports":
            self._authorize_research_write(table, rows[0].get("company_id"))
            for row in rows:
                if any(
                    r["company_id"] == row["company_id"]
                    and r["report_version"] == row["report_version"]
                    for r in self.research_reports
                ):
                    raise DatabaseError(
                        'duplicate key value violates unique constraint '
                        '"uq_research_reports_company_version"',
                        code="23505",
                    )
            created = []
            for row in rows:
                stored = {
                    **row,
                    "id": self._next_id("report"),
                    "created_at": "2026-11-23T00:00:00+00:00",
                }
                self.research_reports.append(stored)
                created.append(dict(stored))
            return created

        if table == "research_findings":
            report_id = rows[0].get("report_id")
            company_id = rows[0].get("company_id")
            self._authorize_research_write(table, company_id)
            parent = [r for r in self.research_reports if r["id"] == report_id]
            if not parent or parent[0]["company_id"] != company_id:
                raise DatabaseError(
                    'insert or update on table "research_findings" violates foreign key '
                    'constraint "fk_findings_report_company"',
                    code="23503",
                )
            for row in rows:
                if row.get("finding_type") == "verified_fact" and (
                    not row.get("evidence_url")
                    or row.get("confidence") not in ("high", "medium")
                ):
                    raise DatabaseError(
                        'new row for relation "research_findings" violates check constraint '
                        '"chk_verified_fact_requires_evidence"',
                        code="23514",
                    )
            if (
                self.fail_findings_after is not None
                and len(self.research_findings) >= self.fail_findings_after
            ):
                # An interruption mid-way: the rows already inserted stay inserted, just as a
                # cancelled statement after earlier committed statements would leave them.
                raise DatabaseError("canceling statement due to statement timeout", code="57014")
            created = []
            for row in rows:
                stored = {**row, "id": self._next_id("finding"), "created_at": "2026-11-23T00:00:00+00:00"}
                self.research_findings.append(stored)
                created.append(dict(stored))
            return created

        if table == "activities":
            company_id = rows[0].get("company_id")
            self._authorize_research_write(table, company_id)
            created = []
            for row in rows:
                stored = {**row, "id": self._next_id("activity"), "occurred_at": "2026-11-23T00:00:00+00:00"}
                self.activities.append(stored)
                created.append(dict(stored))
            return created

        raise AssertionError(f"unexpected table {table!r}")

    # -- PATCH -------------------------------------------------------------
    def patch(self, table: str, data: dict[str, Any], params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        self.calls.append(("PATCH", table, dict(data)))
        if table != "tasks":
            raise AssertionError(f"unexpected patch on {table!r}")

        target_id = str(params.get("id", "")).replace("eq.", "")
        matches = [t for t in self.tasks if t["id"] == target_id]
        if not matches:
            return []
        row = matches[0]
        new_status = data.get("status")

        if self._app().get("user_role") == "agent":
            if row["assigned_actor_identity"] != self._app().get("agent_id"):
                return []
            if row["status"] in ("completed", "cancelled"):
                raise DatabaseError(
                    "Security Policy Violation: Agents cannot reopen, modify, or transition "
                    "completed or cancelled tasks.",
                    code="P0001",
                )
            if row["status"] == "pending" and new_status == "completed":
                raise DatabaseError(
                    "Lifecycle Violation: Tasks must be transitioned to in_progress before "
                    "completion.",
                    code="P0001",
                )

        row["status"] = new_status
        # fn_enforce_task_governance: completed_at is set on completion, cleared otherwise.
        row["completed_at"] = "2026-11-23T00:00:00+00:00" if new_status == "completed" else None
        return [dict(row)]


def run(client: FakeClient, provider: Any = None, **kwargs: Any):
    return execute_research(
        client,
        kwargs.pop("company_id", COMPANY_ID),
        provider or FixtureResearchProvider(),
        **kwargs,
    )


class StaticProvider:
    """A provider whose summary the test controls (the fixture is fixed)."""

    name = "static"

    def __init__(self, summary: str, findings: tuple[Finding, ...] | None = None) -> None:
        self.summary = summary
        self.findings = findings

    def produce(self, company):
        return ResearchPayload(
            summary=self.summary,
            overall_confidence="medium",
            findings=self.findings
            or (
                Finding(
                    category="client_roster",
                    finding_type="hypothesis",
                    statement=f"Hypothesis for {self.summary}",
                    confidence="low",
                ),
            ),
        )


# -----------------------------------------------------------------------------
# Schema contract — read the deployed enums, do not assume them
# -----------------------------------------------------------------------------


def _enum_values(type_name: str) -> set[str]:
    text = MIGRATION.read_text(encoding="utf-8")
    block = text.split(f"CREATE TYPE {type_name} AS ENUM (", 1)[1].split(");", 1)[0]
    return set(re.findall(r"'([a-z_]+)'", block))


def test_categories_match_the_deployed_enum():
    assert CATEGORIES == _enum_values("finding_category_type")


def test_finding_types_match_the_deployed_enum_and_hypothesis_is_the_word():
    assert FINDING_TYPES == _enum_values("research_finding_type")
    assert "hypothesis" in FINDING_TYPES
    assert "inference" not in FINDING_TYPES, "the schema never had an 'inference' finding type"


def test_confidences_match_the_deployed_enum():
    assert CONFIDENCES == _enum_values("confidence_level_type")
    assert VERIFIED_FACT_CONFIDENCES == {"high", "medium"}


def test_version_uniqueness_is_enforced_by_the_database_too():
    assert "uq_research_reports_company_version" in INTEGRITY.read_text(encoding="utf-8")
    assert "(company_id, report_version)" in INTEGRITY.read_text(encoding="utf-8")


def test_executor_imports_no_integration_adapter_at_module_import_time():
    tree = ast.parse(EXECUTOR.read_text(encoding="utf-8"))
    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level.append(node.module)
    assert not [m for m in module_level if m.split(".")[0] == "integrations"]
    assert not [m for m in module_level if m.split(".")[0] == "psycopg"]


def test_executor_opens_no_connection_of_its_own():
    tree = ast.parse(EXECUTOR.read_text(encoding="utf-8"))
    connect_calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"connect", "transaction", "cursor"}
    ]
    assert connect_calls == [], f"the executor must only use the injected client, saw {connect_calls}"


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


def test_happy_path_persists_report_findings_and_activity():
    client = FakeClient()
    result = run(client, task_id=TASK_ID)

    assert result.status == "created"
    assert result.report_version == 1
    assert result.findings_total == 3
    assert result.findings_written == 3
    assert result.findings_existing == 0
    assert result.task_status == "completed"

    assert len(client.research_reports) == 1
    report = client.research_reports[0]
    assert report["company_id"] == COMPANY_ID
    assert report["research_agent_identity"] == OPERATOR_ID
    assert report["report_version"] == 1
    assert report["overall_confidence"] == "low"
    assert "SYNTHETIC FIXTURE" in report["summary"]
    assert report["gaps_and_unknowns"]

    assert len(client.research_findings) == 3
    assert {f["report_id"] for f in client.research_findings} == {report["id"]}
    assert {f["category"] for f in client.research_findings} == {
        "technology_stack",
        "growth_signals",
        "pricing_rates",
    }
    assert len(client.activities) == 1
    assert client.activities[0]["activity_type"] == "note"
    assert client.activities[0]["performed_by_actor_type"] == "human"
    assert client.activities[0]["metadata"]["report_id"] == report["id"]


def test_a_verified_fact_persists_with_its_url():
    client = FakeClient()
    run(client, task_id=TASK_ID)
    verified = [f for f in client.research_findings if f["finding_type"] == "verified_fact"]
    assert len(verified) == 1
    assert verified[0]["evidence_url"].startswith("https://")
    assert verified[0]["confidence"] in VERIFIED_FACT_CONFIDENCES


def test_activity_metadata_records_the_report_and_provider():
    client = FakeClient()
    result = run(client, task_id=TASK_ID)
    metadata = client.activities[0]["metadata"]
    assert metadata["report_id"] == result.report_id
    assert metadata["report_version"] == 1
    assert metadata["provider"] == "fixture"
    assert metadata["sources"] == ["https://northwind.example/about"]


def test_task_transition_order_completes_last():
    client = FakeClient()
    run(client, task_id=TASK_ID)

    index = {i: call for i, call in enumerate(client.calls)}
    in_progress = next(
        i for i, c in index.items() if c[0] == "PATCH" and c[1] == "tasks" and c[2]["status"] == "in_progress"
    )
    report = next(i for i, c in index.items() if c[0] == "POST" and c[1] == "research_reports")
    findings = [i for i, c in index.items() if c[0] == "POST" and c[1] == "research_findings"]
    activity = next(i for i, c in index.items() if c[0] == "POST" and c[1] == "activities")
    completed = next(
        i for i, c in index.items() if c[0] == "PATCH" and c[1] == "tasks" and c[2]["status"] == "completed"
    )

    assert in_progress < report, "the task is claimed before research is written"
    assert report < min(findings), "findings need their report to exist (composite FK)"
    assert max(findings) < activity, "the activity note summarises findings that already landed"
    assert activity < completed, "policy writes require the task to still be in_progress"
    assert index[completed][2] == {"status": "completed"}, "completed_at is left to the trigger"


def test_agent_run_records_agent_attribution():
    client = FakeClient(claims=AGENT_CLAIMS)
    result = run(client, task_id=TASK_ID)

    assert result.identity == AGENT_ID
    assert client.research_reports[0]["research_agent_identity"] == AGENT_ID
    assert client.activities[0]["performed_by_actor_type"] == "agent"
    assert client.activities[0]["performed_by_actor_identity"] == AGENT_ID


# -----------------------------------------------------------------------------
# Idempotency
# -----------------------------------------------------------------------------


def test_retry_with_the_same_payload_reuses_the_report_and_adds_no_findings():
    client = FakeClient()
    first = run(client, task_id=TASK_ID)
    before = len(client.calls)
    second = run(client, task_id=TASK_ID)

    assert second.status == "already_completed", "a completed task is never written twice"
    assert second.report_id == first.report_id
    assert len(client.research_reports) == 1
    assert len(client.research_findings) == 3
    assert len(client.activities) == 1

    writes_after = [c for c in client.calls[before:] if c[0] in ("POST", "PATCH")]
    assert writes_after == [], "an already-completed run performs no writes"


def test_retry_without_a_task_resumes_the_same_report():
    client = FakeClient()
    first = run(client)
    second = run(client)

    assert first.status == "created" and second.status == "resumed"
    assert second.report_id == first.report_id
    assert (second.findings_written, second.findings_existing) == (0, 3)
    assert len(client.research_reports) == 1
    assert len(client.research_findings) == 3
    assert len(client.activities) == 1, "the note is not duplicated on resume"


def test_a_genuinely_new_payload_gets_the_next_version():
    client = FakeClient()
    first = run(client, provider=StaticProvider("First session summary."))
    second = run(client, provider=StaticProvider("Second session summary."))

    assert (first.report_version, second.report_version) == (1, 2)
    assert len(client.research_reports) == 2
    assert first.report_id != second.report_id


def test_explicit_version_is_reused_only_for_the_same_session():
    client = FakeClient()
    first = run(client, provider=StaticProvider("One summary."), report_version=1)
    resumed = run(client, provider=StaticProvider("One summary."), report_version=1)
    assert resumed.status == "resumed" and resumed.report_id == first.report_id

    with pytest.raises(DuplicateReportVersion) as exc:
        run(client, provider=StaticProvider("A different summary."), report_version=1)
    assert "report_version 1 already exists" in str(exc.value)
    assert "refusing to write a second report" in str(exc.value)
    assert len(client.research_reports) == 1


def test_partial_write_then_retry_resumes_without_duplicating_findings():
    client = FakeClient(fail_findings_after=1)
    with pytest.raises(ResearchPersistenceError) as exc:
        run(client, task_id=TASK_ID)

    failure = exc.value
    assert failure.report_id is not None, "the failure names the report a retry must resume"
    assert failure.findings_written == 1
    assert "re-run to resume" in str(failure)
    assert len(client.research_reports) == 1
    assert len(client.research_findings) == 1
    assert client.tasks[0]["status"] == "in_progress", "the task stays claimed, never half-completed"

    client.fail_findings_after = None
    retry = run(client, task_id=TASK_ID)

    assert retry.status == "resumed"
    assert retry.report_id == failure.report_id
    assert retry.report_version == 1
    assert retry.findings_written == 2, "only the two missing findings are inserted"
    assert retry.findings_existing == 1
    assert len(client.research_findings) == 3
    statements = [f["statement"] for f in client.research_findings]
    assert len(statements) == len(set(statements)), "no duplicated evidence statements"
    assert len(client.activities) == 1, "the activity note is written exactly once"
    assert client.tasks[0]["status"] == "completed"


def test_a_concurrent_write_of_the_same_version_is_refused():
    class RacingClient(FakeClient):
        """Another session inserts version 1 between our read and our insert."""

        def post(self, table, data):
            if table == "research_reports":
                self.calls.append(("POST", table, [dict(data)]))
                raise DatabaseError(
                    'duplicate key value violates unique constraint '
                    '"uq_research_reports_company_version"',
                    code="23505",
                )
            return super().post(table, data)

    client = RacingClient()
    with pytest.raises(DuplicateReportVersion) as exc:
        run(client, task_id=TASK_ID)
    assert "concurrently" in str(exc.value)
    assert "uq_research_reports_company_version" in str(exc.value)


# -----------------------------------------------------------------------------
# Refusals — validation happens before any write
# -----------------------------------------------------------------------------


def test_invalid_finding_category_is_refused_before_any_write():
    client = FakeClient()
    provider = StaticProvider(
        "Bad category.",
        findings=(
            Finding(
                category="market_fit",  # not in finding_category_type
                finding_type="hypothesis",
                statement="A claim.",
                confidence="low",
            ),
        ),
    )
    with pytest.raises(ResearchValidationError) as exc:
        run(client, provider=provider, task_id=TASK_ID)

    assert "market_fit" in str(exc.value)
    assert "finding_category_type" in str(exc.value)
    assert [c for c in client.calls if c[0] != "GET"] == [], "validation must precede every write"
    assert client.research_reports == [] and client.research_findings == []
    assert client.tasks[0]["status"] == "pending", "a refused payload never claims the task"


def test_inference_is_refused_because_the_schema_says_hypothesis():
    provider = StaticProvider(
        "Old vocabulary.",
        findings=(
            Finding(
                category="growth_signals",
                finding_type="inference",
                statement="Probably growing.",
                confidence="low",
            ),
        ),
    )
    with pytest.raises(ResearchValidationError) as exc:
        run(FakeClient(), provider=provider)
    assert "inference" in str(exc.value) and "hypothesis" in str(exc.value)


def test_verified_fact_without_a_url_is_refused():
    provider = StaticProvider(
        "No evidence.",
        findings=(
            Finding(
                category="technology_stack",
                finding_type="verified_fact",
                statement="Built on Webflow.",
                confidence="high",
            ),
        ),
    )
    with pytest.raises(ResearchValidationError) as exc:
        run(FakeClient(), provider=provider)
    message = str(exc.value)
    assert "verified_fact without an http(s) evidence_url" in message
    assert "chk_verified_fact_requires_evidence" in message


def test_verified_fact_with_low_confidence_is_refused():
    provider = StaticProvider(
        "Weak evidence.",
        findings=(
            Finding(
                category="client_roster",
                finding_type="verified_fact",
                statement="Works with Meridian.",
                confidence="low",
                evidence_url="https://northwind.example/work",
            ),
        ),
    )
    with pytest.raises(ResearchValidationError) as exc:
        run(FakeClient(), provider=provider)
    assert "requires one of high, medium" in str(exc.value)


def test_a_payload_without_findings_is_refused():
    with pytest.raises(ResearchValidationError):
        validate_payload(ResearchPayload(summary="Nothing to show.", findings=()))


def test_duplicate_statements_are_collapsed_not_written_twice():
    finding = Finding(
        category="technology_stack",
        finding_type="hypothesis",
        statement="Probably Webflow.",
        confidence="low",
    )
    validated = validate_payload(ResearchPayload(summary="Dupes.", findings=(finding, finding)))
    assert len(validated) == 1


def test_company_not_visible_refuses_before_any_write():
    client = FakeClient(companies=[])
    with pytest.raises(CompanyNotVisibleError) as exc:
        run(client, task_id=TASK_ID)
    assert "not visible" in str(exc.value)
    assert "agent_select_companies" in str(exc.value)
    assert client.calls and all(c[0] == "GET" for c in client.calls)


def test_suppressed_company_is_refused():
    client = FakeClient(companies=[company_row(is_suppressed=True)])
    with pytest.raises(CompanySuppressedError):
        run(client, task_id=TASK_ID)
    assert [c for c in client.calls if c[0] != "GET"] == []


def test_task_for_another_company_is_refused():
    client = FakeClient(tasks=[task_row(company_id=OTHER_COMPANY_ID)])
    with pytest.raises(TaskScopeError):
        run(client, task_id=TASK_ID)
    assert client.research_reports == []


# -----------------------------------------------------------------------------
# 42501 — the message must name the claim
# -----------------------------------------------------------------------------


def test_42501_names_the_missing_subagent_type():
    client = FakeClient(claims=DRAFTER_CLAIMS)
    with pytest.raises(ResearchWriteDenied) as exc:
        run(client, task_id=TASK_ID)

    error = exc.value
    assert error.code == "42501"
    assert error.missing_claim == "app_metadata.subagent_type"
    assert "app_metadata.subagent_type" in str(error)
    assert "'researcher'" in str(error)
    assert "research_reports" in str(error)


def test_an_agent_without_a_task_cannot_see_the_company():
    client = FakeClient(claims=AGENT_CLAIMS, tasks=[])
    with pytest.raises(CompanyNotVisibleError) as exc:
        run(client)  # no task_id: agent_select_companies needs an assigned, active task

    assert "agent_select_companies" in str(exc.value)
    assert [c for c in client.calls if c[0] != "GET"] == []
    assert client.research_reports == []


def test_explanation_without_a_task_names_the_task_scope():
    from research.executor import explain_write_denial

    message, claim = explain_write_denial(
        AGENT_CLAIMS, table="research_reports", identity=AGENT_ID, task=None
    )
    assert claim is not None and claim.startswith("tasks.status")
    assert "no task_id was supplied" in message


def test_agent_retry_after_completion_reports_already_completed():
    client = FakeClient(claims=AGENT_CLAIMS)
    first = run(client, task_id=TASK_ID)
    before = len(client.calls)
    second = run(client, task_id=TASK_ID)

    # The task is completed, so the company is no longer visible to the agent. The retry still
    # has to report the finished run rather than claim the company vanished.
    assert second.status == "already_completed"
    assert second.report_id == first.report_id
    assert second.findings_written == 0
    assert [c for c in client.calls[before:] if c[0] != "GET"] == []


def test_42501_with_an_absent_user_role_names_it():
    from research.executor import explain_write_denial

    message, claim = explain_write_denial(
        {"sub": "x"}, table="research_reports", identity="x", task=None
    )
    assert claim == "app_metadata.user_role"
    assert "app_metadata.user_role is missing" in message


def test_42501_with_a_mismatched_identity_names_the_claim():
    from research.executor import explain_write_denial

    message, claim = explain_write_denial(
        AGENT_CLAIMS,
        table="research_reports",
        identity="some-other-agent",
        task=None,
    )
    assert claim is not None and claim.startswith("app_metadata.agent_id")
    assert "is not the claim's agent_id" in message


# -----------------------------------------------------------------------------
# Providers
# -----------------------------------------------------------------------------


def test_fixture_provider_is_labelled_synthetic_and_deterministic():
    provider = FixtureResearchProvider()
    first = provider.produce(company_row())
    second = provider.produce(company_row())

    assert first == second
    assert first.summary.startswith("[SYNTHETIC FIXTURE]")
    assert all(f.statement.startswith("[SYNTHETIC FIXTURE]") for f in first.findings)
    assert {f.finding_type for f in first.findings} == {
        "verified_fact",
        "hypothesis",
        "information_gap_tbd",
    }
    validate_payload(first)


class FakeIntegrationProvider:
    """The ``integrations/`` surface: execute(ProviderRequest) -> ProviderResult."""

    name = "gpt_researcher"

    def __init__(self, findings: list[dict[str, Any]] | None = None) -> None:
        self.findings = findings
        self.requests: list[Any] = []

    def execute(self, request):
        self.requests.append(request)
        data: dict[str, Any] = {"report": "Northwind Studio builds Webflow sites with custom APIs."}
        if self.findings is not None:
            data["findings"] = self.findings
        return ProviderResult(
            status="ok",
            data=data,
            provider=ProviderInfo(name="gpt_researcher", version="3.8.0"),
            evidence=(
                Evidence(
                    url="https://northwind.example/about",
                    excerpt="We build on Webflow.",
                    provider="gpt_researcher",
                    provider_version="3.8.0",
                ),
            ),
        )


def test_integration_adapter_turns_prose_into_an_honest_gap_finding():
    adapter = IntegrationResearchProvider(FakeIntegrationProvider())
    payload = adapter.produce(company_row())

    assert adapter.name == "gpt_researcher"
    assert payload.summary.startswith("Northwind Studio builds Webflow")
    assert [s.url for s in payload.sources] == ["https://northwind.example/about"]
    assert payload.overall_confidence == "low"
    assert len(payload.findings) == 1
    assert payload.findings[0].finding_type == "information_gap_tbd"
    assert payload.findings[0].category == "other"
    assert payload.findings[0].confidence == "unverified"


def test_integration_adapter_passes_through_structured_findings_and_runs_end_to_end():
    structured = [
        {
            "category": "technology_stack",
            "finding_type": "verified_fact",
            "statement": "The studio builds on Webflow with custom API integrations.",
            "confidence": "high",
            "evidence_url": "https://northwind.example/about",
            "evidence_excerpt": "We build on Webflow.",
        }
    ]
    adapter = IntegrationResearchProvider(FakeIntegrationProvider(structured))
    client = FakeClient()
    result = run(client, provider=adapter)

    assert result.findings_written == 1
    assert client.research_reports[0]["summary"].startswith("Northwind Studio builds")
    assert client.activities[0]["metadata"]["provider"] == "gpt_researcher"


def test_integration_adapter_requires_a_domain():
    adapter = IntegrationResearchProvider(FakeIntegrationProvider())
    with pytest.raises(ResearchProviderError) as exc:
        adapter.produce({"name": "No Domain"})
    assert "primary_domain" in str(exc.value)


# -----------------------------------------------------------------------------
# Live database — read-only, skipped when there is nothing to talk to
# -----------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("ERYNDEX_DB_URL"), reason="ERYNDEX_DB_URL is not set")
def test_live_enums_match_the_deployed_database():
    """Read the enums from PostgreSQL itself; never writes, never prints the DSN."""
    psycopg = pytest.importorskip("psycopg")

    from contracts.environment import DatabaseConfig  # noqa: PLC0415

    dsn = DatabaseConfig.load().dsn
    if not dsn:  # pragma: no cover - conftest loads .env when present
        pytest.skip("no connection string configured")

    try:
        with psycopg.connect(dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT t.typname, e.enumlabel FROM pg_type t "
                    "JOIN pg_enum e ON e.enumtypid = t.oid "
                    "WHERE t.typname = ANY(%s)",
                    (["finding_category_type", "research_finding_type", "confidence_level_type"],),
                )
                rows = cursor.fetchall()
    except psycopg.OperationalError as exc:  # pragma: no cover - depends on the host
        pytest.skip(f"database not reachable: {type(exc).__name__}")

    live: dict[str, set[str]] = {}
    for type_name, label in rows:
        live.setdefault(type_name, set()).add(label)

    assert live.get("finding_category_type") == CATEGORIES
    assert live.get("research_finding_type") == FINDING_TYPES
    assert live.get("confidence_level_type") == CONFIDENCES
