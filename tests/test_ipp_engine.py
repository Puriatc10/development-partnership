"""Behavioural tests for the Eryndex IPP v2 qualification engine.

Covers the contract in ``agents/qualification/ipp-scoring-rubric-v2.md`` and
``crm/schema.sql``:

* happy path with hand-computed dimension points,
* insufficient-evidence path (explicit gaps, low confidence, needs-review),
* invalid-finding validation (mirrors chk_verified_fact_requires_evidence),
* hypothesis partial credit and information-gap zero credit,
* determinism (repeat calls and input ordering),
* tier boundary values,
* disqualification triggers (verified-fact-only).

Expected point values are hand-computed from the rubric anchors, not read back
from the engine: e.g. Customer Flow 15 = rubric L96, Development Demand 20 =
rubric L129, Repeatability 12 = L160, Technical Fit 15 = L196, Business Size 8 =
L227, Decision-Maker 8 = L261, Commercial Fit 5 = L293, total 83.
"""

from __future__ import annotations

import json

import pytest

from intelligence.ipp import (
    DimensionScore,
    DisqualificationTrigger,
    Finding,
    QualificationInput,
    QualificationScorecard,
    score_qualification,
)
from intelligence.ipp import rubric


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def fact(finding_id, category, statement, confidence="high", url="https://studio.example/work"):
    return Finding(
        finding_id=finding_id,
        category=category,
        finding_type="verified_fact",
        confidence=confidence,
        statement=statement,
        evidence_url=url,
    )


def hypothesis(finding_id, category, statement, confidence="medium"):
    return Finding(
        finding_id=finding_id,
        category=category,
        finding_type="hypothesis",
        confidence=confidence,
        statement=statement,
    )


def gap(finding_id, category, statement):
    return Finding(
        finding_id=finding_id,
        category=category,
        finding_type="information_gap_tbd",
        confidence="unverified",
        statement=statement,
    )


HAPPY_PATH_FINDINGS = (
    fact("F001", "client_roster", "Delivered live corporate B2B platforms for Meridian and Arqitel in 2026."),
    fact(
        "F002",
        "client_roster",
        "Client work includes Analog Way and Exebenus platforms launched this year.",
        url="https://studio.example/work/case-studies",
    ),
    fact(
        "F003",
        "client_roster",
        "Ongoing client roster of corporate brands; published case studies continue through 2026.",
        confidence="medium",
        url="https://studio.example/clients",
    ),
    fact(
        "F004",
        "technology_stack",
        "Client builds include customer portal dashboards with Supabase PostgreSQL, custom API "
        "endpoints and bespoke authentication flows.",
        url="https://studio.example/work/portal",
    ),
    fact(
        "F005",
        "pain_points",
        "Agency blog notes engineering delivery overflow; a backend engineer role has been open "
        "for 90 days.",
        confidence="medium",
        url="https://studio.example/blog/hiring",
    ),
    fact(
        "F006",
        "business_model",
        "Clients are retained on monthly retainers; multi-year contract renewals are documented.",
        url="https://studio.example/services",
    ),
    fact(
        "F007",
        "technology_stack",
        "Certified Webflow Enterprise Partner building on Client-First with a componentized design "
        "system and GitHub code management; integrates Supabase and React components.",
        url="https://webflow.com/partners/studio",
    ),
    fact("F008", "team_structure", "Team of 18 across design, engineering and account management.",
         url="https://studio.example/about"),
    fact(
        "F009",
        "team_structure",
        "Co-Founder & CEO Jane Doe publishes a direct personal email and an active LinkedIn "
        "profile with InMail enabled.",
        url="https://studio.example/about/team",
    ),
    fact(
        "F010",
        "pricing_rates",
        "Clutch lists a $25k minimum project size and a $150/hr average rate.",
        confidence="medium",
        url="https://clutch.co/profile/studio",
    ),
)

# Hand-computed from the rubric anchors (see module docstring).
EXPECTED_POINTS = {
    "customer_flow": 15.0,
    "development_demand": 20.0,
    "repeatability_orders": 12.0,
    "technical_fit": 15.0,
    "business_size": 8.0,
    "decision_maker_accessibility": 8.0,
    "commercial_fit": 5.0,
}
EXPECTED_TOTAL = 83.0


def happy_path_input(findings=HAPPY_PATH_FINDINGS):
    return QualificationInput(
        findings=findings,
        segment="webflow_studio",
        employee_estimate=18,
        region="United States",
        company_name="Studio Example",
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_scores_seven_dimensions_with_hand_computed_points():
    card = score_qualification(happy_path_input())

    assert isinstance(card, QualificationScorecard)
    assert len(card.dimensions) == 7
    assert [d.key for d in card.dimensions] == [
        "customer_flow",
        "development_demand",
        "repeatability_orders",
        "technical_fit",
        "business_size",
        "decision_maker_accessibility",
        "commercial_fit",
    ]
    for key, expected in EXPECTED_POINTS.items():
        dim = card.dimension(key)
        assert isinstance(dim, DimensionScore)
        assert dim.points_awarded == expected, f"{key}: {dim.rationale}"

    assert dict((d.key, d.max_points) for d in card.dimensions) == {
        "customer_flow": 20,
        "development_demand": 20,
        "repeatability_orders": 15,
        "technical_fit": 15,
        "business_size": 10,
        "decision_maker_accessibility": 10,
        "commercial_fit": 10,
    }


def test_happy_path_total_is_the_sum_of_dimensions_and_tier_1():
    card = score_qualification(happy_path_input())

    assert card.total_score == EXPECTED_TOTAL
    assert card.total_score == sum(d.points_awarded for d in card.dimensions)
    assert card.tier == "tier_1_priority"
    assert card.tier_label == "Tier 1 (Priority)"
    assert card.tier == rubric.tier_for_score(card.total_score)


def test_happy_path_still_requires_human_review():
    card = score_qualification(happy_path_input())

    assert card.needs_human_review is True
    assert card.review_status == "draft"
    assert card.review_status != "approved"
    assert card.overall_confidence == "high"
    assert card.insufficient_evidence is False
    assert card.disqualification_triggers == ()
    assert card.missing_evidence == ()
    assert "human operator review" in card.recommended_action.lower()


def test_each_scored_dimension_rationale_cites_its_finding_ids():
    card = score_qualification(happy_path_input())
    known_ids = {f.finding_id for f in HAPPY_PATH_FINDINGS}

    for dim in card.dimensions:
        assert dim.points_awarded > 0
        assert dim.evidence_refs, f"{dim.key} scored without evidence refs"
        assert set(dim.evidence_refs) <= known_ids
        cited_in_text = [fid for fid in dim.evidence_refs if fid in dim.rationale]
        assert cited_in_text, f"{dim.key} rationale does not name its findings: {dim.rationale}"

    # Spot-check the three strongest citations by hand.
    assert card.dimension("development_demand").evidence_refs == ("F004", "F005")
    assert card.dimension("technical_fit").evidence_refs == ("F004", "F007")
    assert card.dimension("commercial_fit").evidence_refs == ("F010",)


# ---------------------------------------------------------------------------
# 2. Insufficient evidence
# ---------------------------------------------------------------------------


def test_empty_input_yields_explicit_gaps_low_confidence_and_review_flag():
    card = score_qualification(QualificationInput(findings=[]))

    assert card.total_score == 0.0
    assert all(d.points_awarded == 0.0 for d in card.dimensions)
    assert card.tier == "disqualified"
    assert card.overall_confidence == "unverified"
    assert card.insufficient_evidence is True
    assert card.needs_human_review is True
    assert card.review_status == "draft"

    # One explicit gap per dimension plus the global inconclusive marker.
    assert len(card.missing_evidence) == 7
    assert all(item.startswith("[") for item in card.missing_evidence)
    assert card.disqualification_triggers == ()  # unknown is not a disqualifier
    assert "inconclusive" in card.recommended_action
    assert all(d.gap_notes for d in card.dimensions)
    assert all(d.confidence == "unverified" for d in card.dimensions)


def test_information_gap_tbd_scores_zero_and_is_listed_in_missing_evidence():
    card = score_qualification(
        QualificationInput(
            segment="digital_agency",
            findings=[gap("G1", "business_model", "TBD: retainer model unknown")],
        )
    )

    assert card.dimension("repeatability_orders").points_awarded == 0.0
    assert any("G1" in item and "retainer model unknown" in item for item in card.missing_evidence)
    assert card.dimension("repeatability_orders").confidence == "unverified"
    assert card.overall_confidence == "unverified"


# ---------------------------------------------------------------------------
# 3. Hypothesis partial credit
# ---------------------------------------------------------------------------


def test_hypothesis_only_credit_is_capped_at_lowest_positive_anchor():
    card = score_qualification(
        QualificationInput(
            segment="webflow_studio",
            findings=[
                hypothesis(
                    "H1",
                    "technology_stack",
                    "Likely builds standard Webflow sites with Supabase backends and occasional "
                    "external API requests.",
                )
            ],
        )
    )

    dim = card.dimension("technical_fit")
    # Rubric L193/L192 ladder starts at 4; a hypothesis cannot reach the 12 anchor
    # it would otherwise match (rubric L341).
    assert dim.points_awarded == 4.0  # lowest positive Technical Fit anchor (rubric L193)
    assert dim.confidence == "low"
    assert "L341" in dim.rationale
    assert card.overall_confidence == "unverified"


def test_hypothesis_cannot_substitute_for_a_verified_development_demand_anchor():
    card = score_qualification(
        QualificationInput(
            segment="digital_agency",
            findings=[
                hypothesis(
                    "H2",
                    "technology_stack",
                    "Probably maintains customer portals with external databases and custom APIs.",
                    confidence="high",
                )
            ],
        )
    )

    dim = card.dimension("development_demand")
    assert dim.points_awarded == 5.0  # capped at the dimension's lowest positive anchor
    assert dim.points_awarded < 15.0
    assert dim.confidence == "low"


# ---------------------------------------------------------------------------
# 4. Invalid finding validation (chk_verified_fact_requires_evidence)
# ---------------------------------------------------------------------------


def test_verified_fact_without_evidence_url_raises():
    with pytest.raises(ValueError, match="evidence_url"):
        score_qualification(
            QualificationInput(
                findings=[
                    Finding(
                        finding_id="V1",
                        category="client_roster",
                        finding_type="verified_fact",
                        confidence="high",
                        statement="Works with three enterprise clients.",
                    )
                ]
            )
        )


def test_verified_fact_with_low_confidence_raises():
    with pytest.raises(ValueError, match="high.*medium"):
        score_qualification(
            QualificationInput(
                findings=[
                    Finding(
                        finding_id="V2",
                        category="client_roster",
                        finding_type="verified_fact",
                        confidence="low",
                        statement="Works with three enterprise clients.",
                        evidence_url="https://studio.example/work",
                    )
                ]
            )
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(category="not_a_category", finding_type="hypothesis", confidence="low"), "category"),
        (dict(category="client_roster", finding_type="rumour", confidence="low"), "finding_type"),
        (dict(category="client_roster", finding_type="hypothesis", confidence="certain"), "confidence"),
    ],
)
def test_invalid_finding_enum_values_raise(kwargs, message):
    with pytest.raises(ValueError, match=message):
        score_qualification(
            QualificationInput(
                findings=[
                    Finding(
                        finding_id="B1",
                        statement="Something.",
                        **kwargs,
                    )
                ]
            )
        )


def test_out_of_scope_stack_earns_zero_technical_fit_with_a_gap_note():
    card = score_qualification(
        QualificationInput(
            segment="digital_agency",
            findings=[
                fact(
                    "W1",
                    "technology_stack",
                    "Studio delivers WordPress and PHP themes only; no modern JavaScript framework.",
                    url="https://wp-agency.example/services",
                )
            ],
        )
    )

    dim = card.dimension("technical_fit")
    assert dim.points_awarded == 0.0
    assert any("L192" in note and "W1" in note for note in dim.gap_notes)
    # An out-of-scope stack is scored as a zero anchor, not as a disqualifier
    # (rubric L436-440: unknowns/limits are not automatic disqualifiers).
    assert card.disqualification_triggers == ()


def test_duplicate_and_missing_finding_ids_are_rejected():
    duplicate = fact("D1", "client_roster", "Case study one.")
    with pytest.raises(ValueError, match="Duplicate finding_id"):
        score_qualification(QualificationInput(findings=[duplicate, duplicate]))

    with pytest.raises(ValueError, match="finding_id"):
        score_qualification(
            QualificationInput(
                findings=[Finding(finding_id="", category="other", finding_type="hypothesis",
                                  confidence="low", statement="x")]
            )
        )


# ---------------------------------------------------------------------------
# 5. Determinism
# ---------------------------------------------------------------------------


def test_repeated_scoring_is_identical():
    first = score_qualification(happy_path_input())
    second = score_qualification(happy_path_input())

    assert first == second
    assert json.dumps(first.to_db_record(), sort_keys=True) == json.dumps(
        second.to_db_record(), sort_keys=True
    )
    assert json.dumps(first.as_dict(), sort_keys=True) == json.dumps(second.as_dict(), sort_keys=True)


def test_finding_order_does_not_change_the_result():
    forward = score_qualification(happy_path_input(HAPPY_PATH_FINDINGS))
    backward = score_qualification(happy_path_input(tuple(reversed(HAPPY_PATH_FINDINGS))))

    assert forward == backward
    assert forward.to_db_record() == backward.to_db_record()


# ---------------------------------------------------------------------------
# 6. Tier boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score, expected",
    [
        (0.0, "disqualified"),
        (39.99, "disqualified"),
        (40.0, "tier_3_deferred"),
        (59.99, "tier_3_deferred"),
        (60.0, "tier_2_prospective"),
        (79.99, "tier_2_prospective"),
        (80.0, "tier_1_priority"),
        (99.99, "tier_1_priority"),
        (100.0, "tier_1_priority"),
    ],
)
def test_tier_boundary_values(score, expected):
    assert rubric.tier_for_score(score) == expected
    # Any disqualifier forces 'disqualified' regardless of the total
    # (rubric L542, trigger crm/schema.sql L1302-1306).
    assert rubric.tier_for_score(score, has_disqualifier=True) == "disqualified"


def test_tier_values_are_schema_enum_values():
    assert set(rubric.TIER_LABELS) == {
        "tier_1_priority",
        "tier_2_prospective",
        "tier_3_deferred",
        "disqualified",
    }


# ---------------------------------------------------------------------------
# 7. Disqualification
# ---------------------------------------------------------------------------


def test_prohibited_segment_fires_disqualification_trigger():
    card = score_qualification(
        QualificationInput(
            segment="independent_consultant",
            findings=[fact("F001", "client_roster", "Corporate B2B platforms for Meridian.")],
        )
    )

    assert card.disqualification_triggers
    trigger = card.disqualification_triggers[0]
    assert isinstance(trigger, DisqualificationTrigger)
    assert trigger.code == "DQ_PROHIBITED_SEGMENT"
    assert "L497-500" in trigger.rubric_lines
    assert card.tier == "disqualified"
    assert card.needs_human_review is True
    assert card.review_status == "draft"
    assert "DQ_PROHIBITED_SEGMENT" in card.to_db_record()["disqualification_reasons"]


def test_refusal_to_use_external_suppliers_requires_a_verified_fact():
    statement = "Leadership states they never work with external contractors or engineering vendors."

    # A hypothesis is not grounds for disqualification (rubric L514-515).
    inferred = score_qualification(
        QualificationInput(
            segment="digital_agency",
            findings=[hypothesis("H9", "business_model", statement)],
        )
    )
    assert inferred.disqualification_triggers == ()

    # The same statement as a verified fact is a hard disqualifier (L501-502).
    verified = score_qualification(
        QualificationInput(
            segment="digital_agency",
            findings=[fact("V9", "business_model", statement, url="https://agency.example/about")],
        )
    )
    assert [t.code for t in verified.disqualification_triggers] == ["DQ_REFUSES_EXTERNAL_SUPPLIERS"]
    assert verified.disqualification_triggers[0].evidence_finding_ids == ("V9",)
    assert verified.tier == "disqualified"


def test_enterprise_scale_mismatch_fires_when_headcount_is_verified():
    card = score_qualification(
        QualificationInput(
            segment="digital_agency",
            employee_estimate=220,
            findings=[
                fact("T1", "team_structure", "Team of 220 employees across three offices.",
                     url="https://big-agency.example/about")
            ],
        )
    )

    assert [t.code for t in card.disqualification_triggers] == ["DQ_ENTERPRISE_SCALE_MISMATCH"]
    assert card.tier == "disqualified"
    assert card.dimension("business_size").points_awarded == 0.0


# ---------------------------------------------------------------------------
# 8. Business Size bands (rubric L224-228) and DB payload shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "estimate, expected_points",
    [
        (None, 0.0),
        (1, 0.0),      # L224 solo practitioner
        (2, 3.0),      # L225
        (4, 3.0),
        (5, 6.0),      # L226
        (9, 6.0),
        (10, 8.0),     # L227
        (24, 8.0),
        (25, 10.0),    # L228
        (50, 10.0),
        (51, 0.0),     # undefined band -> zero-evidence rule (L398-400)
        (100, 0.0),
        (101, 0.0),    # L224 enterprise
    ],
)
def test_business_size_band_mapping(estimate, expected_points):
    card = score_qualification(
        QualificationInput(segment="webflow_studio", employee_estimate=estimate)
    )
    dim = card.dimension("business_size")
    assert dim.points_awarded == expected_points, dim.rationale
    if estimate is None:
        assert dim.confidence == "unverified"
    else:
        # A profile estimate alone is a medium indicator (rubric L358).
        assert dim.confidence == "medium"


def test_db_record_mirrors_lead_qualifications_constraints():
    card = score_qualification(happy_path_input())
    record = card.to_db_record()

    assert record["review_status"] == "draft"
    assert record["qualification_tier"] in {
        "tier_1_priority",
        "tier_2_prospective",
        "tier_3_deferred",
        "disqualified",
    }
    assert isinstance(record["disqualification_reasons"], list)
    assert record["disqualification_reasons"] == []
    assert record["disqualification_notes"] is None
    assert record["recommended_next_action"] == card.recommended_action

    score_columns = [
        "customer_flow_score",
        "development_demand_score",
        "repeatability_orders_score",
        "technical_fit_score",
        "business_size_score",
        "decision_maker_accessibility_score",
        "commercial_fit_score",
    ]
    # chk_lead_qual_score_sum (crm/schema.sql L545-555)
    assert record["overall_score"] == sum(record[column] for column in score_columns)

    # Trigger L1279-1299: evidence must be non-null whenever a score is positive.
    assert set(record["dimension_evidence"]) == set(rubric.DIMENSION_BY_KEY)
    for key, evidence in record["dimension_evidence"].items():
        if record[f"{key}_score"] > 0:
            assert evidence["evidence"], f"{key} has a positive score without evidence"
            assert evidence["finding_ids"]
        else:
            assert evidence["evidence"] is None
        assert evidence["max"] == rubric.DIMENSION_BY_KEY[key].max_points

    # Payload must be JSON-serialisable for PostgREST insert.
    json.dumps(record)


def test_payload_for_empty_evidence_has_no_positive_scores():
    record = score_qualification(QualificationInput(findings=[])).to_db_record()

    assert record["overall_score"] == 0.0
    assert record["qualification_tier"] == "disqualified"
    assert record["disqualification_reasons"] == []
    assert item_is_null(record["dimension_evidence"])


def item_is_null(dimension_evidence):
    return all(entry["evidence"] is None for entry in dimension_evidence.values())
