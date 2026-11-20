"""Operator surfaces: the human side of the platform's gates.

Agents stage work and never approve it. Everything a human must decide — reviewing a
discovery candidate, registering a company, approving a scorecard, authorising a draft —
lives here, behind an operator JWT whose claims the database itself checks.

This package holds no business rules of its own: the policies, the review-pair constraint
and the partial unique indexes decide what is legal, and these functions surface that
decision as an actionable message rather than an uncaught PostgREST error.
"""

from __future__ import annotations

from .candidates import (
    CANDIDATE_TABLE,
    Gate1Error,
    approve_candidate,
    get_candidate,
    link_company,
    list_candidates,
    list_evidence,
    mark_conflict,
    mark_duplicate,
    reject_candidate,
)
from .registration import (
    COMPANY_TABLE,
    SEGMENT_VALUES,
    RegistrationResult,
    register_candidate,
)
from .session import OperatorSession, OperatorSessionError, open_session

__all__ = [
    "CANDIDATE_TABLE",
    "COMPANY_TABLE",
    "Gate1Error",
    "OperatorSession",
    "OperatorSessionError",
    "RegistrationResult",
    "SEGMENT_VALUES",
    "approve_candidate",
    "get_candidate",
    "link_company",
    "list_candidates",
    "list_evidence",
    "mark_conflict",
    "mark_duplicate",
    "open_session",
    "register_candidate",
    "reject_candidate",
]
