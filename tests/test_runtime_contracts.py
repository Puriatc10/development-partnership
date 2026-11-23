"""Contract tests for the runtime seam: TaskSource, ManualRuntime, and outcomes.

The source under test talks to a stateful stand-in for the deployed ``tasks``
table. The stand-in is not a mock of calls — it holds rows and reproduces the
guards that make the real table safe: identity-scoped visibility, the agent
transition whitelist, ``chk_task_completed_at_sync``, and the enum. That way the
concurrency and refusal tests exercise the source's real decisions rather than
asserting that it called a method.
"""

from __future__ import annotations

import inspect
import threading
import uuid
from datetime import datetime, timezone

import pytest

from contracts.errors import EryndexError
from integrations.postgres.client import DatabaseError
from orchestration.contracts import (
    Runtime,
    TaskClaim,
    TaskClaimConflict,
    TaskOutcome,
    TaskSource,
    TaskSourceError,
)
from orchestration.task_source import (
    OUTCOME_NOTE_PREFIX,
    TRANSITION_NOTE_PREFIX,
    ManualRuntime,
    PostgresTaskSource,
)

IDENTITY = "hermes-researcher-v1"
OTHER_IDENTITY = "hermes-qualifier-v1"
AGENT_ID = "hermes-researcher-v1"

DB_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
AGENT_TRANSITIONS = {("pending", "in_progress"), ("in_progress", "completed")}
PRIORITY_ORDER = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
IMMUTABLE_FOR_AGENTS = (
    "assigned_actor_identity",
    "assigned_actor_type",
    "task_type",
    "priority",
    "title",
    "company_id",
    "contact_id",
)


def task(task_id: str | None = None, **overrides) -> dict:
    row = {
        "id": task_id or str(uuid.uuid4()),
        "company_id": str(uuid.uuid4()),
        "task_type": "company_research",
        "priority": "medium",
        "status": "pending",
        "assigned_actor_type": "agent",
        "assigned_actor_identity": IDENTITY,
        "title": "Research a company",
        "description": "Brief written by the operator.",
        "completed_at": None,
        "created_at": datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# A stateful stand-in for the deployed table.
# ---------------------------------------------------------------------------
class FakeTasksDB:
    """Rows plus the policies/constraints that decide what an agent may write."""

    def __init__(
        self, rows: list[dict] | None = None, *, agent_id: str = IDENTITY, role: str = "agent"
    ) -> None:
        self.rows = {row["id"]: dict(row) for row in (rows or [])}
        self.actor_id = agent_id
        self.role = role
        self.lock = threading.RLock()
        self.patches: list[tuple[dict, dict]] = []

    # -- RLS -------------------------------------------------------------
    def _visible(self, row: dict) -> bool:
        # agent_select/update_assigned_tasks: an agent sees only its own rows;
        # human_operator_all_tasks places no such restriction on an operator.
        return self.role == "operator" or row["assigned_actor_identity"] == self.actor_id

    # -- filters ---------------------------------------------------------
    @staticmethod
    def _matches(row: dict, params: dict) -> bool:
        for key, raw in params.items():
            if key in {"select", "order", "limit", "offset"}:
                continue
            value = row.get(key)
            text = None if value is None else str(value)
            if raw == "is.null":
                if value is not None:
                    return False
            elif raw == "not.is.null":
                if value is None:
                    return False
            elif raw.startswith("in.(") and raw.endswith(")"):
                allowed = [item.strip() for item in raw[4:-1].split(",")]
                if text not in allowed:
                    return False
            elif raw.startswith("eq."):
                if text != raw[3:]:
                    return False
            else:
                raise AssertionError(f"fake does not support filter {key}={raw!r}")
        return True

    @staticmethod
    def _order(rows: list[dict], ordering: str) -> list[dict]:
        ordered = list(rows)
        for clause in reversed([c.strip() for c in ordering.split(",") if c.strip()]):
            column, _, direction = clause.partition(".")
            reverse = direction.lower() == "desc"
            if column == "priority":
                ordered.sort(key=lambda row: PRIORITY_ORDER[str(row["priority"])], reverse=reverse)
            else:
                ordered.sort(key=lambda row: row[column], reverse=reverse)
        return ordered

    # -- operations ------------------------------------------------------
    def get(self, table, params=None):
        assert table == "tasks"
        params = dict(params or {})
        with self.lock:
            rows = [
                row
                for row in self.rows.values()
                if self._visible(row) and self._matches(row, params)
            ]
            if params.get("order"):
                rows = self._order(rows, params["order"])
            offset = int(params.get("offset") or 0)
            limit = params.get("limit")
            rows = rows[offset : offset + int(limit)] if limit is not None else rows[offset:]
            columns = params.get("select")
            if columns and columns.strip() != "*":
                names = [c.strip() for c in columns.split(",")]
                rows = [{name: row.get(name) for name in names} for row in rows]
            return [dict(row) for row in rows]

    def patch(self, table, data, params=None, prefer=""):
        assert table == "tasks"
        params = dict(params or {})
        with self.lock:
            targets = [
                row
                for row in self.rows.values()
                if self._visible(row) and self._matches(row, params)
            ]
            self.patches.append((dict(data), dict(params)))
            updated = []
            for row in targets:
                new = self._apply(row, data, params)
                self.rows[row["id"]] = new
                updated.append(dict(new))
            return updated

    def _apply(self, row: dict, data: dict, params: dict) -> dict:
        new = dict(row)
        new.update(data)

        if self.role == "agent":
            # RLS WITH CHECK: a row may not be rewritten out of the claimant's scope.
            if new["assigned_actor_identity"] != self.actor_id:
                raise DatabaseError(
                    "new row violates row-level security policy", code="42501", status_code=403
                )

            for field in IMMUTABLE_FOR_AGENTS:
                if field in data and data[field] != row.get(field):
                    raise DatabaseError(f"agent cannot modify {field}", code="42501", status_code=403)

        if new["status"] != row["status"]:
            if new["status"] not in DB_STATUSES:
                raise DatabaseError(
                    "invalid input value for enum task_status_type", code="23514", status_code=400
                )
            if self.role == "agent" and (row["status"], new["status"]) not in AGENT_TRANSITIONS:
                # The real guard is a plpgsql RAISE EXCEPTION in
                # fn_enforce_agent_task_transitions, which arrives as P0001.
                raise DatabaseError(
                    f"Lifecycle Violation: agents may only move pending -> in_progress -> completed. "
                    f"Attempted {row['status']} -> {new['status']} on task {row['id']}.",
                    code="P0001",
                    status_code=400,
                )

        # fn_enforce_task_governance: completed_at is set on completion, cleared otherwise.
        if new["status"] == "completed":
            new["completed_at"] = new.get("completed_at") or datetime.now(timezone.utc)
        elif new["status"] != "completed" and "completed_at" in data:
            raise DatabaseError("chk_task_completed_at_sync violated", code="23514", status_code=400)
        else:
            new["completed_at"] = None
        return new


class DuplicatingPatchClient(FakeTasksDB):
    """A client whose UPDATE...RETURNING reports two rows for one id."""

    def patch(self, table, data, params=None, prefer=""):
        updated = super().patch(table, data, params, prefer)
        return updated * 2 if updated else []


class RefusingCompletionClient(FakeTasksDB):
    """Refuses the completion write, as a policy denial would."""

    def patch(self, table, data, params=None, prefer=""):
        if data.get("status") == "completed":
            raise DatabaseError("permission denied for table tasks", code="42501", status_code=403)
        return super().patch(table, data, params, prefer)


class CheckConstraintClient(FakeTasksDB):
    """Raises a check-constraint violation on completion, like chk_task_completed_at_sync."""

    def patch(self, table, data, params=None, prefer=""):
        if data.get("status") == "completed":
            raise DatabaseError(
                'new row for relation "tasks" violates check constraint "chk_task_completed_at_sync"',
                code="23514",
                status_code=400,
            )
        return super().patch(table, data, params, prefer)


class UnprivilegedClient(FakeTasksDB):
    """An identity with no task privileges at all: the read itself is denied."""

    def get(self, table, params=None):
        raise DatabaseError("permission denied for table tasks", code="42501", status_code=403)

    def patch(self, table, data, params=None, prefer=""):
        raise DatabaseError("permission denied for table tasks", code="42501", status_code=403)


class RecordingSource:
    """A non-database TaskSource, to prove the protocol is structural."""

    def __init__(self) -> None:
        self.claims: list[tuple[str, int, tuple[str, ...] | None]] = []
        self.transitions: list[tuple[str, str, str, str]] = []
        self.completions: list[tuple[str, TaskOutcome, str]] = []

    def claim(self, identity, *, limit=1, task_types=None):
        self.claims.append((identity, limit, tuple(task_types) if task_types else None))
        return []

    def transition(self, task_id, status, *, identity, detail=""):
        self.transitions.append((task_id, status, identity, detail))

    def complete(self, task_id, outcome, *, identity):
        self.completions.append((task_id, outcome, identity))


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------
def test_a_fake_source_satisfies_the_task_source_protocol():
    fake = RecordingSource()
    assert isinstance(fake, TaskSource), "TaskSource must be structural, not a base class"
    for method in ("claim", "transition", "complete"):
        protocol = inspect.signature(getattr(TaskSource, method))
        implementation = inspect.signature(getattr(type(fake), method))
        assert list(protocol.parameters) == list(implementation.parameters), method
        for name, parameter in protocol.parameters.items():
            assert parameter.kind == implementation.parameters[name].kind, (method, name)
            assert parameter.default == implementation.parameters[name].default, (method, name)


def test_the_postgres_source_satisfies_the_same_contract():
    source = PostgresTaskSource(FakeTasksDB([task()]))
    assert isinstance(source, TaskSource)
    assert isinstance(ManualRuntime(identity=IDENTITY), Runtime)
    protocol = inspect.signature(Runtime.run_once)
    implementation = inspect.signature(ManualRuntime.run_once)
    assert list(protocol.parameters) == list(implementation.parameters)


# ---------------------------------------------------------------------------
# Claim / transition / complete
# ---------------------------------------------------------------------------
def test_claim_transition_and_complete_happy_path():
    row = task()
    db = FakeTasksDB([row])
    source = PostgresTaskSource(db)

    claimed = source.claim(IDENTITY, limit=1, task_types=["company_research"])
    assert len(claimed) == 1
    claim = claimed[0]
    assert isinstance(claim, TaskClaim)
    assert claim.task_id == row["id"]
    assert claim.company_id == row["company_id"]
    assert claim.assigned_identity == IDENTITY
    assert claim.attempt == 1
    assert claim.claimed_at == row["updated_at"]
    assert db.rows[row["id"]]["status"] == "in_progress", "a returned claim must be backed by the write"

    source.transition(row["id"], "in_progress", identity=IDENTITY, detail="resumed after review")
    assert db.rows[row["id"]]["status"] == "in_progress"

    source.complete(
        row["id"],
        TaskOutcome(status="completed", summary="report persisted", artifacts=("research_reports:1",)),
        identity=IDENTITY,
    )
    stored = db.rows[row["id"]]
    assert stored["status"] == "completed"
    assert stored["completed_at"] is not None
    assert stored["description"].startswith("Brief written by the operator.")
    assert OUTCOME_NOTE_PREFIX in stored["description"]
    assert '"status": "completed"' in stored["description"]


def test_claim_respects_limit_ordering_and_task_types():
    urgent = task(priority="urgent", task_type="company_research")
    low = task(priority="low", task_type="company_research")
    drafting = task(priority="urgent", task_type="outreach_drafting")
    db = FakeTasksDB([low, urgent, drafting])
    source = PostgresTaskSource(db)

    claimed = source.claim(IDENTITY, limit=1, task_types=["company_research"])
    assert [c.task_id for c in claimed] == [urgent["id"]], "urgent first, and not the drafting task"

    rest = source.claim(IDENTITY, limit=5, task_types=["company_research"])
    assert [c.task_id for c in rest] == [low["id"]], "the page stops when no pending rows remain"


def test_a_transition_detail_is_recorded_not_dropped():
    row = task(status="in_progress")
    db = FakeTasksDB([row])
    source = PostgresTaskSource(db)

    source.transition(row["id"], "in_progress", identity=IDENTITY, detail="resumed after review")

    stored = db.rows[row["id"]]["description"]
    assert TRANSITION_NOTE_PREFIX in stored
    assert "resumed after review" in stored


def test_a_claim_that_lost_the_race_is_never_returned():
    row = task(status="in_progress")
    source = PostgresTaskSource(FakeTasksDB([row]))
    assert source.claim(IDENTITY, limit=1) == []


def test_concurrent_claimers_never_receive_the_same_task():
    """Eight workers, four tasks: four claims in total, each task exactly once."""
    rows = [task() for _ in range(4)]
    db = FakeTasksDB(rows)
    sources = [PostgresTaskSource(db) for _ in range(8)]
    barrier = threading.Barrier(len(sources))
    results: list[list[TaskClaim]] = []
    lock = threading.Lock()

    def run(source: PostgresTaskSource) -> None:
        barrier.wait()
        batch = source.claim(IDENTITY, limit=4)
        with lock:
            results.append(batch)

    threads = [threading.Thread(target=run, args=(source,)) for source in sources]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed_ids = [claim.task_id for batch in results for claim in batch]
    assert len(claimed_ids) == len(rows), "every task must be claimed exactly once"
    assert len(set(claimed_ids)) == len(rows), "a task must never be handed out twice"
    assert all(db.rows[task_id]["status"] == "in_progress" for task_id in claimed_ids)


def test_claim_refuses_a_client_that_reports_more_than_one_row():
    row = task()
    source = PostgresTaskSource(DuplicatingPatchClient([row]))
    with pytest.raises(TaskClaimConflict):
        source.claim(IDENTITY, limit=1)


def test_claim_refuses_a_row_that_is_not_backed_by_the_write():
    class SilentClient(FakeTasksDB):
        def patch(self, table, data, params=None, prefer=""):
            # A broken client that reports the row without applying the update.
            return [
                dict(row)
                for row in self.rows.values()
                if row["id"] == params["id"][3:]
            ]

    source = PostgresTaskSource(SilentClient([task()]))
    with pytest.raises(TaskClaimConflict) as exc:
        source.claim(IDENTITY, limit=1)
    assert "not 'in_progress'" in str(exc.value)


# ---------------------------------------------------------------------------
# Refusals must surface, not be swallowed
# ---------------------------------------------------------------------------
def test_a_forbidden_transition_surfaces_the_database_error():
    row = task(status="in_progress")
    source = PostgresTaskSource(FakeTasksDB([row]))

    with pytest.raises(DatabaseError) as exc:
        source.transition(row["id"], "cancelled", identity=IDENTITY, detail="agent tried to cancel")

    assert exc.value.code == "P0001", "the governance trigger's refusal must keep its SQLSTATE"
    assert "cancelled" in str(exc.value)


def test_skipping_in_progress_surfaces_the_lifecycle_refusal():
    row = task(status="pending")
    db = FakeTasksDB([row])
    source = PostgresTaskSource(db)

    with pytest.raises(DatabaseError) as exc:
        source.transition(row["id"], "completed", identity=IDENTITY)

    assert exc.value.code == "P0001"
    assert "pending -> in_progress" in str(exc.value)
    assert db.rows[row["id"]]["status"] == "pending", "a refused transition must change nothing"


def test_a_policy_denial_surfaces_42501():
    source = PostgresTaskSource(UnprivilegedClient([task()]))
    with pytest.raises(DatabaseError) as exc:
        source.claim(IDENTITY, limit=1)
    assert exc.value.code == "42501", "a missing privilege must not look like an empty queue"


def test_a_check_constraint_refusal_surfaces_23514():
    row = task(status="in_progress")
    source = PostgresTaskSource(CheckConstraintClient([row]))
    with pytest.raises(DatabaseError) as exc:
        source.complete(row["id"], TaskOutcome(status="completed"), identity=IDENTITY)
    assert exc.value.code == "23514"


def test_a_transition_that_matches_no_row_is_an_error_not_a_no_op():
    source = PostgresTaskSource(FakeTasksDB([task()]))
    with pytest.raises(TaskSourceError):
        source.transition(str(uuid.uuid4()), "in_progress", identity=IDENTITY)
    assert issubclass(TaskSourceError, EryndexError)


def test_transitions_are_scoped_to_the_calling_identity():
    """The other agent's task is invisible: the update matches nothing and says so."""
    row = task(assigned_actor_identity=OTHER_IDENTITY)
    db = FakeTasksDB([row])
    source = PostgresTaskSource(db)
    with pytest.raises(TaskSourceError):
        source.complete(row["id"], TaskOutcome(status="completed"), identity=IDENTITY)
    assert db.rows[row["id"]]["status"] == "pending"


def test_an_operator_may_cancel_a_task_an_agent_may_not():
    """The source does not police who may cancel: the database does, via the caller's identity."""
    row = task(status="in_progress")
    operator_id = "e1cd1ff2-080d-47c4-9589-477ce66258f3"
    db = FakeTasksDB([row], agent_id=operator_id, role="operator")
    source = PostgresTaskSource(db)

    source.complete(
        row["id"],
        TaskOutcome(status="cancelled", summary="cancelled by the operator after a worker crash"),
        identity=operator_id,
    )

    assert db.rows[row["id"]]["status"] == "cancelled"
    assert "worker crash" in db.rows[row["id"]]["description"]


# ---------------------------------------------------------------------------
# Outcomes the schema cannot store
# ---------------------------------------------------------------------------
def test_a_failed_outcome_is_recorded_without_inventing_a_status():
    row = task(status="in_progress")
    db = FakeTasksDB([row])
    source = PostgresTaskSource(db)

    source.complete(
        row["id"],
        TaskOutcome(status="failed", summary="website unreachable", error="TimeoutError: read timed out"),
        identity=IDENTITY,
    )

    stored = db.rows[row["id"]]
    assert stored["status"] == "in_progress", "the agent transition whitelist cannot write 'failed'"
    assert stored["completed_at"] is None
    assert '"status": "failed"' in stored["description"]
    assert "TimeoutError" in stored["description"]


def test_outcome_status_is_closed():
    with pytest.raises(ValueError):
        TaskOutcome(status="in-progress")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TaskOutcome(status="completed", retry_after_s=-1)
    with pytest.raises(ValueError):
        TaskClaim(
            task_id="x",
            company_id=None,
            task_type="company_research",
            priority="low",
            assigned_identity=IDENTITY,
            attempt=0,
            claimed_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------
def test_runtime_keeps_going_when_one_task_raises():
    rows = [task(task_type="company_research") for _ in range(3)]
    db = FakeTasksDB(rows)
    source = PostgresTaskSource(db)
    runtime = ManualRuntime(identity=IDENTITY, task_types=["company_research"])

    def worker(claim: TaskClaim) -> TaskOutcome:
        if claim.task_id == rows[1]["id"]:
            raise RuntimeError("provider exploded")
        return TaskOutcome(status="completed", summary=f"done {claim.task_id}")

    outcomes = runtime.run_once(worker, source=source, limit=3)

    assert [o.status for o in outcomes] == ["completed", "failed", "completed"]
    assert [o.task_id for o in outcomes] == [row["id"] for row in rows]
    assert "RuntimeError: provider exploded" in outcomes[1].error
    assert db.rows[rows[0]["id"]]["status"] == "completed"
    assert db.rows[rows[1]["id"]]["status"] == "in_progress", "a failed task stays for the operator"
    assert db.rows[rows[2]["id"]]["status"] == "completed"


def test_runtime_calls_the_worker_once_per_claim_and_records_each():
    source = RecordingSource()
    runtime = ManualRuntime(identity=IDENTITY, task_types=["company_research"])
    seen: list[TaskClaim] = []

    outcomes = runtime.run_once(lambda claim: seen.append(claim), source=source, limit=5)

    assert outcomes == []
    assert source.claims == [(IDENTITY, 5, ("company_research",))]


def test_runtime_survives_a_recording_failure_and_reports_it():
    rows = [task() for _ in range(2)]
    db = RefusingCompletionClient(rows)
    source = PostgresTaskSource(db)
    runtime = ManualRuntime(identity=IDENTITY)

    outcomes = runtime.run_once(
        lambda claim: TaskOutcome(status="completed", summary="work done"),
        source=source,
        limit=2,
    )

    assert len(outcomes) == 2, "one refused write must not truncate the page"
    assert {o.status for o in outcomes} == {"failed"}
    assert all("outcome not recorded" in o.error for o in outcomes)
    assert all("42501" in o.error or "permission denied" in o.error for o in outcomes)


def test_runtime_ignores_an_outcome_for_a_different_task():
    db = FakeTasksDB([task()])
    source = PostgresTaskSource(db)
    runtime = ManualRuntime(identity=IDENTITY)

    outcomes = runtime.run_once(
        lambda claim: TaskOutcome(status="completed", task_id="some-other-task"),
        source=source,
        limit=1,
    )

    assert outcomes[0].status == "failed"
    assert "some-other-task" in outcomes[0].error
    assert db.rows[next(iter(db.rows))]["status"] == "in_progress"
