"""Value objects for the Eryndex IPP v2 qualification engine.

Plain ``dataclasses`` only: no behaviour, no side effects, no I/O. Enum-valued
fields are validated against the PostgreSQL enums declared in ``crm/schema.sql``
by :func:`intelligence.ipp.engine.score_qualification` (not on construction, so
that partially-built payloads can be inspected while an agent is assembling
research results).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Enum value sets — transcribed verbatim from crm/schema.sql
# ---------------------------------------------------------------------------

# crm/schema.sql L183-187: research_finding_type
FINDING_TYPES: Tuple[str, ...] = ("verified_fact", "hypothesis", "information_gap_tbd")

# crm/schema.sql L189-199: finding_category_type
FINDING_CATEGORIES: Tuple[str, ...] = (
    "business_model",
    "client_roster",
    "technology_stack",
    "scoping_maturity",
    "growth_signals",
    "pain_points",
    "pricing_rates",
    "team_structure",
    "other",
)

# crm/schema.sql L201-206: confidence_level_type
CONFIDENCE_LEVELS: Tuple[str, ...] = ("high", "medium", "low", "unverified")

# crm/schema.sql L129-137: market_segment_type
MARKET_SEGMENTS: Tuple[str, ...] = (
    "digital_agency",
    "webflow_studio",
    "framer_studio",
    "ecommerce_agency",
    "independent_consultant",
    "small_saas",
    "other_recurring",
)

# crm/schema.sql L146-151: qualification_tier_type
QUALIFICATION_TIERS: Tuple[str, ...] = (
    "tier_1_priority",
    "tier_2_prospective",
    "tier_3_deferred",
    "disqualified",
)

# crm/schema.sql L208-213: qualification_review_status_type. Agents may only
# ever emit the first value (rubric L882, contract 4 section 5.6).
QUALIFICATION_REVIEW_STATUSES: Tuple[str, ...] = (
    "draft",
    "pending_human_review",
    "approved",
    "rejected",
)


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One atomic, evidence-backed research finding.

    Mirrors a row of ``research_findings`` (crm/schema.sql L481-499). The engine
    validates the check constraint ``chk_verified_fact_requires_evidence``
    itself (see ``engine._validate_findings``) so an invalid finding can never
    reach the database through a scorecard.
    """

    finding_id: str
    category: str
    finding_type: str
    confidence: str
    statement: str
    evidence_url: Optional[str] = None
    source_key: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("finding_id", "category", "finding_type", "confidence", "statement"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"Finding field '{name}' must be a string, got {type(value).__name__}")


@dataclass(frozen=True)
class QualificationInput:
    """Profile basics plus the evidence set gathered by the research agent.

    ``employee_estimate`` is the profile-level headcount estimate (usually a
    third-party directory range). Per rubric L358 it is a ``medium`` confidence
    indicator, never a verified contractual fact; the resulting confidence is
    reflected on the Business Size dimension.
    """

    findings: Sequence[Finding] = field(default_factory=tuple)
    segment: Optional[str] = None
    employee_estimate: Optional[int] = None
    region: Optional[str] = None
    company_name: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionScore:
    """Score for one of the seven IPP dimensions (rubric section 2)."""

    key: str
    label: str
    schema_column: str
    points_awarded: float
    max_points: float
    rationale: str
    evidence_refs: Tuple[str, ...] = ()
    confidence: str = "unverified"
    gap_notes: Tuple[str, ...] = ()
    anchor_points: Optional[float] = None
    rubric_lines: str = ""

    def as_evidence_record(self, findings: Sequence[Finding] = ()) -> Dict[str, Any]:
        """Render this dimension into the ``dimension_evidence`` JSONB shape.

        The shape satisfies three consumers at once:
        * rubric L364-390 (score/max/excerpt/confidence/score_justification),
        * agent contract 4 section 5.5 (``evidence`` + ``finding_ids``),
        * trigger ``fn_enforce_lead_qualification_rules`` L1279-1299, which
          raises unless ``dimension_evidence -> <dim> ->> 'evidence'`` is
          non-null for every dimension score greater than zero.
        """
        by_id = {f.finding_id: f for f in findings}
        cited = [by_id[i] for i in self.evidence_refs if i in by_id]
        excerpt = cited[0].statement if cited else None
        first = cited[0] if cited else None

        return {
            "score": round(self.points_awarded, 2),
            "max": self.max_points,
            "evidence": (
                "; ".join(f"[{f.finding_id}] {f.statement}" for f in cited[:2])
                if self.points_awarded > 0 and cited
                else (self.rationale if self.points_awarded > 0 else None)
            ),
            "finding_ids": list(self.evidence_refs),
            "source_url": first.evidence_url if first else None,
            "excerpt": excerpt,
            "finding_type": first.finding_type if first else None,
            "confidence": self.confidence,
            "score_justification": self.rationale,
            "gap_notes": list(self.gap_notes),
        }


@dataclass(frozen=True)
class DisqualificationTrigger:
    """A fired hard disqualifier (rubric section 6.1, L494-511)."""

    code: str
    description: str
    rubric_lines: str
    evidence_finding_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class QualificationScorecard:
    """Draft scorecard produced for mandatory human review.

    There is deliberately no way to express approval here: the object carries
    ``needs_human_review`` / ``review_status='draft'`` as fixed values, matching
    rubric L46, L554 and L879-882 (zero autonomous approval).
    """

    dimensions: Tuple[DimensionScore, ...]
    total_score: float
    tier: str
    tier_label: str
    recommended_action: str
    disqualification_triggers: Tuple[DisqualificationTrigger, ...] = ()
    missing_evidence: Tuple[str, ...] = ()
    overall_confidence: str = "unverified"
    insufficient_evidence: bool = True
    needs_human_review: bool = True
    review_status: str = "draft"
    rubric_version: str = ""
    segment: Optional[str] = None
    region: Optional[str] = None
    company_name: Optional[str] = None
    findings: Tuple[Finding, ...] = ()
    verified_dimensions: int = 0
    verified_fact_count: int = 0

    # -- convenience -----------------------------------------------------

    @property
    def evidence_coverage(self) -> float:
        """Share of dimensions backed by high/medium-confidence evidence (0-1).

        A low total score with low coverage means "we could not measure this",
        which a reviewer must distinguish from "we measured it and it is bad".
        """
        if not self.dimensions:
            return 0.0
        return round(self.verified_dimensions / len(self.dimensions), 3)

    @property
    def disqualified_by_score_only(self) -> bool:
        """True when the tier is 'disqualified' purely because the score is < 40."""
        return self.tier == "disqualified" and not self.disqualification_triggers

    def dimension(self, key: str) -> DimensionScore:
        for dim in self.dimensions:
            if dim.key == key:
                return dim
        raise KeyError(f"Unknown dimension '{key}'")

    @property
    def scores(self) -> Dict[str, float]:
        """Mapping of schema column name -> points (contract 4 section 5.5)."""
        return {d.schema_column: d.points_awarded for d in self.dimensions}

    @property
    def dimension_evidence(self) -> Dict[str, Dict[str, Any]]:
        """``dimension_evidence`` JSONB, with source URLs/excerpts resolved."""
        return {d.key: d.as_evidence_record(self.findings) for d in self.dimensions}

    @property
    def disqualification_reasons(self) -> Tuple[str, ...]:
        return tuple(t.code for t in self.disqualification_triggers)

    def to_db_record(self) -> Dict[str, Any]:
        """Column payload for ``lead_qualifications`` (crm/schema.sql L514-561).

        Only columns this engine is allowed to populate are emitted; reviewer
        identity/timestamps stay NULL because agents may never approve.
        """
        notes = "; ".join(
            f"{t.code}: {t.description} (rubric {t.rubric_lines}; evidence: "
            + (", ".join(t.evidence_finding_ids) if t.evidence_finding_ids else "none")
            + ")"
            for t in self.disqualification_triggers
        )
        record: Dict[str, Any] = {
            "overall_score": round(self.total_score, 2),
            "qualification_tier": self.tier,
            "disqualification_reasons": list(self.disqualification_reasons),
            "disqualification_notes": notes or None,
            "recommended_next_action": self.recommended_action,
            "review_status": "draft",
            "dimension_evidence": self.dimension_evidence,
        }
        for column, points in self.scores.items():
            record[column] = round(points, 2)
        return record

    def as_dict(self) -> Dict[str, Any]:
        """Fully explicit JSON-serialisable rendering (audit / golden files)."""
        return {
            "rubric_version": self.rubric_version,
            "company_name": self.company_name,
            "segment": self.segment,
            "region": self.region,
            "dimensions": [
                {
                    "key": d.key,
                    "label": d.label,
                    "schema_column": d.schema_column,
                    "points_awarded": d.points_awarded,
                    "max_points": d.max_points,
                    "rationale": d.rationale,
                    "evidence_refs": list(d.evidence_refs),
                    "confidence": d.confidence,
                    "gap_notes": list(d.gap_notes),
                    "rubric_lines": d.rubric_lines,
                }
                for d in self.dimensions
            ],
            "total_score": self.total_score,
            "tier": self.tier,
            "tier_label": self.tier_label,
            "recommended_action": self.recommended_action,
            "disqualification_triggers": [
                {
                    "code": t.code,
                    "description": t.description,
                    "rubric_lines": t.rubric_lines,
                    "evidence_finding_ids": list(t.evidence_finding_ids),
                }
                for t in self.disqualification_triggers
            ],
            "missing_evidence": list(self.missing_evidence),
            "overall_confidence": self.overall_confidence,
            "insufficient_evidence": self.insufficient_evidence,
            "verified_dimensions": self.verified_dimensions,
            "verified_fact_count": self.verified_fact_count,
            "evidence_coverage": self.evidence_coverage,
            "disqualified_by_score_only": self.disqualified_by_score_only,
            "needs_human_review": self.needs_human_review,
            "review_status": self.review_status,
        }
