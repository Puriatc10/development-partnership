"""Bounded retries and a per-task timeout, wrapped around any ``Runtime``.

Why this is a decorator and not a new runtime
---------------------------------------------
``ManualRuntime`` already owns the parts that must not be duplicated: it claims
a page, calls the worker once per claim, and records one outcome per claim. A
retry loop that claims again would take a second task instead of retrying the
first, and one that records outcomes itself would disagree with the runtime
about who owns the write.

So this wraps the *worker*, not the source. ``run_once`` hands the inner runtime
a guarded callable; the inner runtime still claims, still records, and still
isolates a failing task. Everything this module adds happens inside a single
worker call:

* a per-call timeout, so one wedged task cannot hold the page forever;
* bounded retries for an error that is worth another attempt;
* an :class:`Escalation` outcome once the budget is spent, which is a task a
  human has to look at — not a silent success and not an infinite loop.

What is *not* retried
---------------------
``TaskClaimConflict`` is contention, not failure: the claim that lost a race is
the source's business, and retrying a claim whose row another worker already
owns is how one task gets processed twice. It is re-raised untouched, as is
every other ``TaskSourceError`` (a source that could not honour its contract
will not honour it on the second call either) and the programming errors a
retry cannot fix (``ValueError``/``TypeError``/``AssertionError``/
``NotImplementedError``). A worker that *returns* an outcome is also taken at
its word — it already decided what happened; retrying a decision is not this
decorator's job.

This decorator adds no swallowing of its own. Note that ``ManualRuntime`` — if it
is the wrapped runtime — already turns any worker exception into a ``failed``
outcome, by its own documented isolation policy; wrapping a runtime that
isolates a conflict still isolates it there. What this module guarantees is that
*it* does not: a conflict raised by the claim, or by a worker under a runtime
that lets exceptions out, leaves ``run_once`` as a ``TaskClaimConflict``.

The timeout mechanism, and its cost
-----------------------------------
The worker runs in a daemon thread and the caller waits on it for ``timeout_s``.
No signals (``SIGALRM`` does not exist on Windows and does not interrupt a
blocking C call on Linux), no ``asyncio`` (a worker is a sync callable and the
rest of the runtime is sync).

The honest limitation: Python cannot kill a thread. A worker that ignores its
timeout keeps running in the background until it returns, and its result is
discarded. Daemon threads mean such a straggler cannot block process exit, and
the number of them is bounded by ``max_attempts`` per claimed task. So the
timeout bounds *how long the run waits*, not how long a hostile callable burns
CPU. That is the trade this platform can pay; a worker that needs a hard kill
needs a process, not a thread.

Backoff
-------
``backoff_s * 2 ** (attempt - 1)``: the first retry waits ``backoff_s``, the
second twice that, and so on. ``clock`` and ``sleep`` are injected so tests
exercise the schedule without waiting for it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from observability.run import (
    OUTCOME_ESCALATED,
    OUTCOME_RETRY,
    RunContext,
    redact,
)

from .contracts import (
    Runtime,
    TaskClaim,
    TaskClaimConflict,
    TaskOutcome,
    TaskSource,
    TaskSourceError,
    Worker,
)

#: Status an escalation reports. ``abandoned`` is the deployed vocabulary's word
#: for "the run gave up"; the schema has no ``failed``/``escalated`` value, and
#: an agent may not invent a terminal state (see ``PostgresTaskSource.complete``).
ESCALATION_STATUS = "abandoned"

#: Errors a retry cannot fix. ``TaskClaimConflict`` is listed first because it is
#: the one that must be *propagated*, not converted into an outcome.
NON_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    TaskClaimConflict,
    TaskSourceError,
    ValueError,
    TypeError,
    AssertionError,
    NotImplementedError,
)

#: Base exceptions that must never be turned into data.
_PROPAGATING = (KeyboardInterrupt, SystemExit, GeneratorExit)


class TaskTimeout(TimeoutError):
    """The worker did not return within ``timeout_s``; it was abandoned, not killed."""


@dataclass(frozen=True)
class Escalation(TaskOutcome):
    """The retry budget is spent: a human has to look at this task.

    A ``TaskOutcome`` subclass, so it can be recorded by any ``TaskSource`` and
    returned by any ``Runtime`` without a new contract. ``reason`` is a short
    word (``timeout`` / ``retries_exhausted`` / ``non_retryable``) and ``attempts``
    is how many worker calls were made.
    """

    attempts: int = 0
    reason: str = ""

    @property
    def escalated(self) -> bool:
        return True


def is_escalation(outcome: Any) -> bool:
    """True for an :class:`Escalation` (including one a caller reconstructed)."""
    return isinstance(outcome, Escalation) or (
        isinstance(outcome, TaskOutcome) and bool(getattr(outcome, "escalated", False))
    )


class RetryingRuntime:
    """A ``Runtime`` that delegates to another one, hardening each worker call.

    ``source`` is the wrapped runtime — any object with the ``Runtime.run_once``
    shape (``ManualRuntime`` today, a fake in tests) and the thing outcomes come
    *from*. It is called with the same ``source``/``limit`` arguments it was
    given, so claim and record behaviour is unchanged.
    """

    def __init__(
        self,
        source: Runtime,
        max_attempts: int = 3,
        timeout_s: float = 60.0,
        backoff_s: float = 1.0,
        *,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
        run: RunContext | None = None,
    ) -> None:
        if int(max_attempts) < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts!r}")
        if timeout_s is None or float(timeout_s) <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if float(backoff_s) < 0:
            raise ValueError(f"backoff_s must be >= 0, got {backoff_s!r}")
        self._inner = source
        self.max_attempts = int(max_attempts)
        self.timeout_s = float(timeout_s)
        self.backoff_s = float(backoff_s)
        self._clock = clock
        self._sleep = sleep
        self.run = run

    # -- Runtime -----------------------------------------------------------
    def run_once(
        self, worker: Worker, *, source: TaskSource, limit: int = 1
    ) -> list[TaskOutcome]:
        """Claim and run one page, with every worker call guarded."""
        return self._inner.run_once(self._guarded(worker), source=source, limit=limit)

    # -- internals ---------------------------------------------------------
    def _guarded(self, worker: Worker) -> Worker:
        def guarded(claim: TaskClaim) -> TaskOutcome:
            return self._attempt_loop(worker, claim)

        return guarded

    def _attempt_loop(self, worker: Worker, claim: TaskClaim) -> TaskOutcome:
        started = self._clock()

        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._call(worker, claim)
            except TaskClaimConflict:
                # Contention, not failure. The source owns the decision; retrying
                # a claim another worker already owns is how a task runs twice.
                raise
            except _PROPAGATING:
                raise
            except BaseException as exc:  # noqa: BLE001 - classified immediately below
                retryable = not isinstance(exc, NON_RETRYABLE_ERRORS)
                exhausted = attempt >= self.max_attempts
                if not retryable or exhausted:
                    reason = "non_retryable" if not retryable else _reason(exc)
                    return self._escalate(claim, exc, attempt, reason, started)
                self._emit(
                    "orchestration.task.retried",
                    stage="runtime",
                    outcome=OUTCOME_RETRY,
                    counts={"retries": 1},
                    duration_ms=_ms(self._clock() - started),
                    task_id=claim.task_id,
                    task_type=claim.task_type,
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    error=redact(f"{type(exc).__name__}: {exc}"),
                )
                self._sleep(self._delay(attempt))

        # Unreachable: the loop returns on the last attempt. Kept so a future edit
        # that drops the `exhausted` branch fails loudly instead of silently
        # returning None.
        raise AssertionError("retry loop exited without an outcome")  # pragma: no cover

    def _escalate(
        self,
        claim: TaskClaim,
        exc: BaseException | None,
        attempts: int,
        reason: str,
        started: float,
    ) -> Escalation:
        error = "<unknown error>" if exc is None else redact(f"{type(exc).__name__}: {exc}")
        outcome = Escalation(
            status=ESCALATION_STATUS,
            summary=(
                f"escalation after {attempts} of {self.max_attempts} attempts "
                f"({reason}); a human must review this task"
            ),
            error=error,
            task_id=claim.task_id,
            attempts=attempts,
            reason=reason,
        )
        self._emit(
            "orchestration.task.escalated",
            stage="runtime",
            outcome=OUTCOME_ESCALATED,
            counts={"attempts": attempts},
            duration_ms=_ms(self._clock() - started),
            task_id=claim.task_id,
            task_type=claim.task_type,
            reason=reason,
            max_attempts=self.max_attempts,
            error=error,
        )
        return outcome

    def _call(self, worker: Worker, claim: TaskClaim) -> TaskOutcome:
        return _call_with_timeout(worker, claim, self.timeout_s)

    def _delay(self, attempt: int) -> float:
        return self.backoff_s * (2 ** (attempt - 1))

    def _emit(self, name: str, **fields: Any) -> None:
        if self.run is not None:
            self.run.structured_event(name, **fields)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"RetryingRuntime({type(self._inner).__name__}, max_attempts={self.max_attempts}, "
            f"timeout_s={self.timeout_s}, backoff_s={self.backoff_s})"
        )


def _reason(exc: BaseException) -> str:
    return "timeout" if isinstance(exc, TimeoutError) else "retries_exhausted"


def _ms(seconds: float) -> float:
    return seconds * 1000.0


def _call_with_timeout(worker: Worker, claim: TaskClaim, timeout_s: float) -> TaskOutcome:
    """Run ``worker(claim)`` in a daemon thread; give up after ``timeout_s``.

    The worker's exception is re-raised in the caller's thread so the retry
    classification sees the real type, not a wrapper. A thread that is still
    running when the timeout fires is abandoned (module docstring: the wait is
    bounded, not the call).
    """
    box: dict[str, Any] = {}
    finished = threading.Event()

    def target() -> None:
        try:
            box["outcome"] = worker(claim)
        except BaseException as exc:  # noqa: BLE001 - handed back to the calling thread
            box["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(target=target, name=f"worker-{claim.task_id[:8]}", daemon=True)
    thread.start()
    if not finished.wait(timeout_s):
        raise TaskTimeout(
            f"worker for task {claim.task_id} did not return within {timeout_s}s"
        )

    error = box.get("error")
    if error is not None:
        raise error
    return box["outcome"]


__all__ = [
    "ESCALATION_STATUS",
    "NON_RETRYABLE_ERRORS",
    "Escalation",
    "RetryingRuntime",
    "TaskTimeout",
    "is_escalation",
]
