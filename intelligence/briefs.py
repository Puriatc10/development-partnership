"""Research briefs aimed at the IPP anchors, not at "understanding a company".

The Refokus calibration showed the failure mode precisely: 13 honest findings,
excellent report, 32/100 — because the research questions were not the rubric's
questions. A brief generated here is derived from the rubric's own anchor ladders
(``intelligence/ipp/rubric.py``), so every question a researcher answers is one
that can actually earn points.

Nothing in a brief invents data; it states what would count as evidence.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .ipp import rubric

RULES_BY_DIMENSION = {
    rubric.CUSTOMER_FLOW: rubric.CUSTOMER_FLOW_RULES,
    rubric.DEVELOPMENT_DEMAND: rubric.DEVELOPMENT_DEMAND_RULES,
    rubric.REPEATABILITY_ORDERS: rubric.REPEATABILITY_RULES,
    rubric.TECHNICAL_FIT: rubric.TECHNICAL_FIT_RULES,
    rubric.DECISION_MAKER_ACCESSIBILITY: rubric.DECISION_MAKER_RULES,
    rubric.COMMERCIAL_FIT: rubric.COMMERCIAL_FIT_RULES,
}

# Calibration finding: Refokus lost points on exactly these three.
DEFAULT_PRIORITY = (
    rubric.REPEATABILITY_ORDERS,
    rubric.DECISION_MAKER_ACCESSIBILITY,
    rubric.TECHNICAL_FIT,
)

PROHIBITED_INFERENCES = (
    "Do not infer headcount, revenue, client names, rates, or contract terms from visual style or site size.",
    "Do not convert a hypothesis into a verified fact because a completion target needs it.",
    "Do not report a price range as the company's rate; directory estimates must be labelled as such, with dates.",
    "Do not claim a decision-maker's receptiveness from a generic contact page.",
    "Do not fabricate a case study, testimonial, or client logo that is not published.",
)


def build_research_brief(
    candidate: Mapping[str, Any],
    *,
    priority_dimensions: Sequence[str] = DEFAULT_PRIORITY,
) -> dict[str, Any]:
    """Produce a machine-usable research brief for one candidate company."""
    targets: list[dict[str, Any]] = []

    for spec in rubric.DIMENSIONS:
        entry: dict[str, Any] = {
            "dimension": spec.key,
            "label": spec.label,
            "max_points": spec.max_points,
            "schema_column": spec.schema_column,
            "rubric_lines": spec.rubric_lines,
            "priority": spec.key in priority_dimensions,
            "evidence_categories": sorted(spec.categories),
            "anchors": [],
        }
        if spec.key == rubric.BUSINESS_SIZE:
            entry["anchors"] = [
                {
                    "points": points,
                    "headcount_range": f"{low}-{high}",
                    "attainment": attainment,
                    "rubric_lines": note,
                }
                for low, high, points, attainment, note in rubric.BUSINESS_SIZE_BANDS
            ]
        else:
            for rule in RULES_BY_DIMENSION.get(spec.key, ()):
                entry["anchors"].append(
                    {
                        "points": rule.points,
                        "attainment": rule.attainment,
                        "rubric_lines": rule.rubric_lines,
                    }
                )
            entry["anchors"].sort(key=lambda a: a["points"], reverse=True)
        targets.append(entry)

    return {
        "rubric_version": rubric.RUBRIC_VERSION,
        "company": {
            "name": candidate.get("name"),
            "domain": candidate.get("domain"),
            "segment": candidate.get("segment") or candidate.get("primary_segment"),
            "source_url": candidate.get("source_url"),
        },
        "objective": (
            "Collect verifiable public evidence that can earn IPP v2 anchor points for this company. "
            "Each finding must cite a source URL and a verbatim excerpt."
        ),
        "priority_dimensions": list(priority_dimensions),
        "targets": targets,
        "output_contract": {
            "fields": [
                "finding_type (verified_fact|hypothesis|information_gap_tbd)",
                "category (schema finding_category_type)",
                "statement",
                "confidence (high|medium|low|unverified)",
                "evidence_url",
                "evidence_excerpt",
            ],
            "rules": [
                "verified_fact requires evidence_url and confidence high|medium",
                "an unknown must be recorded as information_gap_tbd, never guessed",
                "one claim per finding; do not bundle",
            ],
        },
        "prohibited_inferences": list(PROHIBITED_INFERENCES),
    }


def brief_questions(brief: Mapping[str, Any]) -> list[str]:
    """Flatten a brief into human/LLM-readable questions, priority first."""
    questions: list[str] = []
    ordered = sorted(brief["targets"], key=lambda t: (not t["priority"], -t["max_points"]))
    for target in ordered:
        for anchor in target["anchors"]:
            questions.append(
                f"[{target['label']} +{anchor['points']} / {target['max_points']}] "
                f"Can we verify that {anchor['attainment']}? (rubric {anchor['rubric_lines']})"
            )
    return questions
