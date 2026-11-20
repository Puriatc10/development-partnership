"""Eryndex Ideal Partner Profile (IPP) v2 qualification engine.

Pure and deterministic: ``score_qualification`` maps a research evidence set to
the seven-dimension, 100-point scorecard defined by
``agents/qualification/ipp-scoring-rubric-v2.md``, expressed so it can be
persisted into ``lead_qualifications`` (``crm/schema.sql``) unchanged.

Every result is a draft that requires human approval: the engine deliberately
cannot emit an approved decision.

    >>> from intelligence.ipp import QualificationInput, Finding, score_qualification
    >>> card = score_qualification(QualificationInput(findings=[]))
    >>> card.tier, card.needs_human_review
    ('disqualified', True)
"""

from .engine import score_qualification
from .models import (
    DimensionScore,
    DisqualificationTrigger,
    Finding,
    QualificationInput,
    QualificationScorecard,
)

__all__ = [
    "score_qualification",
    "QualificationInput",
    "QualificationScorecard",
    "DimensionScore",
    "DisqualificationTrigger",
    "Finding",
]
