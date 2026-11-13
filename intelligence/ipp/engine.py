"""Deterministic IPP v2 scoring engine (Eryndex qualification layer).

Public entry point: :func:`score_qualification`.

Invariants enforced here (all traceable to the rubric document):

* **Determinism / no clock** — the output is a pure function of
  ``QualificationInput``. Findings are canonicalised by ``finding_id`` before
  evaluation, so even the order of the input list cannot change the result.
* **Zero-Evidence Rule** (rubric L397-409) — an anchor is awarded only when a
  ``verified_fact`` satisfies it. Unknown parameters score zero and are
  reported as gaps, never as silent credit.
* **Hypotheses never substitute for facts** (rubric L341) — hypothesis-only
  evidence earns partial credit capped at that dimension's *lowest positive
  anchor*, and always with ``low`` confidence.
* **Human gate** (rubric L46, L554, L879-882) — every scorecard is a draft
  (``review_status='draft'``) and carries ``needs_human_review=True``; there is
  no code path that produces an approved decision.

No network, no database, no third-party imports.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

from . import rubric
from .models import (
    CONFIDENCE_LEVELS,
    FINDING_CATEGORIES,
    FINDING_TYPES,
    MARKET_SEGMENTS,
    DimensionScore,
    DisqualificationTrigger,
    Finding,
    QualificationInput,
    QualificationScorecard,
)

logger = logging.getLogger("ipp.engine")

VERIFIED_FACT = "verified_fact"
HYPOTHESIS = "hypothesis"
INFORMATION_GAP = "information_gap_tbd"

# Only these two classifications may ever influence a score (rubric L335-345).
SCORING_TYPES = (VERIFIED_FACT, HYPOTHESIS)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_input(inp: QualificationInput) -> Tuple[Finding, ...]:
    """Validate the input against the schema enums and check constraints.

    Raises:
        ValueError: on any invalid enum value, missing evidence for a
            ``verified_fact`` (mirrors ``chk_verified_fact_requires_evidence``,
            crm/schema.sql L495-498), duplicate finding ids, or a non-positive
            employee estimate.
    """
    if not isinstance(inp, QualificationInput):
        raise ValueError("score_qualification expects a QualificationInput instance")

    if inp.segment is not None and inp.segment not in MARKET_SEGMENTS:
        raise ValueError(
            f"Invalid segment '{inp.segment}'. Allowed market_segment_type values: {MARKET_SEGMENTS}"
        )

    if inp.employee_estimate is not None:
        if isinstance(inp.employee_estimate, bool) or not isinstance(inp.employee_estimate, int):
            raise ValueError("employee_estimate must be an integer headcount or None")
        if inp.employee_estimate < 1:
            raise ValueError("employee_estimate must be a positive headcount")

    seen: Dict[str, Finding] = {}
    for index, finding in enumerate(inp.findings):
        if not isinstance(finding, Finding):
            raise ValueError(f"findings[{index}] must be a Finding instance")
        if not finding.finding_id or not finding.finding_id.strip():
            raise ValueError(f"findings[{index}] requires a non-empty 'finding_id'")
        if finding.finding_id in seen:
            raise ValueError(f"Duplicate finding_id '{finding.finding_id}'")
        seen[finding.finding_id] = finding

        if finding.category not in FINDING_CATEGORIES:
            raise ValueError(
                f"Finding '{finding.finding_id}' has invalid category '{finding.category}'. "
                f"Allowed finding_category_type values: {FINDING_CATEGORIES}"
            )
        if finding.finding_type not in FINDING_TYPES:
            raise ValueError(
                f"Finding '{finding.finding_id}' has invalid finding_type '{finding.finding_type}'. "
                f"Allowed research_finding_type values: {FINDING_TYPES}"
            )
        if finding.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(
                f"Finding '{finding.finding_id}' has invalid confidence '{finding.confidence}'. "
                f"Allowed confidence_level_type values: {CONFIDENCE_LEVELS}"
            )
        if not finding.statement or not finding.statement.strip():
            raise ValueError(f"Finding '{finding.finding_id}' requires a non-empty statement")

        # chk_verified_fact_requires_evidence (crm/schema.sql L495-498):
        #   finding_type != 'verified_fact' OR
        #   (evidence_url IS NOT NULL AND confidence IN ('high', 'medium'))
        if finding.finding_type == VERIFIED_FACT:
            if not finding.evidence_url or not finding.evidence_url.strip():
                raise ValueError(
                    f"Finding '{finding.finding_id}' is a 'verified_fact' and therefore requires "
                    "an evidence_url (chk_verified_fact_requires_evidence, crm/schema.sql L495-498)"
                )
            if finding.confidence not in (rubric.CONFIDENCE_HIGH, rubric.CONFIDENCE_MEDIUM):
                raise ValueError(
                    f"Finding '{finding.finding_id}' is a 'verified_fact' and therefore requires "
                    "confidence 'high' or 'medium' (chk_verified_fact_requires_evidence)"
                )

    return tuple(sorted(inp.findings, key=lambda f: f.finding_id))


# ---------------------------------------------------------------------------
# Anchor evaluation
# ---------------------------------------------------------------------------


def _statement_matches(statement: str, any_patterns: Sequence[str], all_patterns: Sequence[str]) -> bool:
    if all_patterns and not all(rubric.compiled(p).search(statement) for p in all_patterns):
        return False
    if any_patterns and not any(rubric.compiled(p).search(statement) for p in any_patterns):
        return False
    return True


def _rule_hit(rule: rubric.AnchorRule, pool: Sequence[Finding]) -> Optional[Tuple[Finding, ...]]:
    """Return the findings that satisfy ``rule`` within ``pool`` (or ``None``).

    ``pool`` is already filtered by evidence classification, so the same rule
    evaluated over facts-only versus facts+hypotheses yields the fact/hypothesis
    distinction required by rubric L341.
    """
    scope = [f for f in pool if f.category in rule.categories]
    if len(scope) < rule.min_findings:
        return None

    if rule.any_patterns or rule.all_patterns:
        matched = [
            f
            for f in scope
            if rule.any_patterns and any(rubric.compiled(p).search(f.statement) for p in rule.any_patterns)
        ]
        if rule.any_patterns and not matched:
            return None
    else:
        matched = list(scope)

    for pattern in rule.all_patterns:
        hitters = [f for f in scope if rubric.compiled(pattern).search(f.statement)]
        if not hitters:
            return None
        for hitter in hitters:
            if hitter not in matched:
                matched.append(hitter)

    if rule.support_category is not None:
        support = [
            f
            for f in pool
            if f.category == rule.support_category
            and any(rubric.compiled(p).search(f.statement) for p in rule.support_patterns)
        ]
        if not support:
            return None
        for supporter in support:
            if supporter not in matched:
                matched.append(supporter)

    # Anchor counts are stated in the rubric ("1-2 projects" L94, "3-5 ... case
    # studies" L95); top up so the cited evidence set is never thinner than the
    # anchor it justifies.
    if len(matched) < rule.min_findings:
        for candidate in scope:
            if candidate not in matched:
                matched.append(candidate)
            if len(matched) >= rule.min_findings:
                break

    return tuple(sorted(matched, key=lambda f: f.finding_id))


def _best_hit(
    rules: Sequence[rubric.AnchorRule], pool: Sequence[Finding]
) -> Optional[Tuple[rubric.AnchorRule, Tuple[Finding, ...]]]:
    """Highest satisfied anchor. Rules are declared strongest-first, so ties
    resolve to the first declaration and evaluation is deterministic."""
    best: Optional[Tuple[rubric.AnchorRule, Tuple[Finding, ...]]] = None
    for rule in rules:
        hit = _rule_hit(rule, pool)
        if hit is None:
            continue
        if best is None or rule.points > best[0].points:
            best = (rule, hit)
    return best


def _lowest_positive(rules: Sequence[rubric.AnchorRule]) -> float:
    positives = [r.points for r in rules if r.points > 0]
    return min(positives) if positives else 0.0


def _next_unmet_note(rules: Sequence[rubric.AnchorRule], points: float) -> Optional[str]:
    """Explanation of the lowest anchor above ``points`` that was not reached."""
    candidates = [r for r in rules if r.points > points]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.points).unmet_note


def _describe(findings: Sequence[Finding]) -> str:
    return ", ".join(f"{f.finding_id} ({f.finding_type}/{f.confidence})" for f in findings)


# ---------------------------------------------------------------------------
# Dimension scoring
# ---------------------------------------------------------------------------


def _score_dimension(spec: rubric.DimensionSpec, findings: Sequence[Finding]) -> DimensionScore:
    rules = rubric.ANCHOR_RULES[spec.key]
    facts = tuple(f for f in findings if f.finding_type == VERIFIED_FACT)
    scoring_pool = tuple(f for f in findings if f.finding_type in SCORING_TYPES)
    gaps = tuple(
        f
        for f in findings
        if f.finding_type == INFORMATION_GAP and f.category in spec.categories
    )

    fact_hit = _best_hit(rules, facts)
    infer_hit = _best_hit(rules, scoring_pool)

    gap_notes: List[str] = []
    for gap in gaps:
        gap_notes.append(
            f"TBD: {gap.statement} (finding {gap.finding_id}) - information gap, never assumed "
            f"(rubric L342-345)."
        )

    if fact_hit is not None:
        rule, cited = fact_hit
        points = rule.points
        confidence = (
            rubric.CONFIDENCE_HIGH
            if any(f.confidence == rubric.CONFIDENCE_HIGH for f in cited)
            else rubric.CONFIDENCE_MEDIUM
        )
        rationale = (
            f"Anchor {rule.points:g} of {spec.max_points:g} awarded (rubric {rule.rubric_lines}): "
            f"{rule.attainment}. Supported by {_describe(cited)}."
        )
        anchor = rule.points
        rubric_lines = f"{spec.rubric_lines}; anchor {rule.rubric_lines}"
    elif infer_hit is not None:
        rule, cited = infer_hit
        cap = _lowest_positive(rules)
        points = min(cap, rule.points)
        confidence = rubric.CONFIDENCE_LOW
        rationale = (
            f"Partial credit {points:g} of {spec.max_points:g} (rubric {spec.rubric_lines}): "
            "only hypothesis-grade evidence was supplied, so credit is capped at the dimension's "
            "lowest positive anchor "
            f"({cap:g}) because hypotheses cannot substitute for empirical facts (rubric L341). "
            f"Hypothesis anchor attempted: {rule.points:g} ({rule.rubric_lines}); "
            f"basis {_describe(cited)}."
        )
        anchor = points
        rubric_lines = f"{spec.rubric_lines}; anchor {rule.rubric_lines}; cap rubric L341"
        gap_notes.append(
            "Score rests on a hypothesis, not a verified fact: require a first-party source "
            "before any outreach decision (rubric L341, L354)."
        )
    else:
        points = 0.0
        confidence = rubric.CONFIDENCE_UNVERIFIED
        rationale = (
            f"0 of {spec.max_points:g} points (Zero-Evidence Rule, rubric L398-400): no "
            f"verified finding satisfied any documented anchor for {spec.label.lower()} "
            f"(anchors: {spec.rubric_lines})."
        )
        anchor = 0.0
        rubric_lines = f"{spec.rubric_lines}; zero-evidence rubric L398-400"
        gap_notes.append(
            "Zero here means the parameter is unverified, not that the prospect was shown to "
            "lack it (rubric L357, L436-440)."
        )
        if spec.key == rubric.TECHNICAL_FIT:
            out_of_scope = [
                f for f in facts if rubric.compiled(rubric.OUT_OF_SCOPE_STACK_RX).search(f.statement)
            ]
            if out_of_scope:
                gap_notes.append(
                    "Documented stack appears to fall outside the three strategic priorities "
                    f"({', '.join(f.finding_id for f in out_of_scope)}), an explicit zero anchor "
                    "(rubric L192); confirm during human review before treating it as a hard "
                    "incompatibility."
                )

    unmet = _next_unmet_note(rules, points)
    if unmet is not None:
        gap_notes.append(unmet)

    cited_ids = tuple(f.finding_id for f in (fact_hit[1] if fact_hit else infer_hit[1] if infer_hit else ()))

    return DimensionScore(
        key=spec.key,
        label=spec.label,
        schema_column=spec.schema_column,
        points_awarded=float(points),
        max_points=float(spec.max_points),
        rationale=rationale,
        evidence_refs=cited_ids,
        confidence=confidence,
        gap_notes=tuple(dict.fromkeys(gap_notes)),
        anchor_points=float(anchor),
        rubric_lines=rubric_lines,
    )


# ---------------------------------------------------------------------------
# Business Size (profile headcount -> rubric bands)
# ---------------------------------------------------------------------------


def _parse_headcount(finding: Finding) -> Optional[int]:
    match = rubric.compiled(rubric.HEADCOUNT_RANGE_RX).search(finding.statement)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if 0 < low <= high <= 1000:
            return int(round((low + high) / 2))
    for pattern in rubric.HEADCOUNT_SINGLE_PATTERNS:
        single = rubric.compiled(pattern).search(finding.statement)
        if single:
            value = int(single.group(1))
            if 0 < value <= 1000:
                return value
    return None


def _resolve_headcount(
    inp: QualificationInput, findings: Sequence[Finding]
) -> Tuple[Optional[int], str, Optional[Finding]]:
    """Resolve the headcount used for Business Size.

    Returns ``(headcount, origin, corroborating_finding)`` where ``origin`` is
    one of ``profile_estimate``, ``verified_fact``, ``hypothesis`` or ``none``.
    The profile estimate wins when present (it is the curated company field),
    with findings used only for corroboration; rubric L358 classifies such an
    estimate as a ``medium`` third-party indicator.
    """
    team_findings = [f for f in findings if f.category == "team_structure"]
    corroborating = next(
        (f for f in team_findings if f.finding_type == VERIFIED_FACT and _parse_headcount(f) is not None),
        None,
    )

    if inp.employee_estimate is not None:
        return int(inp.employee_estimate), "profile_estimate", corroborating

    for finding in team_findings:
        if finding.finding_type == VERIFIED_FACT:
            parsed = _parse_headcount(finding)
            if parsed is not None:
                return parsed, "verified_fact", finding
    for finding in team_findings:
        if finding.finding_type == HYPOTHESIS:
            parsed = _parse_headcount(finding)
            if parsed is not None:
                return parsed, "hypothesis", finding
    return None, "none", None


def _score_business_size(
    spec: rubric.DimensionSpec,
    inp: QualificationInput,
    headcount: Optional[int],
    origin: str,
    corroborating: Optional[Finding],
) -> DimensionScore:
    gap_notes: List[str] = []
    root_gap = (
        "Headcount is unresolved: unknown team size scores zero positive points "
        "(rubric L224, L239, L429-431)."
    )

    if headcount is None:
        gap_notes.extend([root_gap, "Anchor 3 not awarded: no verifiable team size (rubric L225)."])
        return DimensionScore(
            key=spec.key,
            label=spec.label,
            schema_column=spec.schema_column,
            points_awarded=0.0,
            max_points=float(spec.max_points),
            rationale=(
                "0 of 10 points (Zero-Evidence Rule, rubric L398-400): team size and operational "
                "status are unverified; 'uncertain operations' can never be scored (rubric L239)."
            ),
            evidence_refs=(),
            confidence=rubric.CONFIDENCE_UNVERIFIED,
            gap_notes=tuple(dict.fromkeys(gap_notes)),
            anchor_points=0.0,
            rubric_lines=f"{spec.rubric_lines}; zero-evidence rubric L398-400",
        )

    points = 0.0
    line = "L224"
    description = ""
    for low, high, band_points, band_line, band_description in rubric.BUSINESS_SIZE_BANDS:
        if low <= headcount <= high:
            points, line, description = band_points, band_line, band_description
            break

    if points == 0.0:
        if headcount <= rubric.BUSINESS_SIZE_SOLO_MAX:
            line = "L224"
            description = "solo practitioner with zero commercial leverage"
            gap_notes.append(
                "Solo practitioner (1 person) is an explicit zero anchor (rubric L224)."
            )
        elif headcount > rubric.BUSINESS_SIZE_ENTERPRISE_MIN:
            line = "L224"
            description = "enterprise-scale agency (>100 employees) with in-house engineering"
            gap_notes.append(
                "Headcount above 100 is an explicit zero anchor and a 6.1.1 scale mismatch "
                "(rubric L224, L242, L500)."
            )
        else:
            line = "L224; no band defined for 51-100 (rubric L225-228)"
            description = "headcount outside every documented anchor band"
            gap_notes.append(
                "The rubric defines bands only for 2-4, 5-9, 10-24, 25-50 and treats >100 as an "
                "extreme mismatch (rubric L225-228, L224); 51-100 is undefined, so the "
                "Zero-Evidence Rule applies and no points are awarded (rubric L398-400)."
            )
    else:
        withheld = _next_band_note(points)
        if withheld:
            gap_notes.append(withheld)

    if origin == "profile_estimate" and corroborating is None:
        confidence = rubric.CONFIDENCE_MEDIUM
        gap_notes.append(
            "Headcount comes from the profile estimate only (third-party/self-reported range, a "
            "medium indicator per rubric L358); corroborate with a first-party team page."
        )
    elif origin == "hypothesis":
        confidence = rubric.CONFIDENCE_LOW
        gap_notes.append(
            "Headcount is inferred from a hypothesis, not a verified team page (rubric L341)."
        )
    elif origin == "verified_fact":
        confidence = rubric.CONFIDENCE_HIGH
    elif corroborating is not None:
        confidence = (
            rubric.CONFIDENCE_HIGH
            if corroborating.confidence == rubric.CONFIDENCE_HIGH
            else rubric.CONFIDENCE_MEDIUM
        )
    else:
        confidence = rubric.CONFIDENCE_MEDIUM

    cited = [corroborating] if corroborating is not None else []
    rationale = (
        f"Anchor {points:g} of 10 awarded (rubric {line}): {description}; headcount "
        f"{headcount} (origin: {origin})."
    )
    if cited:
        rationale += f" Corroborated by {_describe(cited)}."

    return DimensionScore(
        key=spec.key,
        label=spec.label,
        schema_column=spec.schema_column,
        points_awarded=float(points),
        max_points=float(spec.max_points),
        rationale=rationale,
        evidence_refs=tuple(f.finding_id for f in cited),
        confidence=confidence,
        gap_notes=tuple(dict.fromkeys(gap_notes)),
        anchor_points=float(points),
        rubric_lines=f"{spec.rubric_lines}; anchor {line}",
    )


def _next_band_note(points: float) -> Optional[str]:
    higher = [band for band in rubric.BUSINESS_SIZE_BANDS if band[2] > points]
    if not higher:
        return None
    _, _, _, band_line, band_description = min(higher, key=lambda b: b[2])
    return (
        f"Anchor {min(b[2] for b in higher):g} not awarded: headcount does not reach the "
        f"{band_description} band (rubric {band_line})."
    )


# ---------------------------------------------------------------------------
# Disqualifiers — rubric section 6.1
# ---------------------------------------------------------------------------


def _detect_disqualifiers(
    inp: QualificationInput,
    findings: Sequence[Finding],
    headcount: Optional[int],
    headcount_origin: str,
    corroborating: Optional[Finding],
) -> Tuple[DisqualificationTrigger, ...]:
    """Fire hard disqualifiers. Only verified facts may trigger one (L514-515)."""
    facts = [f for f in findings if f.finding_type == VERIFIED_FACT]
    fired: List[DisqualificationTrigger] = []

    for rule in rubric.DISQUALIFIER_RULES:
        if rule.code == "DQ_PROHIBITED_SEGMENT":
            if inp.segment in rubric.PROHIBITED_SEGMENTS:
                fired.append(
                    DisqualificationTrigger(
                        code=rule.code,
                        description=rule.description,
                        rubric_lines=rule.rubric_lines,
                    )
                )
            continue

        if rule.code == "DQ_ENTERPRISE_SCALE_MISMATCH":
            # L514: the scale claim must itself be verifiable. Either the headcount
            # came straight from a first-party team page, or the profile estimate
            # is corroborated by a verified team_structure finding.
            corroborated = headcount_origin == "verified_fact" or (
                headcount_origin == "profile_estimate" and corroborating is not None
            )
            if headcount is not None and corroborated and headcount > rubric.BUSINESS_SIZE_ENTERPRISE_MIN:
                cited = tuple(
                    f.finding_id
                    for f in facts
                    if f.category == "team_structure" and _parse_headcount(f) is not None
                )
                fired.append(
                    DisqualificationTrigger(
                        code=rule.code,
                        description=rule.description,
                        rubric_lines=rule.rubric_lines,
                        evidence_finding_ids=cited,
                    )
                )
            continue

        cited = [
            f
            for f in facts
            if _statement_matches(f.statement, rule.any_patterns, rule.all_patterns)
        ]
        if cited:
            fired.append(
                DisqualificationTrigger(
                    code=rule.code,
                    description=rule.description,
                    rubric_lines=rule.rubric_lines,
                    evidence_finding_ids=tuple(sorted(f.finding_id for f in cited)),
                )
            )

    return tuple(fired)


# ---------------------------------------------------------------------------
# Aggregate reporting
# ---------------------------------------------------------------------------


def _overall_confidence(dimensions: Sequence[DimensionScore], fact_count: int) -> str:
    """Documented aggregate rule (deterministic, no weighting invented).

    ``unverified`` when no verified fact exists; otherwise scaled by how many
    dimensions carry high/medium confidence evidence. A scorecard can only be
    ``high`` when all seven dimensions rest on verified evidence.
    """
    verified_dims = sum(
        1
        for d in dimensions
        if d.confidence in (rubric.CONFIDENCE_HIGH, rubric.CONFIDENCE_MEDIUM)
    )
    if fact_count == 0 or verified_dims == 0:
        return rubric.CONFIDENCE_UNVERIFIED
    if verified_dims <= 3:
        return rubric.CONFIDENCE_LOW
    if verified_dims <= 6:
        return rubric.CONFIDENCE_MEDIUM
    return rubric.CONFIDENCE_HIGH


def _recommended_action(scorecard_tier: str, triggers: Sequence[DisqualificationTrigger], insufficient: bool, verified_dims: int) -> str:
    if triggers:
        codes = ", ".join(t.code for t in triggers)
        action = (
            f"Do not pursue: hard disqualifier(s) {codes} are recorded on this scorecard. "
            "Retain as a disqualified record for human confirmation (rubric L514, L542, L560)."
        )
    elif scorecard_tier == rubric.TIER_PRIORITY:
        action = (
            "Route to human operator review; after endorsement, prepare personalized outreach "
            "limited to Project-Based Wholesale Development (rubric L38-40, L539, L553-556)."
        )
    elif scorecard_tier == rubric.TIER_PROSPECTIVE:
        action = (
            "Route to human operator review; viable for exploratory outreach after endorsement, "
            "closing the listed evidence gaps first (rubric L540)."
        )
    elif scorecard_tier == rubric.TIER_DEFERRED:
        action = (
            "Retain in CRM and defer outreach until primary segments are exhausted; human review "
            "must decide whether the gaps are closable (rubric L541)."
        )
    else:
        action = (
            "Total below the 40-point viability threshold: retained as disqualified pending human "
            "review (rubric L542)."
        )

    if insufficient:
        action += (
            f" Evidence is insufficient ({verified_dims} of 7 dimensions rest on verified facts): "
            "treat this scorecard as inconclusive rather than as a negative finding "
            "(rubric L357, L436-440)."
        )
    return action


def _missing_evidence(dimensions: Sequence[DimensionScore], findings: Sequence[Finding]) -> Tuple[str, ...]:
    items: List[str] = []
    for finding in findings:
        if finding.finding_type == INFORMATION_GAP:
            items.append(
                f"TBD [{finding.category}] {finding.statement} (finding {finding.finding_id}) - "
                "information_gap_tbd scores zero (rubric L342-345)."
            )
    for dim in dimensions:
        if dim.points_awarded == 0:
            unmet = next(
                (note for note in reversed(dim.gap_notes) if note.startswith("Anchor")),
                "no documented anchor was satisfied by verified evidence",
            )
            items.append(f"[{dim.key}] {dim.max_points:g} points unearned: {unmet}")
    return tuple(dict.fromkeys(items))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score_qualification(inp: QualificationInput) -> QualificationScorecard:
    """Score a prospect against the IPP v2 rubric and return a *draft* scorecard.

    The result always requires human review; the engine never approves.
    """
    findings = _validate_input(inp)

    headcount, headcount_origin, corroborating = _resolve_headcount(inp, findings)

    dimensions: List[DimensionScore] = []
    for spec in rubric.DIMENSIONS:
        if spec.key == rubric.BUSINESS_SIZE:
            dimensions.append(
                _score_business_size(spec, inp, headcount, headcount_origin, corroborating)
            )
        else:
            dimensions.append(_score_dimension(spec, findings))

    total = round(sum(d.points_awarded for d in dimensions), 2)
    triggers = _detect_disqualifiers(inp, findings, headcount, headcount_origin, corroborating)
    tier = rubric.tier_for_score(total, has_disqualifier=bool(triggers))

    fact_count = sum(1 for f in findings if f.finding_type == VERIFIED_FACT)
    verified_dims = sum(
        1 for d in dimensions if d.confidence in (rubric.CONFIDENCE_HIGH, rubric.CONFIDENCE_MEDIUM)
    )
    insufficient = fact_count == 0 or verified_dims < 3
    confidence = _overall_confidence(dimensions, fact_count)

    scorecard = QualificationScorecard(
        dimensions=tuple(dimensions),
        total_score=total,
        tier=tier,
        tier_label=rubric.TIER_LABELS[tier],
        recommended_action=_recommended_action(tier, triggers, insufficient, verified_dims),
        disqualification_triggers=triggers,
        missing_evidence=_missing_evidence(dimensions, findings),
        overall_confidence=confidence,
        insufficient_evidence=insufficient,
        needs_human_review=True,
        review_status="draft",
        rubric_version=rubric.RUBRIC_VERSION,
        segment=inp.segment,
        region=inp.region,
        company_name=inp.company_name,
        findings=findings,
        verified_dimensions=verified_dims,
        verified_fact_count=fact_count,
    )

    logger.info(
        "IPP scorecard drafted (tier=%s total=%s confidence=%s dq=%d)",
        scorecard.tier,
        scorecard.total_score,
        scorecard.overall_confidence,
        len(triggers),
    )
    return scorecard
