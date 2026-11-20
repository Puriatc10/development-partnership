"""Briefs and the evidence -> IPP bridge.

The brief must ask the rubric's questions; the bridge must refuse evidence it
cannot classify rather than inventing a category or a confidence.
"""

from __future__ import annotations

import pytest

from intelligence.bridge import EvidenceContractError, findings_from_evidence, score_candidate
from intelligence.briefs import DEFAULT_PRIORITY, brief_questions, build_research_brief
from intelligence.ipp import QualificationScorecard

CANDIDATE = {"name": "Gamma Dev", "domain": "gamma.dev", "segment": "webflow_studio"}


def test_brief_covers_every_ipv2_dimension():
    brief = build_research_brief(CANDIDATE)
    dims = [t["dimension"] for t in brief["targets"]]
    assert len(dims) == 7
    assert sum(t["max_points"] for t in brief["targets"]) == 100


def test_brief_prioritises_the_dimensions_refokus_failed():
    brief = build_research_brief(CANDIDATE)
    priority = {t["dimension"] for t in brief["targets"] if t["priority"]}
    assert priority == set(DEFAULT_PRIORITY)
    assert "repeatability_orders" in priority


def test_brief_anchors_come_from_the_rubric_not_from_prose():
    brief = build_research_brief(CANDIDATE)
    customer_flow = next(t for t in brief["targets"] if t["dimension"] == "customer_flow")
    points = [a["points"] for a in customer_flow["anchors"]]
    assert points == sorted(points, reverse=True)
    assert 20 in points
    assert all(a["rubric_lines"].startswith("L") for a in customer_flow["anchors"])


def test_brief_questions_are_answerable_and_cite_the_rubric():
    questions = brief_questions(build_research_brief(CANDIDATE))
    assert len(questions) > 10
    assert any("Repeatability of Orders" in q for q in questions)
    assert all("rubric" in q for q in questions)


def test_brief_forbids_inference_and_keeps_unknowns_unknown():
    brief = build_research_brief(CANDIDATE)
    assert any("hypothesis" in rule for rule in brief["prohibited_inferences"])
    assert "information_gap_tbd" in brief["output_contract"]["rules"][1]


# --- bridge ---------------------------------------------------------------

EVIDENCE = [
    {
        "finding_id": "f1",
        "statement": "The studio publishes four commercial client case studies with named brands.",
        "category": "client_roster",
        "finding_type": "verified_fact",
        "confidence": "high",
        "evidence_url": "https://gamma.dev/work",
        "evidence_excerpt": "Our work for ...",
    },
    {
        "finding_id": "f2",
        "statement": "The studio builds Webflow sites with custom JavaScript and third-party API calls.",
        "category": "technology_stack",
        "finding_type": "verified_fact",
        "confidence": "high",
        "evidence_url": "https://gamma.dev/about",
    },
]


def test_evidence_converts_to_findings():
    findings = findings_from_evidence(EVIDENCE)
    assert [f.finding_id for f in findings] == ["f1", "f2"]
    assert findings[0].evidence_url == "https://gamma.dev/work"


def test_unclassifiable_evidence_is_refused_not_defaulted():
    bad = dict(EVIDENCE[0], category="vibes")
    with pytest.raises(EvidenceContractError) as exc:
        findings_from_evidence([bad])
    assert "not in the schema enum" in str(exc.value)


def test_verified_fact_without_url_is_refused():
    bad = dict(EVIDENCE[0], evidence_url="")
    with pytest.raises(EvidenceContractError):
        findings_from_evidence([bad])


def test_bad_confidence_is_refused():
    with pytest.raises(EvidenceContractError):
        findings_from_evidence([dict(EVIDENCE[0], confidence="certain")])


def test_score_candidate_uses_the_canonical_engine():
    card = score_candidate(CANDIDATE, EVIDENCE)
    assert isinstance(card, QualificationScorecard)
    assert card.needs_human_review is True
    assert card.review_status == "draft"
    assert card.evidence_coverage > 0


def test_empty_evidence_produces_an_inconclusive_card():
    card = score_candidate(CANDIDATE, [])
    assert card.insufficient_evidence is True
    assert card.evidence_coverage == 0.0
    assert card.tier == "disqualified"
    assert card.disqualified_by_score_only is True
