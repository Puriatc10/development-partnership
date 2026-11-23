"""Qualification: persisted research in, a reviewable scorecard draft out.

The engine lives in ``intelligence.ipp`` and is frozen. This package is only the seam that
was missing — reading a company's own ``research_findings``, scoring them, and handing the
draft to Human Gate 2 through ``integrations.postgres.qualification_repository``.
"""

from __future__ import annotations

from .loader import (
    QualificationInputError,
    ResearchEvidence,
    build_input,
    findings_from_rows,
    load_company,
    load_evidence,
    score_company,
)
from .service import QualificationResult, qualify_company

__all__ = [
    "QualificationInputError",
    "QualificationResult",
    "ResearchEvidence",
    "build_input",
    "findings_from_rows",
    "load_company",
    "load_evidence",
    "qualify_company",
    "score_company",
]
