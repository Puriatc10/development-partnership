"""Domain models shared across layers.

Only shapes that cross a boundary live here. Database row shapes stay in the
Supabase repositories; agent-internal shapes stay in the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from .provider import Evidence

FindingType = Literal["verified_fact", "hypothesis", "information_gap_tbd"]
Confidence = Literal["high", "medium", "low", "unverified"]

FINDING_TYPES: frozenset[str] = frozenset({"verified_fact", "hypothesis", "information_gap_tbd"})
CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low", "unverified"})

# Mirrors finding_category_type in crm/schema.sql. Kept in sync deliberately:
# a new category requires a migration and an update here.
FINDING_CATEGORIES: frozenset[str] = frozenset(
    {
        "business_model",
        "client_roster",
        "technology_stack",
        "scoping_maturity",
        "growth_signals",
        "pain_points",
        "pricing_rates",
        "team_structure",
        "other",
    }
)

SEGMENTS: frozenset[str] = frozenset(
    {"digital_agency", "webflow_studio", "framer_studio", "ecommerce_agency", "other"}
)


@dataclass(frozen=True)
class CompanyRef:
    name: str
    primary_domain: str
    segment: str
    company_id: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("CompanyRef.name is required")
        if not self.primary_domain.strip():
            raise ValueError("CompanyRef.primary_domain is required")
        if self.segment not in SEGMENTS:
            raise ValueError(f"Unknown segment: {self.segment!r}")


@dataclass(frozen=True)
class Finding:
    """An atomic, citable claim about a company.

    Mirrors the ''verified_fact requires evidence'' check constraint in the
    database, so an invalid finding fails before a write is attempted.
    """

    statement: str
    category: str
    finding_type: FindingType
    confidence: Confidence = "unverified"
    evidence_url: str = ""
    evidence_excerpt: str = ""
    finding_id: str = ""

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("Finding.statement is required")
        if self.finding_type not in FINDING_TYPES:
            raise ValueError(f"Invalid finding_type: {self.finding_type!r}")
        if self.category not in FINDING_CATEGORIES:
            raise ValueError(f"Invalid finding category: {self.category!r}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"Invalid confidence: {self.confidence!r}")
        if self.finding_type == "verified_fact":
            if not self.evidence_url:
                raise ValueError("A 'verified_fact' requires evidence_url")
            if self.confidence not in ("high", "medium"):
                raise ValueError("A 'verified_fact' requires confidence 'high' or 'medium'")

    def to_row(self, report_id: str, company_id: str) -> dict[str, Any]:
        return {
            "report_id": report_id,
            "company_id": company_id,
            "finding_type": self.finding_type,
            "category": self.category,
            "statement": self.statement,
            "evidence_url": self.evidence_url or None,
            "evidence_excerpt": self.evidence_excerpt or None,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Candidate:
    """A discovered company that has NOT yet passed Human Gate 1."""

    name: str
    domain: str
    source_name: str
    source_url: str
    discovered_at: str
    signals: Mapping[str, Any] = field(default_factory=dict)
    normalized_name: str = ""
    status: str = "pending_review"

    @staticmethod
    def normalize_domain(domain: str) -> str:
        d = (domain or "").strip().lower()
        for prefix in ("https://", "http://"):
            if d.startswith(prefix):
                d = d[len(prefix) :]
        d = d.split("/", 1)[0]
        if d.startswith("www."):
            d = d[4:]
        return d.rstrip(".")

    @staticmethod
    def normalize_name(name: str) -> str:
        keep = [c for c in (name or "").lower() if c.isalnum()]
        return "".join(keep)

    def to_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "normalized_name": self.normalized_name or Candidate.normalize_name(self.name),
            "domain": Candidate.normalize_domain(self.domain),
            "source_name": self.source_name,
            "source_url": self.source_url,
            "discovered_at": self.discovered_at,
            "signals": dict(self.signals),
            "status": self.status,
        }


@dataclass(frozen=True)
class ResearchPayload:
    """What a research provider/researcher must produce before persistence."""

    company: CompanyRef
    summary: str
    recommended_angle: str
    findings: Sequence[Finding]
    overall_confidence: Confidence = "medium"
    gaps_and_unknowns: str = ""

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("ResearchPayload.summary is required")
        if not self.recommended_angle.strip():
            raise ValueError("ResearchPayload.recommended_angle is required")
        if not self.findings:
            raise ValueError("ResearchPayload.findings must not be empty")
        if self.overall_confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"Invalid overall_confidence: {self.overall_confidence!r}")

    def evidence(self) -> list[Evidence]:
        return [
            Evidence(
                url=f.evidence_url,
                excerpt=f.evidence_excerpt,
                provider="researcher",
                provider_version="1.0",
            )
            for f in self.findings
            if f.evidence_url
        ]
