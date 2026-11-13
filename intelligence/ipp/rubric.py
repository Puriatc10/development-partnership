"""Declarative transcription of ``agents/qualification/ipp-scoring-rubric-v2.md``.

Every point value in this module is copied from the rubric document; nothing is
invented. Each rule carries the rubric line range it transcribes so a reviewer
can diff the engine against the specification line by line.

Design note — how free-text findings become anchors
---------------------------------------------------
The input contract (task specification) fixes the finding record to
``finding_id / category / finding_type / confidence / statement / evidence_url``
with no numeric score field. The engine therefore classifies each finding
against the *rubric's own anchor wording*:

* ``categories`` — the ``finding_category_type`` values whose rubric evidence
  examples feed that dimension (e.g. ``client_roster`` feeds Customer Flow,
  per rubric L99-102).
* ``any_patterns`` / ``all_patterns`` — literal phrases taken from the anchor
  descriptions (rubric L94-97, L126-129, ...). A rule is satisfied only when a
  finding of an allowed category contains the anchor's own vocabulary.
* ``min_findings`` — where the rubric states a countable example
  ("1–2 projects" L94, "3–5 verifiable ... case studies" L95), the count is
  taken literally.

Scores emitted are always anchor values. Intermediate scores are allowed by
rubric L316-318 but require a written human justification, so the engine never
manufactures them — human review may adjust.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, FrozenSet, Optional, Tuple

import re

RUBRIC_VERSION = "2.0.1"


# ---------------------------------------------------------------------------
# Dimensions — rubric L60-69 (weights and schema column names)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionSpec:
    key: str
    label: str
    max_points: float
    schema_column: str
    rubric_lines: str
    categories: FrozenSet[str]


CUSTOMER_FLOW = "customer_flow"
DEVELOPMENT_DEMAND = "development_demand"
REPEATABILITY_ORDERS = "repeatability_orders"
TECHNICAL_FIT = "technical_fit"
BUSINESS_SIZE = "business_size"
DECISION_MAKER_ACCESSIBILITY = "decision_maker_accessibility"
COMMERCIAL_FIT = "commercial_fit"

DIMENSIONS: Tuple[DimensionSpec, ...] = (
    DimensionSpec(
        key=CUSTOMER_FLOW,
        label="Customer Flow",
        max_points=20,
        schema_column="customer_flow_score",
        rubric_lines="L62, L83-97",
        categories=frozenset({"client_roster", "growth_signals"}),
    ),
    DimensionSpec(
        key=DEVELOPMENT_DEMAND,
        label="Development Demand",
        max_points=20,
        schema_column="development_demand_score",
        rubric_lines="L63, L115-129",
        categories=frozenset({"technology_stack", "pain_points"}),
    ),
    DimensionSpec(
        key=REPEATABILITY_ORDERS,
        label="Repeatability of Orders",
        max_points=15,
        schema_column="repeatability_orders_score",
        rubric_lines="L64, L147-161",
        categories=frozenset({"business_model", "client_roster"}),
    ),
    DimensionSpec(
        key=TECHNICAL_FIT,
        label="Technical Fit",
        max_points=15,
        schema_column="technical_fit_score",
        rubric_lines="L65, L179-196",
        categories=frozenset({"technology_stack", "scoping_maturity"}),
    ),
    DimensionSpec(
        key=BUSINESS_SIZE,
        label="Business Size",
        max_points=10,
        schema_column="business_size_score",
        rubric_lines="L66, L214-228",
        categories=frozenset({"team_structure"}),
    ),
    DimensionSpec(
        key=DECISION_MAKER_ACCESSIBILITY,
        label="Decision-Maker Accessibility",
        max_points=10,
        schema_column="decision_maker_accessibility_score",
        rubric_lines="L67, L248-262",
        categories=frozenset({"team_structure", "other"}),
    ),
    DimensionSpec(
        key=COMMERCIAL_FIT,
        label="Commercial Fit",
        max_points=10,
        schema_column="commercial_fit_score",
        rubric_lines="L68, L281-295",
        categories=frozenset({"pricing_rates", "business_model"}),
    ),
)

DIMENSION_BY_KEY: Dict[str, DimensionSpec] = {d.key: d for d in DIMENSIONS}


# ---------------------------------------------------------------------------
# Tiers — rubric L537-542 ("Score Bands and PostgreSQL Enums")
# ---------------------------------------------------------------------------
# L538: 80-100 -> tier_1_priority | L540: 60-79 -> tier_2_prospective
# L541: 40-59 -> tier_3_deferred  | L542: <40 or DQ -> disqualified
TIER_PRIORITY = "tier_1_priority"
TIER_PROSPECTIVE = "tier_2_prospective"
TIER_DEFERRED = "tier_3_deferred"
TIER_DISQUALIFIED = "disqualified"

TIER_LABELS: Dict[str, str] = {
    TIER_PRIORITY: "Tier 1 (Priority)",          # rubric L539
    TIER_PROSPECTIVE: "Tier 2 (Prospective)",    # rubric L540
    TIER_DEFERRED: "Tier 3 (Deferred)",          # rubric L541
    TIER_DISQUALIFIED: "Disqualified",           # rubric L542
}

TIER_THRESHOLD_PRIORITY = 80.0   # rubric L539
TIER_THRESHOLD_PROSPECTIVE = 60.0  # rubric L540
TIER_THRESHOLD_DEFERRED = 40.0   # rubric L542 / trigger L1303


def tier_for_score(total_score: float, has_disqualifier: bool = False) -> str:
    """Map a total to ``qualification_tier_type`` exactly as the trigger does.

    Transcribed from rubric L537-542 and mirrored in
    ``fn_enforce_lead_qualification_rules`` (crm/schema.sql L1301-1319), which
    forces ``disqualified`` whenever a disqualification reason exists or the
    total is below 40.
    """
    if has_disqualifier or total_score < TIER_THRESHOLD_DEFERRED:
        return TIER_DISQUALIFIED
    if total_score >= TIER_THRESHOLD_PRIORITY:
        return TIER_PRIORITY
    if total_score >= TIER_THRESHOLD_PROSPECTIVE:
        return TIER_PROSPECTIVE
    return TIER_DEFERRED


# ---------------------------------------------------------------------------
# Anchor rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorRule:
    """One documented rubric anchor, expressed as a testable predicate."""

    dimension: str
    points: float
    rubric_lines: str
    attainment: str
    unmet_note: str
    categories: FrozenSet[str]
    any_patterns: Tuple[str, ...] = ()
    all_patterns: Tuple[str, ...] = ()
    min_findings: int = 1
    support_category: Optional[str] = None
    support_patterns: Tuple[str, ...] = ()


@lru_cache(maxsize=None)
def compiled(pattern: str) -> "re.Pattern[str]":
    """Compile-once cache so rule evaluation is cheap and side-effect free."""
    return re.compile(pattern, re.IGNORECASE)


# Shared fragments ----------------------------------------------------------

_BUILDER_RX = r"\bwebflow\b|\bframer\b|visual (?:builder|development)"
_BACKEND_RX = (
    r"\bbackend\b|supabase|postgres|relational database|database architecture"
    r"|custom api|headless|react component|external database"
)
_EXEC_RX = (
    r"\bfounders?\b|\bceo\b|\bcto\b|\bcoo\b|managing director"
    r"|head of (?:delivery|engineering|technology)|technical director|agency partner"
)
_DIRECT_ROUTE_RX = (
    r"linkedin|inmail|direct (?:personal )?email|personal inbox|direct line"
    r"|direct phone|founder inbox|personal (?:email|profile)"
)
_PREMIUM_RX = (
    r"\$\s?(?:2[05]|[3-9]\d)\s*k\b"          # $25k / $40k / $49k project minimums (L293)
    r"|\$\s?\d{2},\d{3}"                      # $25,000
    r"|\$\s?\d{3}\s*(?:/|per)\s*(?:hr|hour)"  # $150/hr (L293)
    r"|\b1[0-9]{2}\s*(?:-|to)\s*[0-9]{2,3}\s*(?:/|per)?\s*(?:hr|hour)"  # $100-$150/hr
    r"|premium (?:positioning|rates|pricing|budget|market positioning)"
)
_STANDARD_COMMERCIAL_RX = (
    r"\$\s?(?:5|10|15)\s*k\b"                 # $5k-$10k modest minimums (L292)
    r"|project minimum"
    r"|standard commercial (?:rates|billing)"
    r"|b2b billing"
    r"|hourly rates?"
)

# Development Demand, L128-129 ("complex or repeated technical work across
# multiple projects", "documented continuous demand ... bottlenecks")
_COMPLEX_TECH_RX = (
    r"(?:customer|client|gated) portal|dashboard|multi-source|headless cms"
    r"|data (?:sync|migration)|api middleware|bespoke (?:calculator|tool|software)"
    r"|authentication flows?|database architecture|platform migrations?"
)


# 1. Customer Flow — rubric L92-97 -----------------------------------------
CUSTOMER_FLOW_RULES: Tuple[AnchorRule, ...] = (
    AnchorRule(
        dimension=CUSTOMER_FLOW,
        points=20,
        rubric_lines="L97",
        attainment=(
            "verified active commercial pipeline (quarterly case studies, enterprise client "
            "roster, public hiring or client intake signals)"
        ),
        unmet_note=(
            "Anchor 20 not awarded: real-time pipeline volume and public intake/hiring signals "
            "were not verified (rubric L97)."
        ),
        categories=DIMENSION_BY_KEY[CUSTOMER_FLOW].categories,
        any_patterns=(
            r"quarterly",
            r"enterprise (?:client|customer)s? roster",
            r"client intake",
            r"public hiring",
            r"high delivery volume",
            r"(?:20|30|40|50|100|200)\+?\s+(?:projects|clients|brands)",
            r"dozens of (?:projects|clients)",
            r"multiple launches per quarter",
        ),
        min_findings=3,
    ),
    AnchorRule(
        dimension=CUSTOMER_FLOW,
        points=15,
        rubric_lines="L96",
        attainment=(
            "strong evidence of ongoing corporate/B2B customers and multiple projects "
            "(continuous stream of verified case studies or recognized brands)"
        ),
        unmet_note=(
            "Anchor 15 not awarded: ongoing corporate/B2B cadence across multiple projects "
            "was not demonstrated (rubric L96)."
        ),
        categories=DIMENSION_BY_KEY[CUSTOMER_FLOW].categories,
        min_findings=6,
    ),
    AnchorRule(
        dimension=CUSTOMER_FLOW,
        points=15,
        rubric_lines="L96",
        attainment=(
            "ongoing corporate/B2B client work across multiple verifiable projects "
            "(continuous stream or recognized brands)"
        ),
        unmet_note=(
            "Anchor 15 not awarded: ongoing corporate/B2B cadence across multiple projects "
            "was not demonstrated (rubric L96)."
        ),
        categories=DIMENSION_BY_KEY[CUSTOMER_FLOW].categories,
        any_patterns=(
            r"ongoing (?:client|customer|work|engagement|roster)",
            r"continuous (?:stream|pipeline|client)",
            r"recurring (?:client|customer|work)",
            r"recognized brands?",
            r"client testimonials?",
            r"enterprise (?:clients?|customers?)",
            r"live corporate b2b",
            r"active client roster",
        ),
        min_findings=3,
    ),
    AnchorRule(
        dimension=CUSTOMER_FLOW,
        points=10,
        rubric_lines="L95",
        attainment=(
            "multiple identifiable real clients (rubric example: 3-5 verifiable commercial "
            "client case studies published within the last 12-18 months)"
        ),
        unmet_note=(
            "Anchor 10 not awarded: fewer than three verifiable commercial client case studies "
            "were supplied (rubric L95)."
        ),
        categories=DIMENSION_BY_KEY[CUSTOMER_FLOW].categories,
        min_findings=3,
    ),
    AnchorRule(
        dimension=CUSTOMER_FLOW,
        points=5,
        rubric_lines="L94",
        attainment=(
            "a few limited or weakly attributable client examples (rubric example: 1-2 projects)"
        ),
        unmet_note=(
            "Anchor 5 not awarded: no verified commercial client example of any kind "
            "(rubric L93-L94)."
        ),
        categories=DIMENSION_BY_KEY[CUSTOMER_FLOW].categories,
        min_findings=1,
    ),
)


# 2. Development Demand — rubric L124-129 ---------------------------------
DEVELOPMENT_DEMAND_RULES: Tuple[AnchorRule, ...] = (
    AnchorRule(
        dimension=DEVELOPMENT_DEMAND,
        points=20,
        rubric_lines="L129",
        attainment=(
            "documented continuous demand for custom backend/logic work coupled with "
            "verifiable technical delivery bottlenecks or capacity constraints"
        ),
        unmet_note=(
            "Anchor 20 not awarded: no verifiable delivery bottleneck or capacity constraint "
            "supports unmet demand (rubric L129, L143, L422)."
        ),
        categories=DIMENSION_BY_KEY[DEVELOPMENT_DEMAND].categories,
        any_patterns=(_COMPLEX_TECH_RX,),
        support_category="pain_points",
        support_patterns=(
            r"bottleneck",
            r"capacity (?:constraint|limit|issue)",
            r"overflow",
            r"backlog",
            r"delayed (?:delivery|launch|project)",
            r"engineering hiring",
            r"open (?:role|position) for (?:a )?(?:backend|software|full-stack) engineer",
            r"subcontract",
        ),
    ),
    AnchorRule(
        dimension=DEVELOPMENT_DEMAND,
        points=15,
        rubric_lines="L128",
        attainment=(
            "complex or repeated technical work (portals, dashboards, data sync, headless CMS, "
            "API middleware, platform migrations)"
        ),
        unmet_note=(
            "Anchor 15 not awarded: no repeated or complex engineering work (portals, "
            "dashboards, data sync, migrations) was documented (rubric L128)."
        ),
        categories=DIMENSION_BY_KEY[DEVELOPMENT_DEMAND].categories,
        any_patterns=(_COMPLEX_TECH_RX,),
    ),
    AnchorRule(
        dimension=DEVELOPMENT_DEMAND,
        points=10,
        rubric_lines="L127",
        attainment=(
            "projects involving third-party APIs, external databases, member authentication "
            "or custom JavaScript/React application logic"
        ),
        unmet_note=(
            "Anchor 10 not awarded: no API, external database, authentication or custom "
            "application logic was documented (rubric L127)."
        ),
        categories=DIMENSION_BY_KEY[DEVELOPMENT_DEMAND].categories,
        any_patterns=(
            r"\bapis?\b",
            r"third-party integration",
            r"external database",
            r"authentication",
            r"(?:member|user) (?:portal|login)",
            r"\breact\b",
            r"database integration",
            r"supabase",
            r"postgres",
            r"airtable",
            r"xano",
            r"custom (?:javascript|react) application",
        ),
    ),
    AnchorRule(
        dimension=DEVELOPMENT_DEMAND,
        points=5,
        rubric_lines="L126",
        attainment=(
            "occasional simple custom code (snippets, embed widgets, form webhooks, CSS overrides)"
        ),
        unmet_note=(
            "Anchor 5 not awarded: no custom code of any kind was verified "
            "(rubric L125-L126)."
        ),
        categories=DIMENSION_BY_KEY[DEVELOPMENT_DEMAND].categories,
        any_patterns=(
            r"custom (?:code|script|javascript)",
            r"embed(?:ded)? (?:widget|script)",
            r"webhook",
            r"form validation",
            r"css (?:override|styling)",
        ),
    ),
)


# 3. Repeatability of Orders — rubric L156-161 ----------------------------
REPEATABILITY_RULES: Tuple[AnchorRule, ...] = (
    AnchorRule(
        dimension=REPEATABILITY_ORDERS,
        points=15,
        rubric_lines="L161",
        attainment=(
            "stable recurring commercial model (dedicated retainers, continuous platform "
            "evolution) across the primary client base"
        ),
        unmet_note=(
            "Anchor 15 not awarded: recurring work across the primary client base "
            "(two or more documented engagements) was not verified (rubric L161)."
        ),
        categories=DIMENSION_BY_KEY[REPEATABILITY_ORDERS].categories,
        any_patterns=(
            r"primary client base",
            r"most (?:clients|engagements)",
            r"all (?:clients|engagements)",
            r"dedicated retainers?",
            r"continuous platform evolution",
            r"recurring technical work",
        ),
        min_findings=2,
    ),
    AnchorRule(
        dimension=REPEATABILITY_ORDERS,
        points=12,
        rubric_lines="L160",
        attainment=(
            "verified long-term contracts, recurring monthly retainers or multi-year rollout "
            "agreements with corporate clients"
        ),
        unmet_note=(
            "Anchor 12 not awarded: formal retainer, monthly recurring or multi-year contract "
            "terms were not verified (rubric L160, L419)."
        ),
        categories=DIMENSION_BY_KEY[REPEATABILITY_ORDERS].categories,
        any_patterns=(
            r"monthly retainer",
            r"recurring retainer",
            r"recurring monthly",
            r"annual retainer",
            r"multi-year",
            r"long-term (?:contract|agreement)",
            r"retained (?:them|us) for",
        ),
    ),
    AnchorRule(
        dimension=REPEATABILITY_ORDERS,
        points=8,
        rubric_lines="L159",
        attainment=(
            "ongoing maintenance, feature iteration or continuous development documented for "
            "a subset of clients"
        ),
        unmet_note=(
            "Anchor 8 not awarded: ongoing maintenance or continuous development for any client "
            "subset was not documented (rubric L159)."
        ),
        categories=DIMENSION_BY_KEY[REPEATABILITY_ORDERS].categories,
        any_patterns=(
            r"ongoing maintenance",
            r"continuous (?:platform|development|feature)",
            r"feature iteration",
            r"multi-phase",
            r"ongoing feature",
            r"continuous optimization",
            r"platform evolution",
        ),
    ),
    AnchorRule(
        dimension=REPEATABILITY_ORDERS,
        points=4,
        rubric_lines="L158",
        attainment=(
            "occasional post-delivery support, warranty periods or sporadic maintenance requests"
        ),
        unmet_note=(
            "Anchor 4 not awarded: no post-delivery support, warranty or maintenance evidence "
            "(rubric L158)."
        ),
        categories=DIMENSION_BY_KEY[REPEATABILITY_ORDERS].categories,
        any_patterns=(
            r"post-?launch support",
            r"warrant(?:y|ies)",
            r"maintenance",
            r"ongoing support",
            r"support contract",
        ),
    ),
)


# 4. Technical Fit — rubric L191-196 --------------------------------------
TECHNICAL_FIT_RULES: Tuple[AnchorRule, ...] = (
    AnchorRule(
        dimension=TECHNICAL_FIT,
        points=15,
        rubric_lines="L196",
        attainment=(
            "strong technical overlap (Webflow/Framer + backend, APIs, databases or React) "
            "plus documented, standardized development workflows"
        ),
        unmet_note=(
            "Anchor 15 not awarded: standardized workflow evidence (Client-First, design system, "
            "component library, Git/GitHub management, structured handoff) was not documented "
            "(rubric L196)."
        ),
        categories=DIMENSION_BY_KEY[TECHNICAL_FIT].categories,
        all_patterns=(
            _BUILDER_RX,
            _BACKEND_RX,
            (
                r"client-first|design system|component-?(?:driven|library|boilerplate)"
                r"|github|git flow|code review|figma (?:handoff|hand-off)"
                r"|structured (?:workflow|handoff)|documented workflow"
            ),
        ),
    ),
    AnchorRule(
        dimension=TECHNICAL_FIT,
        points=12,
        rubric_lines="L195",
        attainment=(
            "verified Webflow/Framer development combined with documented needs for backend "
            "architecture, custom APIs, external databases or bespoke React components"
        ),
        unmet_note=(
            "Anchor 12 not awarded: Webflow/Framer delivery combined with backend, API, "
            "database or React work was not documented (rubric L195)."
        ),
        categories=DIMENSION_BY_KEY[TECHNICAL_FIT].categories,
        all_patterns=(_BUILDER_RX, _BACKEND_RX),
    ),
    AnchorRule(
        dimension=TECHNICAL_FIT,
        points=8,
        rubric_lines="L194",
        attainment=(
            "some overlap: standard Webflow or Framer builds with occasional external API requests"
        ),
        unmet_note=(
            "Anchor 8 not awarded: no Webflow/Framer delivery with external API requests "
            "(rubric L194)."
        ),
        categories=DIMENSION_BY_KEY[TECHNICAL_FIT].categories,
        all_patterns=(_BUILDER_RX, r"\bapis?\b|integrat(?:ion|ions)"),
    ),
    AnchorRule(
        dimension=TECHNICAL_FIT,
        points=4,
        rubric_lines="L193",
        attainment=(
            "limited compatibility: visual-builder delivery (closed ecosystem, rare custom code)"
        ),
        unmet_note=(
            "Anchor 4 not awarded: no Webflow/Framer or comparable visual-builder delivery "
            "was documented (rubric L192-L193)."
        ),
        categories=DIMENSION_BY_KEY[TECHNICAL_FIT].categories,
        any_patterns=(_BUILDER_RX,),
    ),
)

# Rubric L192: zero points for stacks outside the three strategic priorities.
OUT_OF_SCOPE_STACK_RX = (
    r"\bwordpress\b|\bphp\b|\bdrupal\b|shopify liquid|magento|squarespace|\bwix\b"
    r"|\bunity\b|native (?:ios|android)"
)


# 5. Business Size — rubric L223-228 (bands applied to the profile headcount) --
# (min_inclusive, max_inclusive, points, rubric_line, description)
BUSINESS_SIZE_BANDS: Tuple[Tuple[int, int, float, str, str], ...] = (
    (2, 4, 3.0, "L225", "very small micro-team (2-4 core people)"),          # L225
    (5, 9, 6.0, "L226", "small boutique studio (5-9 people)"),               # L226
    (10, 24, 8.0, "L227", "mid-sized studio inside the core sweet spot (10-24 people)"),  # L227
    (25, 50, 10.0, "L228", "established studio across the upper sweet spot (25-50 people)"),  # L228
)

# Rubric L224: solo practitioner (1 person) and >100 employees score zero.
BUSINESS_SIZE_SOLO_MAX = 1        # L224
BUSINESS_SIZE_ENTERPRISE_MIN = 100  # L224 ("large agency (>100 employees)")
BUSINESS_SIZE_SWEET_SPOT_MAX = 50   # L217 / L228

# Headcount extraction from team_structure findings (used only when the profile
# carries no employee_estimate; rubric L231-233 lists team pages as acceptable
# evidence for headcount).
HEADCOUNT_RANGE_RX = (
    r"(\d{1,3})\s*(?:-|\u2013|to)\s*(\d{1,3})\s*(?:core\s+)?"
    r"(?:people|employees|team members|specialists|makers|professionals)"
)
HEADCOUNT_SINGLE_PATTERNS: Tuple[str, ...] = (
    r"team of\s+(\d{1,3})",
    r"(?:headcount|team size) of\s+(\d{1,3})",
    r"(\d{1,3})\s+(?:core\s+)?(?:people|employees|team members|specialists|makers|professionals)",
)


# 6. Decision-Maker Accessibility — rubric L258-262 -----------------------
DECISION_MAKER_RULES: Tuple[AnchorRule, ...] = (
    AnchorRule(
        dimension=DECISION_MAKER_ACCESSIBILITY,
        points=10,
        rubric_lines="L262",
        attainment=(
            "verifiable warm introduction, prior direct relationship or confirmed prior "
            "positive interaction"
        ),
        unmet_note=(
            "Anchor 10 not awarded: no warm introduction or prior relationship is verified; "
            "cold routes cap at anchor 8 (rubric L262)."
        ),
        categories=DIMENSION_BY_KEY[DECISION_MAKER_ACCESSIBILITY].categories,
        any_patterns=(
            r"warm (?:intro|introduction)",
            r"prior (?:direct )?(?:relationship|interaction|contact)",
            r"mutual contact",
            r"existing relationship",
            r"referred by",
            r"met (?:at|with)",
        ),
    ),
    AnchorRule(
        dimension=DECISION_MAKER_ACCESSIBILITY,
        points=8,
        rubric_lines="L261",
        attainment=(
            "direct personal route to the decision-maker (verified personal email or active "
            "LinkedIn/InMail channel)"
        ),
        unmet_note=(
            "Anchor 8 not awarded: no direct personal route (personal email, active LinkedIn) "
            "to a named executive was verified (rubric L261, L480)."
        ),
        categories=DIMENSION_BY_KEY[DECISION_MAKER_ACCESSIBILITY].categories,
        all_patterns=(_EXEC_RX, _DIRECT_ROUTE_RX),
    ),
    AnchorRule(
        dimension=DECISION_MAKER_ACCESSIBILITY,
        points=6,
        rubric_lines="L260",
        attainment=(
            "valid company-level channel (official inquiry/partnership inbox or contact form)"
        ),
        unmet_note=(
            "Anchor 6 not awarded: no company-level contact channel was verified "
            "(rubric L260, L428)."
        ),
        categories=DIMENSION_BY_KEY[DECISION_MAKER_ACCESSIBILITY].categories,
        any_patterns=(
            r"info@|contact@|hello@|partnership@",
            r"contact form",
            r"inquiry form",
            r"company (?:email|channel|contact)",
            r"general (?:email|inbox)",
        ),
    ),
    AnchorRule(
        dimension=DECISION_MAKER_ACCESSIBILITY,
        points=3,
        rubric_lines="L259",
        attainment="relevant founder/executive identified by name and role, without a direct channel",
        unmet_note=(
            "Anchor 3 not awarded: no founder/executive is identified by name and role "
            "(rubric L259)."
        ),
        categories=DIMENSION_BY_KEY[DECISION_MAKER_ACCESSIBILITY].categories,
        all_patterns=(_EXEC_RX,),
    ),
)


# 7. Commercial Fit — rubric L291-295 -------------------------------------
COMMERCIAL_FIT_RULES: Tuple[AnchorRule, ...] = (
    AnchorRule(
        dimension=COMMERCIAL_FIT,
        points=10,
        rubric_lines="L295",
        attainment=(
            "explicitly verified acceptance of a white-label/wholesale partnership model"
        ),
        unmet_note=(
            "Anchor 10 not awarded: no verified white-label/wholesale acceptance was documented; "
            "willingness remains an information gap (rubric L295, L425)."
        ),
        categories=DIMENSION_BY_KEY[COMMERCIAL_FIT].categories,
        any_patterns=(
            r"white-?label",
            r"wholesale (?:partner|model|development|agreement|terms)",
            r"behind-the-scenes",
            r"managed development partner",
        ),
    ),
    AnchorRule(
        dimension=COMMERCIAL_FIT,
        points=7,
        rubric_lines="L294",
        attainment=(
            "premium positioning plus explicit evidence of engaging external software "
            "developers/engineering contractors"
        ),
        unmet_note=(
            "Anchor 7 not awarded: no evidence of external software-engineering contracting "
            "(a general creative/freelancer network does not qualify) (rubric L294, L434)."
        ),
        categories=DIMENSION_BY_KEY[COMMERCIAL_FIT].categories,
        all_patterns=(
            _PREMIUM_RX,
            (
                r"external (?:software )?(?:developer|engineer|engineering|contractor)"
                r"|engineering (?:contractor|partner)"
                r"|subcontract\w*|outsourc\w* (?:software|development|engineering)"
                r"|technical (?:vendor|development) partner"
            ),
        ),
    ),
    AnchorRule(
        dimension=COMMERCIAL_FIT,
        points=5,
        rubric_lines="L293",
        attainment=(
            "premium market positioning with documented premium budget indicators "
            "(e.g. $20k-$25k+ minimums or $100-$150+/hr rates)"
        ),
        unmet_note=(
            "Anchor 5 not awarded: premium pricing/margin room was not verified "
            "(rubric L293)."
        ),
        categories=DIMENSION_BY_KEY[COMMERCIAL_FIT].categories,
        any_patterns=(_PREMIUM_RX,),
    ),
    AnchorRule(
        dimension=COMMERCIAL_FIT,
        points=3,
        rubric_lines="L292",
        attainment=(
            "limited commercial signal: standard B2B billing (e.g. $5k-$10k minimums) without "
            "premium cushion"
        ),
        unmet_note=(
            "Anchor 3 not awarded: no commercial billing or pricing signal was verified "
            "(rubric L292)."
        ),
        categories=DIMENSION_BY_KEY[COMMERCIAL_FIT].categories,
        any_patterns=(_STANDARD_COMMERCIAL_RX,),
    ),
)


ANCHOR_RULES: Dict[str, Tuple[AnchorRule, ...]] = {
    CUSTOMER_FLOW: CUSTOMER_FLOW_RULES,
    DEVELOPMENT_DEMAND: DEVELOPMENT_DEMAND_RULES,
    REPEATABILITY_ORDERS: REPEATABILITY_RULES,
    TECHNICAL_FIT: TECHNICAL_FIT_RULES,
    DECISION_MAKER_ACCESSIBILITY: DECISION_MAKER_RULES,
    COMMERCIAL_FIT: COMMERCIAL_FIT_RULES,
    BUSINESS_SIZE: (),
}


# ---------------------------------------------------------------------------
# Hard disqualifiers — rubric section 6.1 (L494-511)
# ---------------------------------------------------------------------------
# Every rule below transcribes one of the five numbered conditions. They only
# fire on ``verified_fact`` findings: L514 requires verifiable, documented
# evidence, and L515 forbids using an information gap as grounds for automatic
# disqualification.


@dataclass(frozen=True)
class DisqualifierRule:
    code: str
    description: str
    rubric_lines: str
    any_patterns: Tuple[str, ...] = ()
    all_patterns: Tuple[str, ...] = ()


DISQUALIFIER_RULES: Tuple[DisqualifierRule, ...] = (
    DisqualifierRule(
        code="DQ_PROHIBITED_SEGMENT",
        description=(
            "Segment is outside the approved target segments: solitary freelancer / independent "
            "consultant without commercial leverage"
        ),
        rubric_lines="L497-500 (6.1.1)",
    ),
    DisqualifierRule(
        code="DQ_ENTERPRISE_SCALE_MISMATCH",
        description=(
            "Enterprise holding company / large agency (>100 employees) with entrenched vendor "
            "approval layers"
        ),
        rubric_lines="L500 (6.1.1), L224",
    ),
    DisqualifierRule(
        code="DQ_REFUSES_EXTERNAL_SUPPLIERS",
        description=(
            "Explicit, public policy of never working with external contractors or engineering "
            "vendors"
        ),
        rubric_lines="L501-502 (6.1.2)",
        any_patterns=(
            r"100\s?%\s*in-?house",
            r"no outsourcing",
            r"(?:never|not|zero)\s+(?:work with|use|engage)\s+(?:any\s+)?external",
            r"refuses? to (?:use|work with) (?:any )?external",
            r"do not (?:work with )?(?:external )?(?:contractors|vendors)",
        ),
    ),
    DisqualifierRule(
        code="DQ_COMMERCIAL_MODEL_INCOMPATIBILITY",
        description=(
            "Demands unpaid speculative builds, equity-only compensation, commodity offshore "
            "rate expectations, or refuses written SOWs/deposits"
        ),
        rubric_lines="L503-506 (6.1.3)",
        any_patterns=(
            r"equity[- ]only",
            r"unpaid (?:speculative|pilot|build|work|development)",
            r"spec(?:ulative)? work",
            r"(?:refuses?|will not sign|unwilling to sign)[^.]{0,40}(?:sow|scope of work|deposit)",
            r"no (?:upfront )?deposit",
            r"lowest (?:hourly )?rates?",
            r"offshore rates?",
        ),
    ),
    DisqualifierRule(
        code="DQ_OUT_OF_SCOPE_SERVICE_DEMAND",
        description=(
            "Requirements exclusively demand out-of-scope services (native mobile, legacy "
            "monoliths, ERP, blockchain)"
        ),
        rubric_lines="L507-508 (6.1.4)",
        all_patterns=(
            r"(?:exclusively|only|solely)\s+(?:require|need|demand|seek|want)",
            (
                r"native (?:ios|android|mobile)|\bswift\b|\bkotlin\b|smart contracts?"
                r"|blockchain|\bdrupal\b|php monolith|erp implementation|\bsap\b"
            ),
        ),
    ),
    DisqualifierRule(
        code="DQ_COMMERCIAL_INTEGRITY_CONCERN",
        description=(
            "Documented unpaid vendor claims, supplier litigation, refusal to sign mutual "
            "non-circumvention, or fraudulent practices"
        ),
        rubric_lines="L509-510 (6.1.5)",
        any_patterns=(
            r"unpaid vendor",
            r"litigation",
            r"lawsuit|sued (?:by|for)",
            r"refuses? to sign[^.]{0,40}non-circumvention",
            r"fraud",
            r"chargeback dispute",
        ),
    ),
)

# rubric L497-500: segments that are categorically outside the approved target
# list. ``independent_consultant`` is the schema's own label for the solitary
# freelancer condition in 6.1.1. The "future expansion" segments
# (ecommerce_agency, small_saas, other_recurring) are NOT disqualified: rubric
# L541 reserves them for tier 3 deferral instead.
PROHIBITED_SEGMENTS: FrozenSet[str] = frozenset({"independent_consultant"})


# ---------------------------------------------------------------------------
# Confidence vocabulary — rubric L353 / crm/schema.sql L201-206
# ---------------------------------------------------------------------------

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_UNVERIFIED = "unverified"

# Ordering helper for "weakest link" comparisons between confidence levels.
CONFIDENCE_RANK: Dict[str, int] = {
    CONFIDENCE_UNVERIFIED: 0,
    CONFIDENCE_LOW: 1,
    CONFIDENCE_MEDIUM: 2,
    CONFIDENCE_HIGH: 3,
}
