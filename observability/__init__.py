"""Run tracing without an observability stack: run ids, JSON log lines, redaction.

What this package is
--------------------
The platform runs workers that write to PostgreSQL and call one research
provider. What it did not have was a way to answer two operational questions:
*which* run produced this report, and *why* did this task fail. It does not need
a tracing backend, a metrics agent, or a new dependency to answer them — it
needs an identifier that travels with the work and one line of JSON per step
that an operator can grep.

Two modules, one job each:

``observability.run``
    :class:`RunContext` — a fresh ``run_id`` per process run plus a ``trace_id``
    that a retry can reuse — and the one-line JSON emitter behind it.

``observability.redaction``
    The credential scrubber. The regexes are *not* restated here: the compiler
    of those rules is ``outreach/gate.py``, which needed them first, and this
    module imports them. Two copies of a security rule drift, and the copy that
    drifts is always the one nobody re-read.

Nothing here writes to the database, opens a socket, or holds global state, so
importing it from a CLI, a test, or a worker costs nothing.
"""

from __future__ import annotations

from .redaction import redact, redact_fields
from .run import (
    EVENT_FIELDS,
    FAILURE_OUTCOMES,
    LOGGER_NAME,
    OUTCOME_ESCALATED,
    OUTCOME_FAILED,
    OUTCOME_OK,
    OUTCOME_RETRY,
    OUTCOME_SKIPPED,
    RunContext,
    structured_event,
)

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
    "redact_fields",
    "structured_event",
]
