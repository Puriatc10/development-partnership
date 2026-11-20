"""Qualification input: the seam between persisted research and the IPP engine.

Before this module, nothing read ``research_findings`` for scoring. The engine was
unit-tested, the repository was contract-tested, and no code connected them to the
database — so Human Gate 2 had no input to review.

The mapping here is deliberately literal. A finding's own UUID is preserved as
``finding_id`` so a scorecard dimension can cite a row that actually exists; the engine's
evidence discipline is only meaningful if the citation is addressable. ``source_key`` is
carried when present because a finding may have been sourced from a document rather than
a URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from intelligence.ipp.engine import score_qualification
from intelligence.ipp.models import Finding, QualificationInput

COMPANIES = "companies"
REPORTS = "research_reports"
FINDINGS = "research_findings"

REPORT_SELECT = "id,company_id,report_version,overall_confidence,created_at"
FINDING_SELECT = (
    "id,report_id,company_id,finding_type,category,statement,evidence_url,"
    "evidence_excerpt,confidence,created_at"
)


class QualificationInputError(RuntimeError):
    """The company's persisted research cannot be turned into a scoreable input."""


@dataclass(frozen=True)
class ResearchEvidence:
    company: Mapping[str, Any]
    report: Mapping[str, Any]
    findings: tuple[Mapping[str, Any], ...]

    @property
    def finding_ids(self) -> tuple[str, ...]:
        return tuple(str(row["id"]) for row in self.findings)


def load_company(client: Any, company_id: str) -> Mapping[str, Any]:
    rows = client.get(
        COMPANIES,
        {
            "select": "id,name,primary_domain,primary_segment,headcount_min,headcount_max,country,lifecycle_stage",
            "id": f"eq.{company_id}",
        },
    )
    if not rows:
        raise QualificationInputError(f"company {company_id!r} not found (or not visible)")
    if len(rows) > 1:
        raise QualificationInputError(f"company {company_id!r} resolved to {len(rows)} rows")
    return rows[0]


def load_evidence(client: Any, company_id: str) -> ResearchEvidence:
    """Read the company's most recent completed research report and its findings.

    A report without findings is a real state — research ran and produced nothing citable
    — and is reported as such rather than scored as a zero.
    """
    company = load_company(client, company_id)
    reports = client.get(
        REPORTS,
        {
            "select": REPORT_SELECT,
            "company_id": f"eq.{company_id}",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if not reports:
        raise QualificationInputError(
            f"company {company_id!r} has no research report; run research before qualification"
        )
    report = reports[0]
    findings = client.get(
        FINDINGS,
        {
            "select": FINDING_SELECT,
            "company_id": f"eq.{company_id}",
            "report_id": f"eq.{report['id']}",
            "order": "created_at.asc",
        },
    )
    return ResearchEvidence(company=company, report=report, findings=tuple(findings))


def findings_from_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[Finding, ...]:
    """Map persisted findings to engine findings, keeping the row's own identity."""
    mapped: list[Finding] = []
    for row in rows:
        missing = [key for key in ("id", "category", "finding_type", "statement") if not row.get(key)]
        if missing:
            raise QualificationInputError(
                f"research_findings row {row.get('id')!r} is missing {missing}; refusing to score "
                "evidence that cannot be cited"
            )
        mapped.append(
            Finding(
                finding_id=str(row["id"]),
                category=str(row["category"]),
                finding_type=str(row["finding_type"]),
                confidence=str(row.get("confidence") or "unverified"),
                statement=str(row["statement"]),
                evidence_url=(str(row["evidence_url"]) if row.get("evidence_url") else None),
                source_key=(str(row["evidence_excerpt"]) if row.get("evidence_excerpt") else None),
            )
        )
    return tuple(mapped)


def build_input(evidence: ResearchEvidence) -> QualificationInput:
    company = evidence.company
    headcount: int | None = None
    # A directory range is an estimate, never a verified count (rubric: medium confidence).
    if company.get("headcount_min") is not None:
        headcount = int(company["headcount_min"])
    elif company.get("headcount_max") is not None:
        headcount = int(company["headcount_max"])
    return QualificationInput(
        findings=findings_from_rows(evidence.findings),
        segment=str(company.get("primary_segment") or "") or None,
        employee_estimate=headcount,
        region=(str(company.get("country")) if company.get("country") else None),
        company_name=str(company.get("name") or ""),
    )


def score_company(client: Any, company_id: str) -> tuple[Any, ResearchEvidence]:
    """Score a company from its own persisted research. Returns (scorecard, evidence)."""
    evidence = load_evidence(client, company_id)
    if not evidence.findings:
        raise QualificationInputError(
            f"company {company_id!r} has a research report with no findings; there is nothing to "
            "score, and an empty scorecard would be a measurement that was never taken"
        )
    scorecard = score_qualification(build_input(evidence))
    return scorecard, evidence
