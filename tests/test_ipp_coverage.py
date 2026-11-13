"""Evidence-coverage semantics on the scorecard (ADR-0006).

A low score can mean "bad prospect" or "we could not measure it". These tests
pin the distinction so a reviewer is never misled by a tier label alone.
"""

from __future__ import annotations

from intelligence.ipp import Finding, QualificationInput, score_qualification


def card(findings):
    return score_qualification(QualificationInput(findings=findings, segment="webflow_studio"))


def test_empty_evidence_has_zero_coverage_and_is_inconclusive():
    c = card([])
    assert c.evidence_coverage == 0.0
    assert c.insufficient_evidence is True
    assert c.tier == "disqualified"
    assert c.disqualified_by_score_only is True
    assert c.needs_human_review is True


def test_partial_evidence_reports_its_coverage():
    findings = [
        Finding(
            finding_id="f1",
            category="business_model",
            finding_type="verified_fact",
            confidence="high",
            statement="The studio publishes three commercial client case studies with ongoing retainers.",
            evidence_url="https://example.com/work",
        ),
        Finding(
            finding_id="f2",
            category="technology_stack",
            finding_type="verified_fact",
            confidence="high",
            statement="The studio builds Webflow sites with custom JavaScript and external API requests.",
            evidence_url="https://example.com/about",
        ),
    ]
    c = card(findings)
    assert 0.0 < c.evidence_coverage <= 1.0
    assert c.verified_dimensions == c.verified_fact_count or c.verified_dimensions >= 1
    assert c.evidence_coverage == round(c.verified_dimensions / len(c.dimensions), 3)
    assert c.as_dict()["evidence_coverage"] == c.evidence_coverage


def test_score_only_disqualification_is_distinguishable_from_a_fired_trigger():
    """A tier of 'disqualified' with no trigger means 'below threshold', not 'rejected for cause'."""
    findings = [
        Finding(
            finding_id="f1",
            category="business_model",
            finding_type="verified_fact",
            confidence="high",
            statement="The agency publishes two client case studies.",
            evidence_url="https://example.com/work",
        )
    ]
    c = card(findings)
    assert c.tier == "disqualified"
    assert c.disqualification_triggers == ()
    assert c.disqualified_by_score_only is True
