"""Orchestration layer (B): workflow stages, gates, idempotency, runtimes."""

from .contracts import (
    Runtime,
    TaskClaim,
    TaskClaimConflict,
    TaskOutcome,
    TaskSource,
)
from .idempotency import canonical_json, content_key, research_idempotency_key
from .task_source import ManualRuntime, PostgresTaskSource
from .workflow import GATES, Gate, Stage, assert_gate

__all__ = [
    "GATES",
    "Gate",
    "ManualRuntime",
    "PostgresTaskSource",
    "Runtime",
    "Stage",
    "TaskClaim",
    "TaskClaimConflict",
    "TaskOutcome",
    "TaskSource",
    "assert_gate",
    "canonical_json",
    "content_key",
    "research_idempotency_key",
]
