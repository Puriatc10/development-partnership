"""Qualification service: score from persisted research and persist a reviewable draft.

The engine may only ever emit a draft (ADR-0005/0006): ``needs_human_review`` is required,
an inconclusive scorecard is refused rather than written as a zero, and the persisted row
keeps the evaluator identity so a reviewer can see who produced it.

Approval is not here. Human Gate 2 is an operator action against the persisted row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from integrations.supabase.qualification_repository import QualificationRepository

from .loader import ResearchEvidence, score_company


@dataclass(frozen=True)
class QualificationResult:
    company_id: str
    scorecard: Any
    evidence: ResearchEvidence
    persisted: dict | None
    reused: bool = False


def qualify_company(
    client: Any,
    company_id: str,
    *,
    agent_identity: str,
    persist: bool = True,
) -> QualificationResult:
    """Score one company and, by default, persist the draft.

    ``persist=False`` exists so an operator can inspect a scorecard before it becomes part
    of the record; it does not change what the engine is allowed to emit.
    """
    if not agent_identity:
        raise ValueError("agent_identity is required: a scorecard must name its evaluator")

    scorecard, evidence = score_company(client, company_id)
    if not persist:
        return QualificationResult(company_id, scorecard, evidence, None)

    repository = QualificationRepository(client, agent_identity=agent_identity)
    outcome = repository.persist_draft(company_id, scorecard)
    # ``persist_draft`` reports whether it wrote or matched an identical draft; deriving
    # "reused" from a pre-read would call a fresh write reused whenever any older draft
    # happened to exist.
    reused = isinstance(outcome, dict) and outcome.get("status") == "already_executed"
    row = outcome.get("record") if isinstance(outcome, dict) else outcome
    return QualificationResult(
        company_id=company_id,
        scorecard=scorecard,
        evidence=evidence,
        persisted=row,
        reused=reused,
    )
