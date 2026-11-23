"""Run tracing: ids, JSON log lines, redaction, and the run summary.

The assertions are on the *log record text* rather than on a return value,
because the record text is what an operator greps and what a secret would leak
into. A redaction test that inspects a variable proves nothing about what was
written.
"""

from __future__ import annotations

import json
import logging
from typing import ClassVar

import pytest

from observability import RunContext, redact, redact_fields
from observability.run import EVENT_FIELDS, LOGGER_NAME, structured_event
from observability.run import RunContext as RunContextFromModule

COMPANY_ID = "11111111-1111-1111-1111-111111111111"
TASK_ID = "22222222-2222-2222-2222-222222222222"
OPERATOR_ID = "44444444-4444-4444-4444-444444444444"

JWT = "tok_live_4bf19ce2a7d34e0fb6c8"
API_KEY = "sbp_1234567890abcdef0123456789"
PASSWORD = "hunter2-correct-horse-battery"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def run_messages(caplog) -> list[str]:
    """The log record text for this run, in order (not caplog.text, which mixes levels)."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME
    ]


def records_of(caplog) -> list[dict]:
    return [json.loads(message) for message in run_messages(caplog)]


@pytest.fixture
def run_log(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    return caplog


# -----------------------------------------------------------------------------
# Identity: unique per run, stable within one
# -----------------------------------------------------------------------------


def test_each_run_gets_a_unique_run_id():
    ids = {RunContext.begin().run_id for _ in range(50)}
    assert len(ids) == 50, "a run id must be a fresh uuid4, not a shared or derived value"


def test_the_run_id_is_stable_within_one_run(run_log):
    run = RunContext.begin(stage="research")
    for name in ("research.started", "research.findings.persisted", "research.task.completed"):
        run.structured_event(name, counts={"findings": 1})

    payloads = records_of(run_log)
    assert len(payloads) == 3
    assert {p["run_id"] for p in payloads} == {run.run_id}
    assert {p["trace_id"] for p in payloads} == {run.trace_id}


def test_a_retry_shares_the_trace_and_keeps_its_own_run_id(run_log):
    first = RunContext.begin(stage="research")
    second = RunContext.begin(stage="research", trace_id=first.run_id)

    assert second.trace_id == first.run_id, "the retry joins the earlier trace"
    assert second.run_id != first.run_id, "'which run' must stay unambiguous"
    assert first.trace_id == first.run_id, "a first attempt is its own trace"


def test_a_blank_trace_id_is_refused():
    with pytest.raises(ValueError):
        RunContext.begin(trace_id="   ")
    with pytest.raises(ValueError):
        RunContext(trace_id="x", run_id="")


# -----------------------------------------------------------------------------
# The record: one line, JSON, fixed fields
# -----------------------------------------------------------------------------


def test_the_event_is_one_line_of_json_with_the_required_keys(run_log):
    run = RunContext.begin(stage="research")
    run.structured_event(
        "research.findings.persisted",
        stage="persist",
        outcome="ok",
        counts={"findings": 3, "written": 2},
        duration_ms=12.34,
        report_id="report-1",
    )

    message = run_messages(run_log)[0]
    assert "\n" not in message, "an operator greps lines, so an event must not break one"
    payload = json.loads(message)

    assert set(EVENT_FIELDS) <= set(payload), f"missing {set(EVENT_FIELDS) - set(payload)}"
    assert payload["event"] == "research.findings.persisted"
    assert payload["stage"] == "persist"
    assert payload["outcome"] == "ok"
    assert payload["counts"] == {"findings": 3, "written": 2}
    assert payload["duration_ms"] == 12.3
    assert payload["report_id"] == "report-1"
    assert payload["duration_ms"] != "" and isinstance(payload["duration_ms"], float)


def test_the_fixed_fields_cannot_be_overwritten(run_log):
    run = RunContext.begin()
    with pytest.raises(ValueError):
        run.structured_event("research.started", run_id="spoofed")
    with pytest.raises(ValueError):
        run.structured_event("research.started", counts={"a": "not-a-number"})
    with pytest.raises(ValueError):
        run.structured_event("")


def test_a_missing_run_context_is_a_silent_no_op():
    assert structured_event(None, "research.started") is None


# -----------------------------------------------------------------------------
# Redaction: the secret must not appear in the emitted text
# -----------------------------------------------------------------------------


def test_a_bearer_token_never_reaches_the_log(run_log):
    run = RunContext.begin(stage="research")
    run.structured_event(
        "research.failed",
        outcome="failed",
        error=f"adapter refused: Authorization: Bearer {JWT} (401)",
    )
    run.structured_event("research.failed", outcome="failed", error=f"retry with Bearer {JWT}")

    first, second = run_messages(run_log)
    assert JWT not in first and JWT not in second
    assert "***" in first and "***" in second
    assert "401" in first, "redaction must not eat the diagnosis"
    assert "bearer=***" in second, "the header value is replaced, the field name is kept"


def test_an_api_key_never_reaches_the_log(run_log):
    run = RunContext.begin(stage="research")
    run.structured_event(
        "research.failed",
        outcome="failed",
        error=f"GET https://db.example/rest/v1/companies?apikey={API_KEY} failed",
    )

    message = run_messages(run_log)[0]
    assert API_KEY not in message
    assert "apikey=***" in message
    assert "/rest/v1/companies" in message, "the URL path is the useful part"


def test_a_password_field_is_masked_even_without_a_key_prefix(run_log):
    run = RunContext.begin(stage="research")
    record = run.structured_event("research.failed", outcome="failed", password=PASSWORD)

    message = run_messages(run_log)[0]
    assert PASSWORD not in message
    assert record["password"] == "***"
    assert json.loads(message)["password"] == "***"


def test_a_password_inside_a_nested_field_is_masked(run_log):
    run = RunContext.begin(stage="research")
    run.structured_event(
        "research.failed",
        outcome="failed",
        connection={"dsn": "postgresql://u:p@host/db", "password": PASSWORD, "token": JWT},
    )

    message = run_messages(run_log)[0]
    assert PASSWORD not in message and JWT not in message
    assert json.loads(message)["connection"]["password"] == "***"


def test_redact_applies_the_gate_rules_and_the_cap():
    message = f"password={PASSWORD} Authorization: Bearer {JWT}\x00\x07 end"
    redacted = redact(message)

    assert PASSWORD not in redacted and JWT not in redacted
    assert "\x00" not in redacted and "\x07" not in redacted
    assert redact("x" * 400).endswith("...") and len(redact("x" * 400)) == 303
    assert redact(123) == "123", "a non-string field is stringified, not dropped"
    assert redact_fields({"retry_after_s": 5, "ok": None}) == {"retry_after_s": 5, "ok": None}


def test_a_dsn_password_is_masked_but_the_role_and_host_survive():
    redacted = redact("could not connect: postgresql://agent:s3cret-pw@127.0.0.1:5433/postgres")

    assert "s3cret-pw" not in redacted
    assert "postgresql://agent:***@127.0.0.1:5433/postgres" in redacted


# -----------------------------------------------------------------------------
# run_summary: what was emitted, not what was intended
# -----------------------------------------------------------------------------


def test_run_summary_counts_match_what_was_emitted(run_log):
    run = RunContext.begin(stage="research")
    run.structured_event("research.started", stage="research", counts={})
    run.structured_event(
        "research.findings.persisted",
        stage="persist",
        counts={"findings": 3, "written": 2, "existing": 1},
        duration_ms=8.0,
    )
    run.structured_event(
        "research.task.completed", stage="complete", counts={"written": 2}, duration_ms=4.0
    )

    emitted = records_of(run_log)
    summary = run.run_summary()

    summed: dict[str, int] = {}
    for record in emitted:
        for key, value in record["counts"].items():
            summed[key] = summed.get(key, 0) + value

    assert summary["events"] == len(emitted) == 3
    assert summary["counts"] == summed
    assert summary["counts"] == {"findings": 3, "written": 4, "existing": 1}
    assert summary["events_by_name"]["research.findings.persisted"] == 1
    assert summary["stages"] == ["research", "persist", "complete"]
    assert summary["run_id"] == run.run_id and summary["trace_id"] == run.trace_id
    assert isinstance(summary["duration_ms"], float)
    assert summary["retries"] == 0 and summary["failures"] == 0


def test_run_summary_counts_retries_and_failures(run_log):
    run = RunContext.begin(stage="research")
    run.structured_event("orchestration.task.retried", outcome="retry", counts={"retries": 1})
    run.structured_event("orchestration.task.retried", outcome="retry", counts={"retries": 1})
    run.structured_event("research.failed", outcome="failed", error="boom")

    summary = run.run_summary()
    assert summary["retries"] == 2
    assert summary["failures"] == 1
    assert summary["failure_events"] == ["research.failed"]
    assert summary["outcomes"] == {"retry": 2, "failed": 1}


def test_the_summary_is_json_serialisable():
    run = RunContext.begin(stage="research")
    run.structured_event("research.started", counts={})
    json.dumps(run.run_summary())  # a report file has to be able to hold it


def test_the_same_class_is_exported_from_both_modules():
    assert RunContextFromModule is RunContext


# -----------------------------------------------------------------------------
# Wiring: the executor emits the three events with counts and the report id
# -----------------------------------------------------------------------------


class FakeClient:
    """The smallest write path the executor needs, for an operator (human) identity."""

    claims: ClassVar[dict] = {
        "sub": OPERATOR_ID,
        "role": "authenticated",
        "app_metadata": {"user_role": "operator"},
    }

    def __init__(self) -> None:
        self.reports: list[dict] = []
        self.findings: list[dict] = []
        self.activities: list[dict] = []
        self.task_status = "pending"

    def get(self, table, params=None):
        params = dict(params or {})
        if table == "companies":
            return [
                {
                    "id": COMPANY_ID,
                    "name": "Northwind Studio",
                    "primary_domain": "northwind.example",
                    "primary_segment": "digital_agency",
                    "lifecycle_stage": "registered",
                    "is_suppressed": False,
                    "research_status": "in_progress",
                    "website_url": None,
                }
            ]
        if table == "tasks":
            return [
                {
                    "id": TASK_ID,
                    "status": self.task_status,
                    "company_id": COMPANY_ID,
                    "assigned_actor_type": "human",
                    "assigned_actor_identity": OPERATOR_ID,
                    "completed_at": None,
                }
            ]
        if table == "research_reports":
            return list(self.reports)
        if table == "research_findings":
            report_id = str(params.get("report_id", "eq."))[3:]
            return [row for row in self.findings if row["report_id"] == report_id]
        if table == "activities":
            return list(self.activities)
        raise AssertionError(f"unexpected table {table}")

    def post(self, table, row):
        if table == "research_reports":
            created = {**row, "id": "report-1"}
            self.reports.append(created)
            return [created]
        if table == "research_findings":
            created = {**row, "id": f"finding-{len(self.findings) + 1}"}
            self.findings.append(created)
            return [created]
        if table == "activities":
            created = {**row, "id": "activity-1"}
            self.activities.append(created)
            return [created]
        raise AssertionError(f"unexpected table {table}")

    def patch(self, table, data, params=None, prefer=""):
        assert table == "tasks"
        self.task_status = str(data.get("status") or self.task_status)
        return [
            {
                "id": TASK_ID,
                "company_id": COMPANY_ID,
                "status": self.task_status,
                "assigned_actor_type": "human",
                "assigned_actor_identity": OPERATOR_ID,
                "completed_at": None,
            }
        ]


def test_the_executor_emits_started_findings_and_completion(run_log):
    from research.executor import FixtureResearchProvider, execute_research

    client = FakeClient()
    run = RunContext.begin(stage="research")
    result = execute_research(
        client,
        COMPANY_ID,
        FixtureResearchProvider(),
        task_id=TASK_ID,
        run=run,
    )

    assert result.status == "created" and result.findings_written == 3, "behaviour is unchanged"

    payloads = records_of(run_log)
    assert [p["event"] for p in payloads] == [
        "research.started",
        "research.findings.persisted",
        "research.task.completed",
    ]

    findings = payloads[1]
    assert findings["counts"] == {"findings": 3, "written": 3, "existing": 0}
    assert findings["report_id"] == "report-1", "the event must name the report it wrote"
    assert findings["report_version"] == 1

    completed = payloads[2]
    assert completed["task_id"] == TASK_ID
    assert completed["task_status"] == "completed"
    assert completed["report_id"] == "report-1"

    assert {p["run_id"] for p in payloads} == {run.run_id}
    assert run.run_summary()["counts"]["findings"] == 3


def test_the_executor_carries_the_caller_trace(run_log):
    from research.executor import FixtureResearchProvider, execute_research

    run = RunContext.begin(stage="research", trace_id="trace-from-the-first-attempt")

    execute_research(FakeClient(), COMPANY_ID, FixtureResearchProvider(), task_id=TASK_ID, run=run)

    assert {p["trace_id"] for p in records_of(run_log)} == {"trace-from-the-first-attempt"}
    assert run.run_summary()["trace_id"] == "trace-from-the-first-attempt"


def test_the_executor_still_works_without_a_run_context(run_log):
    """Instrumentation is optional: the same writes, an id the caller did not ask for."""
    from research.executor import FixtureResearchProvider, execute_research

    result = execute_research(FakeClient(), COMPANY_ID, FixtureResearchProvider())

    assert result.status == "created"
    payloads = records_of(run_log)
    assert payloads[0]["event"] == "research.started"
    assert payloads[-1]["event"] == "research.completed"
    assert len({p["run_id"] for p in payloads}) == 1


def test_the_cli_accepts_a_run_id_that_becomes_the_trace():
    from scripts.research_run import build_parser

    args = build_parser().parse_args(["--company-id", COMPANY_ID, "--run-id", "trace-abc"])
    run = RunContext.begin(stage="research", trace_id=args.run_id)

    assert args.run_id == "trace-abc"
    assert run.trace_id == "trace-abc"
    assert run.run_id != "trace-abc"
