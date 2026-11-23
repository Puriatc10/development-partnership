"""Runtime contracts: a task source, a runtime loop, and their value objects.

ADR-0001 splits orchestration from execution; §5.2 of
``agents/task-execution-protocol.md`` describes a runtime adapter layer that
sits between the database and the workers. This module is the code form of that
seam, and its most important property is what it does *not* contain:

**A Runtime holds no business rules: it claims, hands off, and records.** It
does not decide which tasks matter, whether a deliverable is good enough, which
stage comes next, or whether a gate may be passed. Those decisions live in
``orchestration/workflow.py`` (gates and stages) and in the database's own
governance triggers. A runtime that starts making those decisions has to be
rewritten every time a workflow changes — the exact coupling ADR-0001 exists to
prevent.

Two structural contracts make the seam substitutable:

``TaskSource``
    Where claims come from and where outcomes go. Implemented against the
    deployed ``tasks`` table in ``orchestration/task_source.py``; a test double
    or a different queue can satisfy the same shape.

``Runtime``
    The loop that turns a source plus a worker callable into outcomes.

Both are ``typing.Protocol`` — structural, not inherited — so a fake in a test
and a future autonomous driver are equally valid without a shared base class.

Nothing here imports a database driver or an HTTP client, so the contracts stay
usable from a CLI, a test, or an agent runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Protocol, Sequence, runtime_checkable

from contracts.errors import EryndexError

#: The only outcome statuses a worker may report. Deliberately *not* the same
#: vocabulary as the database's ``task_status_type`` enum (see ``TaskSource``).
OutcomeStatus = Literal["completed", "failed", "cancelled", "abandoned"]
OUTCOME_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "abandoned"})


class TaskSourceError(EryndexError):
    """The source could not honour the contract.

    Never raised for a policy decision — a refusal by the database arrives as
    ``integrations.postgres.client.DatabaseError`` carrying the SQLSTATE, so a
    caller can tell "the database said no" from "the source is broken".
    """


class TaskClaimConflict(TaskSourceError):
    """A claim did not resolve to exactly one row owned by the claimant.

    Raised instead of returning a claim that might be processed twice. A claim
    that is not backed by a committed status change is a bug, and processing a
    task twice is worse than failing a batch.
    """


@dataclass(frozen=True)
class TaskClaim:
    """One unit of claimed work.

    ``attempt`` is the claim round for this task. The deployed table has no
    retry counter (protocol §5.1) and an agent may not move a task backwards, so
    the database can only ever produce attempt 1; sources fill it from a row
    field when one exists, and otherwise report 1 rather than inventing history.
    """

    task_id: str
    company_id: str | None
    task_type: str
    priority: str
    assigned_identity: str
    attempt: int
    claimed_at: datetime

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("TaskClaim.task_id must be non-empty")
        if int(self.attempt) < 1:
            raise ValueError(f"TaskClaim.attempt must be >= 1, got {self.attempt!r}")


@dataclass(frozen=True)
class TaskOutcome:
    """What a worker produced for one claim.

    ``completed`` is the only status the agent transition whitelist permits a
    worker to write itself; the others describe how work ended and are recorded
    by the source as far as the database allows (see ``PostgresTaskSource``).

    ``task_id`` is not part of the worker-facing vocabulary, but a batch returns
    outcomes out of any log the operator can read, so the claim's id travels
    with the result. It defaults to empty and the runtime fills it in.
    """

    status: OutcomeStatus
    summary: str = ""
    artifacts: tuple[str, ...] = ()
    error: str = ""
    retry_after_s: int | None = None
    task_id: str = ""

    def __post_init__(self) -> None:
        if self.status not in OUTCOME_STATUSES:
            raise ValueError(
                f"TaskOutcome.status must be one of {sorted(OUTCOME_STATUSES)}, got {self.status!r}"
            )
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if self.retry_after_s is not None and int(self.retry_after_s) < 0:
            raise ValueError("TaskOutcome.retry_after_s must be >= 0")


#: A worker takes one claim and returns its outcome. Returning ``None`` means
#: "completed with no detail"; raising means "failed" and the runtime records it.
Worker = Callable[[TaskClaim], TaskOutcome | None]


@runtime_checkable
class TaskSource(Protocol):
    """Where claims come from and where outcomes go.

    Contract, in the order a runtime uses it:

    * ``claim`` — atomically take up to ``limit`` pending tasks assigned to
      ``identity``, optionally narrowed to ``task_types``. A task returned in
      the list has already been moved to ``in_progress`` for that identity; a
      task it could not take is never returned (no double-claim).
    * ``transition`` — move one task to ``status`` as ``identity`` with an
      optional ``detail``. Refusals by RLS or a constraint propagate as the
      underlying error; they are never converted into a no-op.
    * ``complete`` — record a ``TaskOutcome`` for one task as ``identity``.
    """

    def claim(
        self, identity: str, *, limit: int = 1, task_types: Sequence[str] | None = None
    ) -> list[TaskClaim]: ...

    def transition(self, task_id: str, status: str, *, identity: str, detail: str = "") -> None: ...

    def complete(self, task_id: str, outcome: TaskOutcome, *, identity: str) -> None: ...


@runtime_checkable
class Runtime(Protocol):
    """Claims one page and runs a worker once per claim."""

    def run_once(
        self, worker: Worker, *, source: TaskSource, limit: int = 1
    ) -> list[TaskOutcome]: ...
