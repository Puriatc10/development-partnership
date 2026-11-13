"""Bridge: normalized discovery evidence -> IPP scorecard.

One scoring implementation exists (``intelligence.ipp``); this module only maps
a candidate's evidence rows onto its input model, refusing anything it cannot
classify rather than guessing a category or promoting a hypothesis.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .ipp import Finding, QualificationInput, QualificationScorecard, score_qualification
from .ipp.models import CONFIDENCE_LEVELS, FINDING_CATEGORIES, FINDING_TYPES

REQUIRED_FIELDS = ("statement", "category", "finding_type", "confidence")


class EvidenceContractError(ValueError):
    """An evidence row cannot be represented as a policy-compliant finding."""


def findings_from_evidence(rows: Iterable[Mapping[str, Any]]) -> list[Finding]:
    """Validate and convert evidence rows; raises rather than inventing values."""
    findings: list[Finding] = []
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_FIELDS if not str(row.get(field) or "").strip()]
        if missing:
            raise EvidenceContractError(f"evidence[{index}] missing required field(s): {missing}")

        category = str(row["category"])
        finding_type = str(row["finding_type"])
        confidence = str(row["confidence"])
        if category not in FINDING_CATEGORIES:
            raise EvidenceContractError(
                f"evidence[{index}] category '{category}' is not in the schema enum; "
                "map it explicitly instead of defaulting to 'other'"
            )
        if finding_type not in FINDING_TYPES:
            raise EvidenceContractError(f"evidence[{index}] invalid finding_type '{finding_type}'")
        if confidence not in CONFIDENCE_LEVELS:
            raise EvidenceContractError(f"evidence[{index}] invalid confidence '{confidence}'")

        url = str(row.get("evidence_url") or "").strip()
        if finding_type == "verified_fact" and not url:
            raise EvidenceContractError(
                f"evidence[{index}] is a verified_fact without evidence_url (mirrors chk_verified_fact_requires_evidence)"
            )

        findings.append(
            Finding(
                finding_id=str(row.get("finding_id") or f"evidence-{index}"),
                category=category,
                finding_type=finding_type,  # type: ignore[arg-type]
                confidence=confidence,  # type: ignore[arg-type]
                statement=str(row["statement"]).strip(),
                evidence_url=url or None,
                source_key=str(row.get("source_key") or "") or None,
            )
        )
    return findings


def score_candidate(
    candidate: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    employee_estimate: int | None = None,
    region: str | None = None,
) -> QualificationScorecard:
    """Run the canonical engine over a discovery candidate's evidence."""
    findings = findings_from_evidence(evidence_rows)
    return score_qualification(
        QualificationInput(
            findings=findings,
            segment=str(candidate.get("segment") or candidate.get("primary_segment") or "digital_agency"),
            employee_estimate=employee_estimate,
            region=region,
            company_name=str(candidate.get("name") or ""),
        )
    )
