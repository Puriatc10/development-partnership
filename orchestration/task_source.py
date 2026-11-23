"""A ``TaskSource`` over the deployed ``tasks`` table, and the manual runtime driver.

Claiming is a compare-and-swap, not a read-then-write
-----------------------------------------------------
The obvious implementation — ``SELECT ... WHERE status='pending' LIMIT n``
followed by ``UPDATE ... SET status='in_progress'`` — is a lost-update race: two
workers select the same row, both update it, both believe they own it, and the
task is executed twice. This source refuses that shape.

Instead every claim is a single conditional UPDATE, issued through
``PostgresClient.patch``::

    UPDATE tasks SET status = 'in_progress'
     WHERE id = ... AND status = 'pending' AND assigned_actor_identity = ...
    RETURNING *

The ``status = 'pending'`` predicate is the lock. PostgreSQL re-reads the row
under a row lock before applying the update, so exactly one caller can observe a
returned row; every later caller matches zero rows. A zero-row result is not an
error — it means another worker won — so it is never turned into a claim. The
selection query exists only to decide *which* rows are worth attempting, and it
is re-run after contention, so a page that overlaps with another worker still
fills up to ``limit``.

The failure mode is loud rather than quiet: if a client ever returns more than
one row for one id, or returns a row that is not ``in_progress`` for this
identity, ``TaskClaimConflict`` is raised instead of a claim being handed out.
A claim that is not backed by a committed status change is a bug, and running a
task twice is worse than failing a batch.

What the database permits is what happens
-----------------------------------------
RLS and the task triggers are the authority, and this module deliberately does
not second-guess them:

* agents may only see and update rows assigned to their own identity, so a
  cross-identity update matches no rows and becomes ``TaskSourceError``;
* ``fn_enforce_agent_task_transitions`` whitelists ``pending -> in_progress ->
  completed`` for agents, so an over-reaching ``transition`` raises the
  database's own exception (``42501`` from a policy, ``23514`` from a check,
  ``P0001`` from a governance trigger) and it propagates untouched;
* only a human operator may write ``cancelled`` or move a task backwards.

No ``except`` in this module converts a ``DatabaseError`` into a no-op. The one
status the source writes without asking is ``in_progress`` during a claim, and
that write is itself the claim.

Recording outcomes the schema cannot store
------------------------------------------
``task_status_type`` is ``{pending, in_progress, completed, cancelled}`` — there
is no ``failed`` or ``abandoned``, and protocol §5.1 records that the table has
no error or output column. So ``complete`` writes what the database can hold:

* ``completed`` -> ``status='completed'`` (the trigger fills ``completed_at``);
* ``cancelled`` -> ``status='cancelled'``, which only an operator may do; an
  agent's attempt is refused by the database and raises;
* ``failed`` / ``abandoned`` -> the lifecycle status is left untouched (an agent
  cannot write anything else, and a worker must not be able to invent a terminal
  state the operator never approved) and the outcome is appended to
  ``description``, the only field an agent may rewrite, as a single
  ``[task-outcome] {json}`` line.

That note is a stopgap, not telemetry: it is how a failure survives the process
that produced it until a schema change adds a real column. A task whose worker
failed therefore stays ``in_progress`` for an operator to cancel or reassign —
the protocol's escalation path, not a hidden success.

Why ``ManualRuntime`` lives here
--------------------------------
``contracts.py`` is the declaration surface: the module everyone imports, which
must stay free of drivers and of behaviour. A concrete driver is implementation,
so it belongs beside the source it drives, not in the contracts. Keeping both in
one module also keeps the seam reviewable as a pair; splitting one driver out is
indirection without a boundary.

The driver itself holds no business rules: one page of claims, one worker call
per claim, one recorded outcome per claim. Failures are isolated per task — a
worker that raises becomes a ``failed`` outcome, and a recording failure is
reported in the outcome rather than truncating the batch — so one bad task does
not cost the operator the rest of the page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from .contracts import (
    TaskClaim,
    TaskClaimConflict,
    TaskOutcome,
    TaskSource,
    TaskSourceError,
    Worker,
)

TASKS = "tasks"

#: Prefix of the outcome record appended to ``tasks.description``.
OUTCOME_NOTE_PREFIX = "[task-outcome]"

#: Prefix of a transition note appended to ``tasks.description``.
TRANSITION_NOTE_PREFIX = "[task-transition]"

_CLAIM_COLUMNS = (
    "id,company_id,task_type,priority,assigned_actor_identity,status,description,updated_at"
)
_DB_AGENT = "agent"


class TableClient(Protocol):
    """The slice of ``PostgresClient`` this module uses.

    Typed structurally so ``orchestration`` never imports ``integrations``
    (ADR-0001: orchestration may import contracts and agent entry points). The
    composition root constructs the client with the right identity and injects
    it; a stateful fake satisfies the same shape in tests.
    """

    def get(self, table: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def patch(
        self,
        table: str,
        data: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
        prefer: str = "",
    ) -> list[dict[str, Any]]: ...


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.now(timezone.utc)


def _outcome_note(outcome: TaskOutcome) -> str:
    payload: dict[str, Any] = {
        "status": outcome.status,
        "summary": outcome.summary,
        "artifacts": list(outcome.artifacts),
    }
    if outcome.error:
        payload["error"] = outcome.error
    if outcome.retry_after_s is not None:
        payload["retry_after_s"] = outcome.retry_after_s
    return f"{OUTCOME_NOTE_PREFIX} {json.dumps(payload, sort_keys=True)}"


class PostgresTaskSource:
    """``TaskSource`` implemented with the RLS-aware ``PostgresClient`` only."""

    def __init__(self, client: TableClient) -> None:
        self._client = client

    # -- TaskSource --------------------------------------------------------
    def claim(
        self, identity: str, *, limit: int = 1, task_types: Sequence[str] | None = None
    ) -> list[TaskClaim]:
        """Take up to ``limit`` pending tasks for ``identity``, atomically.

        Each task is taken by a conditional UPDATE; anything another worker took
        first is skipped, never returned twice.
        """
        if not identity:
            raise TaskSourceError("claim requires an identity")
        limit = int(limit)
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit!r}")

        claimed: list[TaskClaim] = []
        attempted: set[str] = set()
        while len(claimed) < limit:
            rows = self._pending_page(identity, limit - len(claimed), task_types)
            candidates = [row for row in rows if str(row["id"]) not in attempted]
            if not candidates:
                break
            progressed = False
            for row in candidates:
                task_id = str(row["id"])
                attempted.add(task_id)
                updated = self._client.patch(
                    TASKS,
                    {"status": "in_progress"},
                    params={
                        "id": f"eq.{task_id}",
                        "status": "eq.pending",
                        "assigned_actor_identity": f"eq.{identity}",
                    },
                )
                if not updated:
                    # Another worker moved this row out of 'pending' first. The
                    # UPDATE matched nothing, so nothing was written and nothing
                    # is claimed: this is contention, not an error.
                    continue
                claimed.append(self._claim_from(updated, identity))
                progressed = True
                if len(claimed) >= limit:
                    break
            if not progressed:
                # Every candidate on this page lost its race; a fresh read would
                # only see the same rows as taken, so stop rather than spin.
                break
        return claimed

    def transition(
        self, task_id: str, status: str, *, identity: str, detail: str = ""
    ) -> None:
        """Move one task to ``status``; the database's verdict is the verdict.

        A refused transition raises the ``DatabaseError`` that produced it, with
        its SQLSTATE intact (``42501`` policy, ``23514`` check, ``P0001``
        governance trigger). Nothing is caught or rewritten here.
        """
        if not status:
            raise ValueError("transition requires a target status")
        note = (
            f"{TRANSITION_NOTE_PREFIX} {json.dumps({'status': status, 'detail': detail})}"
            if detail
            else None
        )
        self._record(task_id, identity, status=status, note=note, expected_status=None)

    def complete(self, task_id: str, outcome: TaskOutcome, *, identity: str) -> None:
        """Record ``outcome`` for ``task_id`` as far as the schema allows."""
        note = _outcome_note(outcome)
        if outcome.status == "completed":
            self._record(task_id, identity, status="completed", note=note, expected_status="in_progress")
        elif outcome.status == "cancelled":
            # Only an operator may cancel; an agent's attempt is refused by the
            # transition whitelist and that refusal propagates by design.
            self._record(task_id, identity, status="cancelled", note=note, expected_status="in_progress")
        else:
            # failed / abandoned: no enum value exists and an agent may not write
            # any other status. Leave the lifecycle state alone, keep the record.
            self._record(task_id, identity, status=None, note=note, expected_status="in_progress")

    # -- internals ---------------------------------------------------------
    def _pending_page(
        self, identity: str, limit: int, task_types: Sequence[str] | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "select": _CLAIM_COLUMNS,
            # A runtime worker claims agent work, so an operator identity driving
            # the same source does not take an agent's queue by accident.
            "assigned_actor_type": f"eq.{_DB_AGENT}",
            "assigned_actor_identity": f"eq.{identity}",
            "status": "eq.pending",
            # priority is a PostgreSQL enum (low < medium < high < urgent), so
            # descending puts urgent first; oldest first within a priority.
            "order": "priority.desc,created_at.asc",
            "limit": str(limit),
        }
        if task_types:
            params["task_type"] = "in.(" + ",".join(str(t) for t in task_types) + ")"
        return self._client.get(TASKS, params)

    @staticmethod
    def _claim_from(rows: Sequence[Mapping[str, Any]], identity: str) -> TaskClaim:
        if len(rows) != 1:
            raise TaskClaimConflict(
                f"claim matched {len(rows)} rows; a claim must match exactly one. "
                "Refusing to hand out a claim that is not backed by a single committed update."
            )
        row = rows[0]
        returned_identity = _as_text(row.get("assigned_actor_identity"))
        if returned_identity != identity:
            raise TaskClaimConflict(
                f"claim returned a task assigned to {returned_identity!r}, not {identity!r}"
            )
        if row.get("status") != "in_progress":
            raise TaskClaimConflict(
                f"claim returned task {row.get('id')!r} in status {row.get('status')!r}, "
                "not 'in_progress': the update did not commit, refusing the claim"
            )
        return TaskClaim(
            task_id=str(row["id"]),
            company_id=_as_text(row.get("company_id")),
            task_type=str(row.get("task_type") or ""),
            priority=str(row.get("priority") or ""),
            assigned_identity=identity,
            # tasks has no retry counter (protocol §5.1) and an agent may not move a
            # task backwards, so the only claim round the database can produce is the
            # first. If a column is ever added, it must also be added to
            # _CLAIM_COLUMNS; until then this reports 1 rather than inventing history.
            attempt=int(row.get("attempt") or 1),
            claimed_at=_as_datetime(row.get("updated_at")),
        )

    def _record(
        self,
        task_id: str,
        identity: str,
        *,
        status: str | None,
        note: str | None,
        expected_status: str | None,
    ) -> dict[str, Any]:
        if not task_id:
            raise TaskSourceError("a task id is required")
        data: dict[str, Any] = {}
        if status is not None:
            data["status"] = status
        if note:
            data["description"] = self._description_with_note(task_id, note)
        if not data:
            raise TaskSourceError("nothing to record")

        # Scope is enforced by RLS, not by the WHERE clause: an agent's update of
        # a row it does not own matches nothing, and an operator's update of an
        # agent's task is legitimate (operators cancel and reopen). Filtering on
        # assigned_actor_identity here would also be filtering on the *actor*,
        # which is only the same person for a self-assigned claim.
        params: dict[str, Any] = {"id": f"eq.{task_id}"}
        if expected_status is not None:
            params["status"] = f"eq.{expected_status}"

        updated = self._client.patch(TASKS, data, params=params)
        if len(updated) != 1:
            raise TaskSourceError(
                f"update of task {task_id!r} as {identity!r} matched {len(updated)} rows"
                + (f" with status {expected_status!r}" if expected_status else "")
                + "; the row is not visible to this identity or is no longer in the expected state"
            )
        return updated[0]

    def _description_with_note(self, task_id: str, note: str) -> str:
        rows = self._client.get(TASKS, {"select": "id,description", "id": f"eq.{task_id}"})
        if len(rows) != 1:
            raise TaskSourceError(
                f"task {task_id!r} is not visible; refusing to write an unattributable note"
            )
        existing = str(rows[0].get("description") or "").rstrip()
        return f"{existing}\n{note}" if existing else note


@dataclass(frozen=True)
class ManualRuntime:
    """The supervised driver: one page of claims, one worker call per claim.

    ``identity`` is the worker identity carried by every claim, transition and
    outcome. ``task_types`` optionally narrows the page to the task types this
    worker handles; it is not a filter the worker can apply itself, because a
    claim it does not want has already been taken away from a worker that does.
    """

    identity: str
    task_types: Sequence[str] | None = None

    def run_once(self, worker: Worker, *, source: TaskSource, limit: int = 1) -> list[TaskOutcome]:
        claims = source.claim(self.identity, limit=limit, task_types=self.task_types)
        return [self._run_one(worker, source, claim) for claim in claims]

    def _run_one(self, worker: Worker, source: TaskSource, claim: TaskClaim) -> TaskOutcome:
        outcome = self._invoke(worker, claim)
        try:
            source.complete(claim.task_id, outcome, identity=self.identity)
        except Exception as exc:  # noqa: BLE001 - isolation is the point, detail goes in the outcome
            # The work happened; the record did not. Report both instead of
            # losing the batch: the caller sees the database refusal in the
            # returned outcome, and the task stays claimable by an operator.
            recorded_error = f"outcome not recorded: {type(exc).__name__}: {exc}"
            return replace(
                outcome,
                status="failed",
                error=f"{outcome.error}; {recorded_error}" if outcome.error else recorded_error,
            )
        return outcome

    @staticmethod
    def _invoke(worker: Worker, claim: TaskClaim) -> TaskOutcome:
        try:
            result = worker(claim)
        except Exception as exc:  # noqa: BLE001 - a worker failure is data, not a crash
            return TaskOutcome(
                task_id=claim.task_id,
                status="failed",
                summary=f"worker raised on {claim.task_type}",
                error=f"{type(exc).__name__}: {exc}",
            )
        if result is None:
            return TaskOutcome(task_id=claim.task_id, status="completed")
        if not isinstance(result, TaskOutcome):
            return TaskOutcome(
                task_id=claim.task_id,
                status="failed",
                summary="worker returned a non-TaskOutcome",
                error=f"expected TaskOutcome or None, got {type(result).__name__}",
            )
        if result.task_id and result.task_id != claim.task_id:
            return TaskOutcome(
                task_id=claim.task_id,
                status="failed",
                summary="worker returned an outcome for a different task",
                error=f"worker reported task {result.task_id!r}, claimed {claim.task_id!r}",
            )
        return result if result.task_id else replace(result, task_id=claim.task_id)
