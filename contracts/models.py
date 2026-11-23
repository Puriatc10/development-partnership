"""Domain models shared across layers.

Only shapes that cross a boundary live here. Database row shapes stay in the
PostgreSQL repositories; agent-internal shapes stay in the agent.

Findings deliberately do **not** live here. The one authoritative finding shape
is :class:`intelligence.ipp.models.Finding` — the scoring engine's input, field
for field the ``research_findings`` row (``crm/schema.sql`` L481-499) — and that
row is the only place a finding's identity is minted. A duplicate ``Finding``
dataclass (with ``ResearchPayload``) used to sit in this module with zero
importers; it was deleted rather than kept in sync, because two shapes for one
row is exactly how an id gets dropped between research and scoring. It cannot be
"reconciled" by hanging engine converters off it either: ``contracts`` may not
import ``intelligence`` (ADR-0001, ``tests/test_architecture_boundaries.py``), so
conversion belongs to :mod:`intelligence.bridge`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

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
