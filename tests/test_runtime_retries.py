"""``RetryingRuntime``: bounded retries, a per-task timeout, and escalation.

Every test runs on an injected clock and sleep, so the backoff schedule is
asserted without waiting for it. The one exception is the timeout test, which
has to use a real (very short) wait because the mechanism *is* a real wait — 50 ms
per attempt, and the worker threads are released in a fixture teardown.
"""

from __future__ import annotations

import inspect
import threading
from datetime import datetime, timezone

import pytest

from observability import RunContext
from orchestration.contracts import (
    Runtime,
    TaskClaim,
    TaskClaimConflict,
    TaskOutcome,
    TaskSourceError,
)
from orchestration.runtime import (
    ESCALATION_STATUS,
    Escalation,
    RetryingRuntime,
    is_escalation,
)
from orchestration.task_source import ManualRuntime

IDENTITY = "hermes-researcher-v1"


def claim(task_id: str = "task-1") -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        company_id="company-1",
        task_type="company_research",
        priority="high",
        assigned_identity=IDENTITY,
        attempt=1,
        claimed_at=datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc),
    )


class FakeSource:
    """A ``TaskSource`` that hands out prepared claims and records completions."""

    def __init__(self, claims: list[TaskClaim] | None = None, *, claim_error: Exception | None = None):
        self._claims = list(claims or [])
        self._claim_error = claim_error
        self.claim_calls = 0
        self.transitions: list[tuple[str, str]] = []
        self.completions: list[tuple[str, TaskOutcome, str]] = []

    def claim(self, identity, *, limit=1, task_types=None):
        self.claim_calls += 1
        if self._claim_error is not None:
            raise self._claim_error
        taken, self._claims = self._claims, []
        return taken

    def transition(self, task_id, status, *, identity, detail=""):
        self.transitions.append((task_id, status))

    def complete(self, task_id, outcome, *, identity):
        self.completions.append((task_id, outcome, identity))


class PropagatingRuntime:
    """A ``Runtime`` that lets worker exceptions out, to prove what is not swallowed."""

    def run_once(self, worker, *, source, limit=1):
        return [worker(item) for item in source.claim(IDENTITY, limit=limit)]


@pytest.fixture
def guarded():
    """A runtime over ``ManualRuntime`` that never really sleeps."""
    sleeps: list[float] = []

    def build(inner=None, **kwargs) -> RetryingRuntime:
        runtime = inner if inner is not None else ManualRuntime(identity=IDENTITY)
        return RetryingRuntime(
            runtime,
            max_attempts=kwargs.pop("max_attempts", 3),
            timeout_s=kwargs.pop("timeout_s", 5.0),
            backoff_s=kwargs.pop("backoff_s", 0.5),
            clock=kwargs.pop("clock", lambda: 0.0),
            sleep=kwargs.pop("sleep", sleeps.append),
            **kwargs,
        )

    build.sleeps = sleeps  # type: ignore[attr-defined]
    return build


# -----------------------------------------------------------------------------
# Shape
# -----------------------------------------------------------------------------


def test_it_is_a_runtime_with_the_same_call_shape(guarded):
    runtime = guarded()
    assert isinstance(runtime, Runtime)
    protocol = inspect.signature(Runtime.run_once)
    implementation = inspect.signature(RetryingRuntime.run_once)
    assert list(protocol.parameters) == list(implementation.parameters)
    for name, parameter in protocol.parameters.items():
        assert parameter.kind == implementation.parameters[name].kind
        assert parameter.default == implementation.parameters[name].default


def test_an_escalation_is_a_task_outcome_any_source_can_record():
    outcome = Escalation(status=ESCALATION_STATUS, summary="gave up", task_id="task-1", attempts=2)
    assert isinstance(outcome, TaskOutcome), "a source must be able to record it unchanged"
    assert outcome.status == "abandoned"
    assert is_escalation(outcome)
    assert not is_escalation(TaskOutcome(status="failed", error="boom"))


def test_the_budget_is_validated(guarded):
    with pytest.raises(ValueError):
        guarded(max_attempts=0)
    with pytest.raises(ValueError):
        guarded(timeout_s=0)
    with pytest.raises(ValueError):
        guarded(backoff_s=-1)


# -----------------------------------------------------------------------------
# Retries
# -----------------------------------------------------------------------------


def test_retries_stop_at_max_attempts(guarded):
    source = FakeSource([claim()])
    calls: list[str] = []

    def worker(item: TaskClaim):
        calls.append(item.task_id)
        raise ConnectionError("upstream reset")

    runtime = guarded(max_attempts=4, backoff_s=0.5)
    outcomes = runtime.run_once(worker, source=source, limit=1)

    assert len(calls) == 4, "the budget is a ceiling, not a suggestion"
    assert guarded.sleeps == [0.5, 1.0, 2.0], "exponential backoff, one sleep per retry"
    assert len(outcomes) == 1

    escalation = outcomes[0]
    assert is_escalation(escalation)
    assert escalation.attempts == 4
    assert escalation.reason == "retries_exhausted"
    assert escalation.status == "abandoned"
    assert "upstream reset" in escalation.error
    assert source.completions[0][1] is escalation, "the source records the escalation itself"


def test_a_retryable_error_then_success_returns_the_worker_outcome(guarded):
    source = FakeSource([claim()])
    calls: list[int] = []

    def worker(item: TaskClaim) -> TaskOutcome:
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise TimeoutError("provider timed out")
        return TaskOutcome(status="completed", summary="report persisted", task_id=item.task_id)

    outcomes = guarded(max_attempts=3, backoff_s=0.25).run_once(worker, source=source, limit=1)

    assert len(calls) == 3
    assert guarded.sleeps == [0.25, 0.5]
    assert outcomes[0].status == "completed"
    assert not is_escalation(outcomes[0])
    assert source.completions[0][1].status == "completed"


def test_a_programming_error_is_not_retried(guarded):
    source = FakeSource([claim()])
    calls: list[int] = []

    def worker(item: TaskClaim):
        calls.append(1)
        raise ValueError("the payload was never going to validate")

    outcomes = guarded().run_once(worker, source=source, limit=1)

    assert len(calls) == 1, "a ValueError is a bug; a second call repeats it"
    assert guarded.sleeps == []
    assert outcomes[0].reason == "non_retryable"


def test_a_source_error_is_not_retried(guarded):
    source = FakeSource([claim()])
    calls: list[int] = []

    def worker(item: TaskClaim):
        calls.append(1)
        raise TaskSourceError("the source could not honour its contract")

    outcomes = guarded().run_once(worker, source=source, limit=1)
    assert len(calls) == 1
    assert outcomes[0].reason == "non_retryable"


# -----------------------------------------------------------------------------
# Timeout
# -----------------------------------------------------------------------------


def test_a_timeout_produces_an_escalation(guarded):
    release = threading.Event()
    source = FakeSource([claim()])
    started = threading.Event()

    def worker(item: TaskClaim):
        started.set()
        release.wait(5.0)
        return None

    runtime = guarded(max_attempts=2, timeout_s=0.05, backoff_s=0.0)
    try:
        outcomes = runtime.run_once(worker, source=source, limit=1)
    finally:
        release.set()  # let the abandoned threads finish, so nothing lingers

    assert started.is_set()
    assert len(outcomes) == 1
    escalation = outcomes[0]
    assert is_escalation(escalation)
    assert escalation.reason == "timeout"
    assert escalation.attempts == 2, "a timeout consumes an attempt like any other error"
    assert "TaskTimeout" in escalation.error
    assert source.completions[0][1] is escalation


# -----------------------------------------------------------------------------
# What must not be swallowed
# -----------------------------------------------------------------------------


def test_a_claim_conflict_propagates_and_the_worker_never_runs(guarded):
    source = FakeSource(claim_error=TaskClaimConflict("claim matched 2 rows; refusing the claim"))
    calls: list[int] = []

    def worker(item: TaskClaim):
        calls.append(1)
        return None

    with pytest.raises(TaskClaimConflict):
        guarded(max_attempts=5).run_once(worker, source=source, limit=1)

    assert calls == [], "contention is not failure: there is nothing to retry"
    assert source.claim_calls == 1


def test_a_worker_claim_conflict_is_re_raised_not_converted(guarded):
    """The inner runtime here lets exceptions out; the decorator must not turn it into data."""
    source = FakeSource([claim()])
    calls: list[int] = []

    def worker(item: TaskClaim):
        calls.append(1)
        raise TaskClaimConflict("a claim that is not backed by a commit")

    with pytest.raises(TaskClaimConflict):
        guarded(PropagatingRuntime(), max_attempts=5).run_once(worker, source=source, limit=1)

    assert len(calls) == 1, "a conflict must not be retried, and must not become an outcome"


# -----------------------------------------------------------------------------
# Visibility
# -----------------------------------------------------------------------------


def test_retries_and_the_escalation_land_in_the_run_summary(guarded):
    source = FakeSource([claim()])
    run = RunContext.begin(stage="runtime")

    def worker(item: TaskClaim):
        raise ConnectionError("upstream reset")

    guarded(max_attempts=3, run=run).run_once(worker, source=source, limit=1)

    summary = run.run_summary()
    assert summary["retries"] == 2
    assert summary["failures"] == 1
    assert summary["events_by_name"]["orchestration.task.retried"] == 2
    assert summary["failure_events"] == ["orchestration.task.escalated"]
    assert run.event_names()[-1] == "orchestration.task.escalated"


def test_a_worker_failure_never_logs_a_credential(caplog, guarded):
    import json
    import logging

    from observability.run import LOGGER_NAME

    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    source = FakeSource([claim()])
    run = RunContext.begin(stage="runtime")
    secret = "sk-live-abcdef1234567890"

    def worker(item: TaskClaim):
        raise ConnectionError(f"connect failed: postgresql://agent:pw@db/x?apikey={secret}")

    guarded(max_attempts=1, run=run).run_once(worker, source=source, limit=1)

    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert messages, "an escalation must be observable"
    assert all(secret not in message for message in messages)
    assert "***" in messages[-1]
    assert json.loads(messages[-1])["outcome"] == "escalated"
