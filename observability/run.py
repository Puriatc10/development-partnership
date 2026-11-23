"""One run, one id, one line of JSON per step — no observability stack.

The two questions this answers
------------------------------
An operator reads ``tasks.description`` and ``activities`` after the fact and
has to reconstruct a run from them. That is enough to see *what* landed and not
enough to answer either of the questions that actually come up:

* *Which run produced this report?* — nothing carried a run identifier, so two
  attempts at the same company were indistinguishable in the logs;
* *Why did this task fail?* — the failure was a stack trace on stderr with no
  task, no stage and no duration attached, so correlating it meant reading
  timestamps.

A run id and a trace id fix the first. One JSON object per step — with the
stage, the outcome, the counts and the duration in fixed fields — fixes the
second without a tracing backend, a metrics agent or a new dependency:
``json.loads`` on a log line is the whole query language.

Run id vs trace id
------------------
``run_id`` is fresh per invocation (``uuid4``) and identical on every event that
invocation emits. ``trace_id`` is the id the *work* is followed by: a first
attempt uses its own ``run_id`` as the trace id, and a retry passes that id back
(:class:`RunContext.begin` ``trace_id=...``, ``scripts/research_run.py
--run-id``) so both attempts appear under one trace while staying tellable
apart. Reusing a trace can never merge two runs' ids, and it never reuses a
``run_id`` — that would make "which run" ambiguous again.

The field set
-------------
Every record carries exactly::

    event, run_id, trace_id, stage, outcome, counts, duration_ms

``counts`` is always an object of integers, ``duration_ms`` a number or null,
``outcome`` a word from :data:`OUTCOME_OK` and friends. Extra keyword arguments
(``report_id``, ``task_id``, ``error``, ...) are appended as top-level keys, so
the seven required fields keep their meaning while a specific event can still be
specific. Callers may not overwrite the seven.

Everything is redacted through :func:`observability.redaction.redact_fields`
before it is serialised, so a credential in an exception message cannot reach
the log — including when the event is a failure, which is exactly when a
provider error carrying a request URL shows up.

Log line format
---------------
The logger is :data:`LOGGER_NAME` (``eryndex.run``), level ``INFO``, one record
per event, message = compact sorted-key JSON. Attach a handler with
``format="%(message)s"`` (``scripts/research_run.py`` does) and an operator
sees::

    {"counts":{"findings":3,"written":3},"duration_ms":12.4,"event":"research.findings.persisted",
     "outcome":"ok","run_id":"9f0c...","stage":"persist","trace_id":"4b2e..."}

Run summary
-----------
:meth:`RunContext.run_summary` returns a machine-readable dict (counts summed
across events, event tally, retries, failures, durations, stages) suitable for
writing into a report file. It is derived from the events that were actually
emitted, so it cannot disagree with the log.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from .redaction import redact, redact_fields

#: Logger every event is written through; attach a ``%(message)s`` handler to it.
LOGGER_NAME = "eryndex.run"

#: The fixed field set of every record, in no particular order (keys are sorted
#: on the wire). A caller may not add a field with one of these names.
EVENT_FIELDS: tuple[str, ...] = (
    "event",
    "run_id",
    "trace_id",
    "stage",
    "outcome",
    "counts",
    "duration_ms",
)

#: ``outcome`` vocabulary. Free-form strings are allowed, but these are the ones
#: :meth:`RunContext.run_summary` counts.
OUTCOME_OK = "ok"
OUTCOME_RETRY = "retry"
OUTCOME_FAILED = "failed"
OUTCOME_ESCALATED = "escalated"
OUTCOME_SKIPPED = "skipped"

#: Outcomes that mean the run (or one task in it) needs a human.
FAILURE_OUTCOMES: frozenset[str] = frozenset({OUTCOME_FAILED, OUTCOME_ESCALATED, "error"})

_LOG = logging.getLogger(LOGGER_NAME)


class RunContext:
    """The identity and the accounting of one run.

    Aggregates are guarded by a lock: a worker thread may emit while the thread
    that started the run is emitting too, and a half-written summary is worse
    than a slower one.
    """

    def __init__(
        self,
        run_id: str,
        trace_id: str,
        stage: str = "",
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("RunContext.run_id must be a non-empty string")
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("RunContext.trace_id must be a non-empty string")
        self.run_id = run_id
        self.trace_id = trace_id
        self.stage = stage
        self.clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self._by_name: dict[str, int] = {}
        self._outcomes: dict[str, int] = {}
        self._stages: list[str] = []
        self._retries = 0
        self._failures = 0
        self._failure_events: list[str] = []

    # -- construction ------------------------------------------------------
    @classmethod
    def begin(
        cls,
        *,
        stage: str = "",
        trace_id: str | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> RunContext:
        """Start a run: a fresh ``run_id``, joining ``trace_id`` when one is given.

        A first attempt passes nothing and the new ``run_id`` becomes its own
        trace id; a retry passes the id printed by the earlier attempt, so both
        land under one trace and keep distinct run ids.
        """
        run_id = str(uuid.uuid4())
        if trace_id is not None and not str(trace_id).strip():
            raise ValueError("trace_id must be a non-empty string when supplied")
        return cls(run_id=run_id, trace_id=str(trace_id) if trace_id else run_id, stage=stage, clock=clock)

    # -- the emitter -------------------------------------------------------
    def structured_event(
        self,
        name: str,
        *,
        stage: str | None = None,
        outcome: str = OUTCOME_OK,
        counts: Mapping[str, Any] | None = None,
        duration_ms: float | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        r"""Emit one event; return the record that was logged.

        ``name`` should be dotted and namespaced (``research.findings.persisted``)
        so ``grep '"event":"research\.'`` selects a subsystem. ``counts`` must
        hold integers and should count *work*, not configuration: a summary that
        sums them has to stay arithmetic (an attempt budget in ``counts`` would
        be summed into a meaningless total).
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("an event needs a non-empty name")
        clashes = sorted(set(fields) & set(EVENT_FIELDS))
        if clashes:
            raise ValueError(f"event fields {clashes} are fixed by the emitter; pass them as arguments")

        stage_value = self.stage if stage is None else str(stage)
        record: dict[str, Any] = {
            "event": name,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "stage": stage_value,
            "outcome": str(outcome),
            "counts": _int_counts(counts),
            "duration_ms": None if duration_ms is None else round(float(duration_ms), 1),
        }
        record.update(redact_fields(fields))
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)

        with self._lock:
            self._record(record)
        _LOG.log(logging.INFO, line)
        return record

    def _record(self, record: Mapping[str, Any]) -> None:
        self._events.append(dict(record))
        name = str(record["event"])
        self._by_name[name] = self._by_name.get(name, 0) + 1
        outcome = str(record["outcome"])
        self._outcomes[outcome] = self._outcomes.get(outcome, 0) + 1
        for key, value in dict(record["counts"]).items():
            self._counts[key] = self._counts.get(key, 0) + int(value)
        if record["stage"] and record["stage"] not in self._stages:
            self._stages.append(record["stage"])
        if outcome == OUTCOME_RETRY:
            self._retries += 1
        if outcome in FAILURE_OUTCOMES:
            self._failures += 1
            if name not in self._failure_events:
                self._failure_events.append(name)

    # -- the report --------------------------------------------------------
    def run_summary(self) -> dict[str, Any]:
        """Machine-readable summary of what this run emitted so far.

        ``counts`` is the sum of every event's ``counts`` (so ``findings`` means
        "findings this run reported", not "findings in the last event"); use
        ``events_by_name`` when the per-event split matters. ``retries`` counts
        events with outcome ``retry``, ``failures`` those in
        :data:`FAILURE_OUTCOMES`.
        """
        with self._lock:
            elapsed_ms = round((self.clock() - self._started) * 1000, 1)
            return {
                "run_id": self.run_id,
                "trace_id": self.trace_id,
                "stage": self.stage,
                "events": len(self._events),
                "events_by_name": dict(self._by_name),
                "outcomes": dict(self._outcomes),
                "stages": list(self._stages),
                "counts": dict(self._counts),
                "retries": self._retries,
                "failures": self._failures,
                "failure_events": list(self._failure_events),
                "duration_ms": elapsed_ms,
            }

    # -- convenience -------------------------------------------------------
    def event_names(self) -> tuple[str, ...]:
        """Names emitted, in order, including repeats. For assertions and debugging."""
        with self._lock:
            return tuple(str(record["event"]) for record in self._events)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"RunContext(run_id={self.run_id!r}, trace_id={self.trace_id!r}, stage={self.stage!r})"


def structured_event(run: RunContext | None, name: str, **fields: Any) -> dict[str, Any] | None:
    """Emit ``name`` on ``run``; a no-op when there is no run.

    Instrumentation must never be the reason a call site cannot be reused, so a
    ``None`` context (an existing caller, a test, a bare worker) is legal and
    silent rather than an error.
    """
    return None if run is None else run.structured_event(name, **fields)


def _int_counts(counts: Mapping[str, Any] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in (counts or {}).items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"counts[{key!r}] must be an integer, got {value!r}: run_summary() sums these"
            ) from exc
    return result


__all__ = [
    "EVENT_FIELDS",
    "FAILURE_OUTCOMES",
    "LOGGER_NAME",
    "OUTCOME_ESCALATED",
    "OUTCOME_FAILED",
    "OUTCOME_OK",
    "OUTCOME_RETRY",
    "OUTCOME_SKIPPED",
    "RunContext",
    "redact",
    "structured_event",
]
