"""First-party provider contract: it may only report what the pages it read actually say.

Everything here runs against fakes: a fetcher, a planner/crawler pair and a findings deriver,
all injected. That is deliberate twice over — these tests never touch the network, and they stay
runnable while ``research/http.py``, ``crawl.py`` and ``findings.py`` are being written in
parallel. What they pin down is the part that belongs to this provider: the honesty rules
(nothing cited that was not fetched, nothing green about a run that read nothing), determinism,
the confidence rule, and the ``RESEARCH_*`` environment contract.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from intelligence.ipp.models import CONFIDENCE_LEVELS, FINDING_CATEGORIES, FINDING_TYPES
from observability.run import RunContext
from research.executor import (
    ResearchPayload,
    ResearchProviderError,
    validate_payload,
)
from research.firstparty import (
    DEFAULT_DELAY_S,
    DEFAULT_MAX_PAGES,
    DEFAULT_TIMEOUT_S,
    FirstPartyResearchProvider,
    start_url_for,
)
from research.research_types import CrawlPlan, DraftFinding, PageContent, ResearchFetchError

COMPANY = {"name": "Studio Example", "primary_domain": "studio.example"}

HOME_URL = "https://studio.example/"
ABOUT_URL = "https://studio.example/about"
SERVICES_URL = "https://studio.example/services"
BROKEN_URL = "https://studio.example/broken"

HOME_TEXT = (
    "Studio Example designs and builds Webflow marketing sites for B2B software companies. "
    "The team keeps a small client roster on purpose and ships every project with a custom "
    "backend integration rather than a plugin."
)
ABOUT_TEXT = (
    "We are a team of four: one designer, two Webflow developers and one backend engineer, "
    "working from Lisbon and taking on three new projects a year."
)
SERVICES_TEXT = (
    "Our services include Webflow development, Framer builds and custom API integrations. "
    "Every engagement starts at a fixed project fee and hands over the code at the end."
)

HOME_EXCERPT = "designs and builds Webflow marketing sites for B2B software companies"
ABOUT_EXCERPT = "one designer, two Webflow developers and one backend engineer"
SERVICES_EXCERPT = "Webflow development, Framer builds and custom API integrations"


def page(url: str, title: str, text: str) -> PageContent:
    return PageContent(
        url=url, title=title, description="", text=text, fetched_at="2026-01-01T00:00:00Z"
    )


PAGES = (
    page(HOME_URL, "Studio Example", HOME_TEXT),
    page(ABOUT_URL, "About us", ABOUT_TEXT),
    page(SERVICES_URL, "Services", SERVICES_TEXT),
)


class FakeFetcher:
    """The two methods the provider may use, and nothing else.

    ``reason_for`` lets a test make one URL fail for a specific reason, which is how the
    "zero pages must name the reasons" rule gets exercised without any I/O.
    """

    def __init__(self, pages, *, reason_for: dict[str, str] | None = None) -> None:
        self.pages = {p.url: p for p in pages}
        self.reason_for = dict(reason_for or {})
        self.requested: list[str] = []

    def robots_allows(self, url: str) -> bool:
        return True

    def get(self, url: str):
        self.requested.append(url)
        if url not in self.pages:
            raise ResearchFetchError(url, self.reason_for.get(url, "http_error"), "not found")
        return self.pages[url]


def fake_plan_fn(urls):
    def plan_crawl(start_url, *, fetcher, max_pages):
        planned = tuple(urls)[: int(max_pages)]
        return CrawlPlan(start_url=start_url, pages=planned, reasons=dict.fromkeys(planned, "site"))

    return plan_crawl


def fake_crawl_fn():
    def crawl(plan, fetcher):
        pages, failures = [], []
        for url in plan.pages:
            try:
                pages.append(fetcher.get(url))
            except ResearchFetchError as exc:
                failures.append(exc)
        return SimpleNamespace(pages=tuple(pages), failures=tuple(failures), plan=plan)

    return crawl


def fake_derive_fn(drafts_by_url, extra=()):
    def derive_findings(pages):
        out = [draft for p in pages for draft in drafts_by_url.get(p.url, ())]
        out.extend(extra)
        return out

    return derive_findings


def verified(statement: str, excerpt: str, url: str, **overrides) -> DraftFinding:
    fields = {
        "statement": statement,
        "excerpt": excerpt,
        "source_url": url,
        "category": "technology_stack",
        "finding_type": "verified_fact",
        "confidence": "medium",
        "kind": "technology",
    }
    fields.update(overrides)
    return DraftFinding(**fields)


def six_verified() -> list[DraftFinding]:
    """Enough evidence for a ``high`` grade: six verified facts across three pages."""
    return [
        verified(f"Home fact {i}.", HOME_EXCERPT, HOME_URL) for i in range(2)
    ] + [
        verified(f"About fact {i}.", ABOUT_EXCERPT, ABOUT_URL) for i in range(2)
    ] + [
        verified(f"Services fact {i}.", SERVICES_EXCERPT, SERVICES_URL) for i in range(2)
    ]


def build_provider(
    *,
    pages=PAGES,
    planned=None,
    drafts=None,
    extra=(),
    reason_for=None,
    **kwargs,
):
    """Provider wired to fakes only — no sibling module is imported."""
    fetcher = FakeFetcher(pages, reason_for=reason_for)
    if drafts is None:
        drafts = {p.url: six_verified()[i * 2 : i * 2 + 2] for i, p in enumerate(pages)}
    urls = planned if planned is not None else [p.url for p in pages]
    provider = FirstPartyResearchProvider(
        fetcher=fetcher,
        plan_fn=fake_plan_fn(urls),
        crawl_fn=fake_crawl_fn(),
        derive_fn=fake_derive_fn(drafts, extra),
        **kwargs,
    )
    return provider, fetcher


# -----------------------------------------------------------------------------
# Identity and payload validity
# -----------------------------------------------------------------------------


def test_provider_identifies_itself_as_first_party_not_synthetic():
    from research.executor import ResearchProvider

    provider, _ = build_provider()

    assert provider.name == "firstparty"
    assert provider.synthetic is False
    assert isinstance(provider, ResearchProvider)


def test_payload_passes_the_executor_validators_and_its_own_enums():
    provider, _ = build_provider()

    payload = provider.produce(COMPANY)

    assert isinstance(payload, ResearchPayload)
    stored = validate_payload(payload)
    assert len(stored) == 6
    assert payload.summary.strip()
    for finding in payload.findings:
        assert finding.category in FINDING_CATEGORIES
        assert finding.finding_type in FINDING_TYPES
        assert finding.confidence in CONFIDENCE_LEVELS


def test_summary_states_how_many_pages_were_read_and_names_them():
    provider, _ = build_provider()

    summary = provider.produce(COMPANY).summary

    assert "read 3 page(s)" in summary
    for fetched in PAGES:
        assert fetched.url in summary
        assert fetched.title in summary
    # No characterisation of the company may appear: only the pages that were read.
    assert "designs and builds" not in summary


def test_sources_are_one_per_page_sorted_with_a_real_excerpt():
    provider, _ = build_provider()

    sources = provider.produce(COMPANY).sources

    assert [source.url for source in sources] == sorted(p.url for p in PAGES)
    assert len(sources) == len(PAGES)
    by_url = {p.url: p for p in PAGES}
    for source in sources:
        assert source.excerpt
        assert " ".join(source.excerpt.split()) in " ".join(by_url[source.url].text.split())


def test_every_grounded_draft_becomes_one_executor_finding():
    drafts = six_verified()
    provider = FirstPartyResearchProvider(
        fetcher=FakeFetcher(PAGES),
        plan_fn=fake_plan_fn([p.url for p in PAGES]),
        crawl_fn=fake_crawl_fn(),
        derive_fn=lambda pages: drafts,
    )

    payload = provider.produce(COMPANY)

    assert len(payload.findings) == len(drafts)
    assert {f.statement for f in payload.findings} == {d.statement for d in drafts}
    for finding in payload.findings:
        assert finding.evidence_url in {p.url for p in PAGES}
        assert finding.evidence_excerpt
        assert finding.finding_type == "verified_fact"


# -----------------------------------------------------------------------------
# Grounding is the admission rule
# -----------------------------------------------------------------------------


def test_an_ungrounded_finding_is_dropped_and_reported_as_dropped():
    invented = verified(
        "The studio is the largest Webflow partner in Europe.",
        "we are the largest Webflow partner in Europe",
        HOME_URL,
    )
    provider, _ = build_provider(extra=[invented])

    payload = provider.produce(COMPANY)

    statements = {finding.statement for finding in payload.findings}
    assert invented.statement not in statements
    assert len(payload.findings) == 6
    assert "did not survive grounding" in (payload.gaps_and_unknowns or "")


def test_a_finding_citing_a_page_that_was_not_fetched_is_dropped():
    """Defence in depth: ``grounded`` already refuses it, and this provider refuses it again."""
    unfetched = verified(
        "Careers page exists.", HOME_EXCERPT, "https://studio.example/careers"
    )
    provider, _ = build_provider(extra=[unfetched])

    payload = provider.produce(COMPANY)

    assert unfetched.statement not in {f.statement for f in payload.findings}
    assert {source.url for source in payload.sources} == {p.url for p in PAGES}


# -----------------------------------------------------------------------------
# Failure honesty
# -----------------------------------------------------------------------------


def test_zero_pages_raises_naming_every_reason_and_fetches_nothing_else():
    provider, fetcher = build_provider(
        pages=(),
        planned=[BROKEN_URL],
        reason_for={BROKEN_URL: "timeout"},
    )

    with pytest.raises(ResearchProviderError) as excinfo:
        provider.produce(COMPANY)

    message = str(excinfo.value)
    assert BROKEN_URL in message
    assert "timeout" in message
    assert fetcher.requested == [BROKEN_URL]


def test_a_partial_run_says_so_in_the_summary_and_the_gaps():
    provider, _ = build_provider(planned=[p.url for p in PAGES] + [BROKEN_URL])

    payload = provider.produce(COMPANY)

    gaps = payload.gaps_and_unknowns or ""
    assert "partial" in gaps
    assert "Unread page" in gaps and BROKEN_URL in gaps
    assert "could not be read" in payload.summary
    assert "partial" in payload.summary
    assert provider.last_run.partial is True
    assert provider.last_run.pages_failed == 1


def test_last_run_accounts_for_the_pages_actually_read():
    provider, _ = build_provider()

    provider.produce(COMPANY)
    stats = provider.last_run.as_dict()

    assert stats["pages_read"] == 3
    assert stats["pages_failed"] == 0
    assert stats["partial"] is False
    assert stats["findings"] == 6
    assert stats["verified_findings"] == 6


def test_no_grounded_findings_is_a_failure_not_a_thin_report():
    provider, _ = build_provider(
        drafts={p.url: [] for p in PAGES},
        extra=[verified("Invented.", "text that is on no page at all", HOME_URL)],
    )

    with pytest.raises(ResearchProviderError) as excinfo:
        provider.produce(COMPANY)

    assert "no admissible findings" in str(excinfo.value)


# -----------------------------------------------------------------------------
# Determinism
# -----------------------------------------------------------------------------


def test_the_same_pages_produce_an_identical_payload():
    first_provider, _ = build_provider()
    second_provider, _ = build_provider()

    first = first_provider.produce(COMPANY)
    second = second_provider.produce(COMPANY)
    again = first_provider.produce(COMPANY)

    assert first == second == again
    assert first.sources == second.sources
    assert first.findings == second.findings
    assert first.gaps_and_unknowns == second.gaps_and_unknowns


# -----------------------------------------------------------------------------
# Confidence
# -----------------------------------------------------------------------------


def test_confidence_is_high_only_with_breadth_and_six_verified_findings():
    provider, _ = build_provider()

    assert provider.produce(COMPANY).overall_confidence == "high"


def test_confidence_is_medium_when_few_findings_are_verified():
    drafts = {p.url: [] for p in PAGES}
    drafts[HOME_URL] = [verified("Only one fact.", HOME_EXCERPT, HOME_URL)]
    provider, _ = build_provider(drafts=drafts)

    payload = provider.produce(COMPANY)

    assert payload.overall_confidence == "medium"
    assert len(payload.findings) == 1


def test_confidence_is_never_high_when_a_planned_page_went_unread():
    provider, _ = build_provider(planned=[p.url for p in PAGES] + [BROKEN_URL])

    payload = provider.produce(COMPANY)

    assert payload.overall_confidence == "medium"
    assert provider.last_run.verified_findings == 6


# -----------------------------------------------------------------------------
# Gaps
# -----------------------------------------------------------------------------


def test_information_gap_findings_become_gaps_and_unknowns():
    gap = DraftFinding(
        statement="No pricing is published beyond the fixed-fee line.",
        excerpt=SERVICES_EXCERPT,
        source_url=SERVICES_URL,
        category="pricing_rates",
        finding_type="information_gap_tbd",
        confidence="unverified",
    )
    provider, _ = build_provider(extra=[gap])

    payload = provider.produce(COMPANY)

    gaps = payload.gaps_and_unknowns or ""
    assert f"Open question: {gap.statement}" in gaps
    # It stays a finding too: the gap is part of the evidence record.
    assert gap.statement in {f.statement for f in payload.findings}


# -----------------------------------------------------------------------------
# start URL
# -----------------------------------------------------------------------------


def test_start_url_prefers_the_website_then_falls_back_to_the_domain():
    assert start_url_for({"website_url": "https://site.example/#top"}) == "https://site.example/"
    assert start_url_for({"website_url": "site.example"}) == "https://site.example"
    assert start_url_for({"primary_domain": "studio.example"}) == "https://studio.example"


def test_start_url_refuses_a_company_with_no_site():
    with pytest.raises(ResearchProviderError) as excinfo:
        start_url_for({"name": "Nowhere"})

    assert "website_url" in str(excinfo.value)


# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------


def test_from_env_defaults_are_bounded_and_name_a_contact_url():
    provider = FirstPartyResearchProvider.from_env({})

    assert provider.name == "firstparty"
    assert "firstparty" in provider.user_agent
    assert "https://" in provider.user_agent
    assert provider.max_pages == DEFAULT_MAX_PAGES
    assert provider.timeout_s == DEFAULT_TIMEOUT_S
    assert provider.delay_s == DEFAULT_DELAY_S


def test_from_env_reads_overrides(monkeypatch):
    for key, value in {
        "RESEARCH_USER_AGENT": "eryndex-research/2.0 (+https://ops.example/bot)",
        "RESEARCH_MAX_PAGES": "4",
        "RESEARCH_TIMEOUT_S": "3.5",
        "RESEARCH_DELAY_S": "0",
    }.items():
        monkeypatch.setenv(key, value)

    provider = FirstPartyResearchProvider.from_env()

    assert provider.user_agent == "eryndex-research/2.0 (+https://ops.example/bot)"
    assert provider.max_pages == 4
    assert provider.timeout_s == 3.5
    assert provider.delay_s == 0.0


def test_from_env_refuses_a_page_budget_that_would_crawl_the_internet():
    with pytest.raises(ValueError) as excinfo:
        FirstPartyResearchProvider.from_env({"RESEARCH_MAX_PAGES": "800"})

    assert "RESEARCH_MAX_PAGES" in str(excinfo.value)


def test_from_env_refuses_a_value_that_is_not_a_number():
    with pytest.raises(ValueError) as excinfo:
        FirstPartyResearchProvider.from_env({"RESEARCH_TIMEOUT_S": "soon"})

    assert "RESEARCH_TIMEOUT_S" in str(excinfo.value)


# -----------------------------------------------------------------------------
# The settings must reach the fetcher under whichever name it uses
# -----------------------------------------------------------------------------


def test_the_delay_setting_reaches_a_fetcher_that_spells_it_differently():
    """``RESEARCH_DELAY_S`` must not be dropped because the sibling renamed the parameter."""
    from research.firstparty import _fetcher_kwargs

    class RealisticFetcher:
        def __init__(self, *, user_agent: str, timeout_s: float, per_host_delay_s: float = 1.0):
            self.user_agent = user_agent
            self.timeout_s = timeout_s
            self.per_host_delay_s = per_host_delay_s

    kwargs = _fetcher_kwargs(
        RealisticFetcher, user_agent="eryndex/1.0", timeout_s=3.0, delay_s=0.25
    )

    assert kwargs == {"user_agent": "eryndex/1.0", "timeout_s": 3.0, "per_host_delay_s": 0.25}


def test_the_settings_never_invent_a_keyword_the_constructor_does_not_take():
    from research.firstparty import _fetcher_kwargs

    class NarrowFetcher:
        def __init__(self, *, user_agent: str):
            self.user_agent = user_agent

    assert _fetcher_kwargs(NarrowFetcher, user_agent="ua", timeout_s=1.0, delay_s=1.0) == {
        "user_agent": "ua"
    }


# -----------------------------------------------------------------------------
# Missing siblings, and the CLI wiring
# -----------------------------------------------------------------------------


def test_a_missing_sibling_module_is_named_when_the_run_needs_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "research.crawl", None)
    fetcher = FakeFetcher(PAGES)
    provider = FirstPartyResearchProvider(fetcher=fetcher, derive_fn=lambda pages: [])

    with pytest.raises(ImportError) as excinfo:
        provider.produce(COMPANY)

    assert "research.crawl" in str(excinfo.value)
    assert fetcher.requested == []


def test_the_cli_offers_firstparty_and_builds_it_from_the_environment(monkeypatch):
    from scripts.research_run import build_parser, build_provider

    args = build_parser().parse_args(
        ["--company-id", "11111111-1111-1111-1111-111111111111", "--provider", "firstparty"]
    )
    monkeypatch.setenv("RESEARCH_MAX_PAGES", "3")

    provider = build_provider(args)

    assert provider.name == "firstparty"
    assert provider.max_pages == 3
    # The fixture stays the default so an operator cannot accidentally go to the network.
    assert build_parser().parse_args(["--company-id", "x"]).provider == "fixture"


def test_the_cli_reports_page_count_and_failures_in_the_run_summary():
    from scripts.research_run import provider_run_summary, record_source_stats

    provider, _ = build_provider(planned=[p.url for p in PAGES] + [BROKEN_URL])
    provider.produce(COMPANY)
    payload: dict = {}
    run = RunContext.begin(stage="research")

    record_source_stats(payload, run, provider)

    assert payload["research_source"]["pages_read"] == 3
    assert payload["research_source"]["failures"] == [f"{BROKEN_URL}: http_error (not found)"]
    summary = run.run_summary()
    assert summary["counts"]["pages_read"] == 3
    assert summary["counts"]["pages_failed"] == 1


def test_a_provider_without_run_statistics_adds_nothing_to_the_payload():
    from scripts.research_run import provider_run_summary

    assert provider_run_summary(object()) is None
