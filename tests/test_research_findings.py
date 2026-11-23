"""Findings derivation, offline: pages in, grounded claims out.

The load-bearing test in this file is the grounding one. Everything else describes coverage; that
one describes the property the engine is sold on — a claim without a verbatim quote cannot exist,
so a fabricated client, headcount or price is structurally impossible rather than merely unlikely.
"""

from __future__ import annotations

from intelligence.ipp.models import FINDING_CATEGORIES, FINDING_TYPES
from research.findings import (
    KIND_CAPS,
    MAX_FINDINGS,
    derive_findings,
    find_information_gaps,
    make_finding,
    opening_excerpt,
)
from research.research_types import (
    DraftFinding,
    PageContent,
    excerpt_is_grounded,
    grounded,
    normalise,
)

HOME = "https://studio.example/"

HOME_TEXT = (
    "Studio North is a digital studio in Lisbon. "
    "We design and build Webflow sites for fintech and healthcare brands. "
    "Our clients include Acme Bank, Beta Health and Gamma Labs. "
    "We are a team of 12 designers and engineers. "
    "We work in two-week sprints with a discovery phase before every build. "
    "We use Framer and React for prototypes. "
    "Email hello@studionorth.example to start a conversation."
)

SILENT_TEXT = (
    "Northwind Studio makes calm websites for people who like calm websites. "
    "We are a small team with big ambitions and a tidy studio in Porto. "
    "Read our notes and say hello when you are passing through the neighbourhood."
)


def page(url: str = HOME, text: str = HOME_TEXT, **kwargs) -> PageContent:
    return PageContent(url=url, title=kwargs.pop("title", "Studio North"), description="",
                       text=text, **kwargs)


def categories_of(findings: list[DraftFinding]) -> set[str]:
    return {finding.category for finding in findings}


def statements_of(findings: list[DraftFinding], category: str) -> list[str]:
    return [finding.statement for finding in findings if finding.category == category]


# -----------------------------------------------------------------------------
# Grounding: the anti-hallucination rule
# -----------------------------------------------------------------------------


def test_every_derived_finding_is_grounded_in_the_page_it_cites():
    source = page()
    findings = derive_findings([source])

    assert findings, "the fixture page must yield findings, or the assertion below proves nothing"
    for finding in findings:
        assert finding.source_url == HOME
        assert len(normalise(finding.excerpt)) >= 12
        assert excerpt_is_grounded(finding.excerpt, source.text), finding.statement
    assert grounded(findings, [source]) == findings


def test_a_fabricated_excerpt_is_refused_by_make_finding():
    source = page()

    fabricated = make_finding(
        "Pricing starts at $5,000 per month",
        "Our retainer starts at $5,000 per month.",
        source,
        category="pricing_rates",
        finding_type="verified_fact",
        confidence="high",
        kind="pricing",
    )

    assert fabricated is None, "an excerpt the page never states must not become a finding"


def test_a_grounded_excerpt_is_accepted_verbatim():
    source = page()

    finding = make_finding(
        "States a service offering",
        "We design and build Webflow sites for fintech and healthcare brands.",
        source,
        category="business_model",
        finding_type="verified_fact",
        confidence="medium",
        kind="service",
    )

    assert finding is not None
    assert finding.excerpt in normalise(source.text)


def test_make_finding_refuses_a_category_outside_the_deployed_vocabulary():
    try:
        make_finding(
            "statement long enough to pass",
            "We design and build Webflow sites for fintech",
            page(),
            category="vibes",
            finding_type="verified_fact",
            confidence="medium",
            kind="service",
        )
    except ValueError as exc:
        assert "finding_category_type" in str(exc)
    else:  # pragma: no cover - the raise is the contract
        raise AssertionError("an undeployed category must raise")


# -----------------------------------------------------------------------------
# Named clients
# -----------------------------------------------------------------------------


def test_named_clients_only_when_the_text_names_them():
    findings = derive_findings([page()])
    statements = statements_of(findings, "client_roster")

    assert any("Acme Bank" in statement for statement in statements)
    assert any("Beta Health" in statement for statement in statements)
    assert all(finding.confidence == "high" for finding in findings
               if finding.category == "client_roster")


def test_a_generic_audience_is_not_a_client_name():
    text = (
        "We are a studio for ambitious people. Trusted by startups and agencies worldwide, "
        "we build websites that convert visitors into customers."
    )

    findings = derive_findings([page(text=text)])

    assert statements_of(findings, "client_roster") == []


def test_a_client_is_never_invented_from_an_unrelated_sentence():
    text = (
        "We design websites for teams that care about typography. "
        "Our work has been featured in several places over the years."
    )

    findings = derive_findings([page(text=text)])

    assert statements_of(findings, "client_roster") == []


# -----------------------------------------------------------------------------
# Team size
# -----------------------------------------------------------------------------


def test_team_size_requires_a_number():
    findings = derive_findings([page(url=f"{HOME}about", text=SILENT_TEXT)])

    assert not [statement for statement in statements_of(findings, "team_structure")
                if "team of" in statement.lower()]


def test_team_size_is_high_confidence_when_the_number_is_stated():
    findings = derive_findings([page()])
    sizes = [finding for finding in findings if finding.statement.startswith("States a team size")]

    assert sizes, "a stated headcount must be captured"
    assert "12" in sizes[0].statement
    assert sizes[0].confidence == "high"
    assert sizes[0].category == "team_structure"


def test_a_hiring_number_is_a_growth_signal_not_a_headcount():
    # "hiring 2 engineers" is not "a team of 2": the headcount pattern must not take the bait.
    findings = derive_findings([page(text="We are hiring 2 engineers for our platform team.")])
    sizes = [finding for finding in findings if finding.statement.startswith("States a team size")]

    assert sizes == []


def test_a_currency_prefixed_figure_counts_as_a_stated_price():
    findings = derive_findings([page(text="Retainers start at EUR 4,000 per month. Get in touch.")])
    pricing = [finding for finding in findings if finding.category == "pricing_rates"]

    assert pricing
    assert pricing[0].confidence == "high"
    assert pricing[0].finding_type == "verified_fact"


# -----------------------------------------------------------------------------
# Pricing
# -----------------------------------------------------------------------------


def test_a_stated_price_becomes_a_high_confidence_finding():
    text = "Our engagements start with a discovery sprint. Retainers start at $4,500 per month."

    findings = derive_findings([page(text=text)])
    pricing = [finding for finding in findings if finding.category == "pricing_rates"]

    assert pricing
    assert pricing[0].confidence == "high"
    assert pricing[0].finding_type == "verified_fact"
    assert "$4,500" in pricing[0].excerpt


def test_a_pricing_gap_is_emitted_when_the_pages_are_silent():
    source = page(url=f"{HOME}about", text=SILENT_TEXT)

    findings = derive_findings([source])
    gaps = [finding for finding in findings if finding.finding_type == "information_gap_tbd"]

    assert [gap.category for gap in gaps] == ["pricing_rates", "team_structure", "technology_stack"]
    assert all(gap.confidence == "unverified" for gap in gaps)
    assert all(excerpt_is_grounded(gap.excerpt, source.text) for gap in gaps)


def test_no_pricing_gap_when_a_figure_is_published():
    text = "Our monthly retainer is $3,200 for a reserved development slot. Get in touch."

    findings = derive_findings([page(text=text)])
    gaps = [finding for finding in findings if finding.category == "pricing_rates"
            and finding.finding_type == "information_gap_tbd"]

    assert gaps == []


def test_pricing_language_without_a_figure_is_medium_confidence():
    text = "We work on a monthly retainer so your roadmap keeps moving between launches."

    findings = derive_findings([page(text=text)])
    pricing = [finding for finding in findings if finding.category == "pricing_rates"]

    assert pricing
    assert pricing[0].confidence == "medium"


# -----------------------------------------------------------------------------
# Technology, services, process, growth, contact
# -----------------------------------------------------------------------------


def test_technology_mentions_map_to_the_stack_category():
    findings = derive_findings([page()])
    stack = statements_of(findings, "technology_stack")

    assert any("Webflow" in statement for statement in stack)
    assert any("React" in statement for statement in stack)
    assert all(
        finding.finding_type == "verified_fact"
        for finding in findings
        if finding.category == "technology_stack" and finding.finding_type != "information_gap_tbd"
    )


def test_services_map_to_business_model_and_process_to_scoping_maturity():
    findings = derive_findings([page()])

    services = statements_of(findings, "business_model")
    process = statements_of(findings, "scoping_maturity")

    assert any("service offering" in statement for statement in services)
    assert any("process" in statement for statement in process)
    assert all(
        finding.kind != "gap"
        for finding in findings
        if finding.category == "business_model"
    )


def test_growth_language_is_a_hypothesis_until_it_carries_a_number():
    vague = derive_findings([page(text="We are hiring across the studio and always keen to talk.")])
    numbered = derive_findings([page(text="We are hiring 3 engineers for our platform team.")])

    vague_growth = [finding for finding in vague if finding.category == "growth_signals"]
    numbered_growth = [finding for finding in numbered if finding.category == "growth_signals"]

    assert vague_growth and vague_growth[0].finding_type == "hypothesis"
    assert vague_growth[0].confidence == "low"
    assert numbered_growth
    assert numbered_growth[0].finding_type == "verified_fact"
    assert numbered_growth[0].confidence == "high"


def test_contact_channels_are_grounded_and_categorised():
    findings = derive_findings([page()])
    contact = [finding for finding in findings if finding.category == "business_model"
               and "contact email" in finding.statement]

    assert contact
    assert "hello@studionorth.example" in contact[0].statement
    assert contact[0].confidence == "high"


def test_every_category_and_type_is_a_deployed_value():
    findings = derive_findings([page()]) + derive_findings([page(text=SILENT_TEXT)])

    assert {finding.category for finding in findings} <= set(FINDING_CATEGORIES)
    assert {finding.finding_type for finding in findings} <= set(FINDING_TYPES)


# -----------------------------------------------------------------------------
# Dedupe, caps, determinism
# -----------------------------------------------------------------------------


def test_the_same_statement_from_two_pages_collapses_to_one():
    sentence = "We design and build Webflow sites for fintech and healthcare brands."
    first = page(url=HOME, text=f"Studio North builds calm things. {sentence}")
    second = page(url=f"{HOME}about", text=f"About us. {sentence}")

    findings = derive_findings([first, second])
    matching = [finding for finding in findings if sentence in finding.statement]

    assert len(matching) == 1


def test_per_kind_and_total_caps_hold():
    pages = [
        page(
            url=f"{HOME}work/case-{index}",
            text=f"We build platform number {index} for teams that need one. " * 3,
        )
        for index in range(12)
    ]

    findings = derive_findings(pages)

    assert len(findings) <= MAX_FINDINGS
    services = [finding for finding in findings if finding.kind == "service"]
    assert len(services) <= KIND_CAPS["service"]


def test_findings_are_deterministic_across_runs():
    pages = [page(), page(url=f"{HOME}about", text=SILENT_TEXT)]

    first = derive_findings(pages)
    second = derive_findings(list(pages))

    assert first == second
    assert [finding.statement for finding in first] == [finding.statement for finding in second]


def test_an_empty_corpus_produces_no_findings_and_no_gaps():
    assert derive_findings([]) == []
    assert derive_findings([page(text="   ")]) == []


def test_gaps_need_a_corpus_to_be_measured_against():
    assert find_information_gaps([], []) == []


def test_opening_excerpt_is_a_verbatim_prefix():
    source = page()

    excerpt = opening_excerpt(source)

    assert excerpt
    assert excerpt_is_grounded(excerpt, source.text)
    assert normalise(source.text).startswith(excerpt)

# ---------------------------------------------------------------------------
# Precision: shaped-like-evidence that is not evidence
# ---------------------------------------------------------------------------


def test_hero_copy_is_not_read_as_a_client_list():
    """"trusted by teams who need Design Assets Brand Templates" names nobody."""
    content = page(
        "https://studio.example.com/",
        "Trusted by teams who need Design Assets Brand Templates faster and cheaper.",
    )
    assert [f for f in derive_findings([content]) if f.kind == "client"] == []


def test_a_case_study_names_the_client_and_is_not_a_service_offering():
    content = page(
        "https://studio.example.com/work",
        "We delivered rapid UI prototyping for Xero, helping them scale to 4.2M subscribers.",
    )
    findings = derive_findings([content])
    clients = [f.statement for f in findings if f.kind == "portfolio"]
    services = [f.statement for f in findings if f.kind == "service"]
    assert any("Xero" in statement for statement in clients)
    assert not any("Xero" in statement for statement in services)


def test_a_phone_number_is_read_from_the_tel_link_and_quoted_from_the_page():
    content = page(
        "https://studio.example.com/contact",
        "Call us on +1 415 831 7880 during business hours.",
        phones=("+14158317880",),
    )
    findings = [f for f in derive_findings([content]) if "phone number" in f.statement]
    assert len(findings) == 1
    assert "415 831 7880" in findings[0].excerpt


def test_a_tel_number_the_page_never_prints_is_dropped():
    content = page(
        "https://studio.example.com/contact",
        "Reach the team through the form below and we will reply.",
        phones=("+14158317880",),
    )
    assert [f for f in derive_findings([content]) if "phone number" in f.statement] == []


def test_an_obfuscated_tel_link_is_not_recorded_as_a_number():
    content = page(
        "https://studio.example.com/contact",
        "Our number appears after you enable JavaScript.",
        phones=("+141****7880",),
    )
    assert [f for f in derive_findings([content]) if "phone number" in f.statement] == []


def test_role_titles_keep_their_acronyms():
    content = page(
        "https://studio.example.com/team",
        "Our leadership team includes the CEO and the CTO of the studio.",
    )
    statements = [
        f.statement for f in derive_findings([content]) if f.category == "team_structure"
    ]
    assert any("CEO" in statement for statement in statements)
    assert not any("Ceo" in statement for statement in statements)
