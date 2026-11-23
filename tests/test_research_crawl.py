"""Crawl planning and crawling, entirely offline.

The fetcher and the extractor are the two seams the rest of the engine hangs on, so both are
faked here: this file proves the planner's ordering and the crawler's politeness without a socket,
and it will keep passing while ``research/http.py`` and ``research/extract.py`` are rewritten.
"""

from __future__ import annotations

from research.crawl import CrawlFailure, CrawlResult, crawl, plan_crawl
from research.research_types import (
    CrawlPlan,
    FetchResult,
    PageContent,
    ResearchFetchError,
)

HOME = "https://studio.example/"
ABOUT = "https://studio.example/about"
SERVICES = "https://studio.example/services"
WORK = "https://studio.example/work"
CONTACT = "https://studio.example/contact"
TEAM = "https://studio.example/team"
PRICING = "https://studio.example/pricing"
BLOG = "https://studio.example/blog"


def result(url: str, text: str = "") -> FetchResult:
    return FetchResult(
        url=url, status=200, content_type="text/html", text=text, fetched_at="2026-01-01T00:00:00Z"
    )


class FakeFetcher:
    """Canned responses plus a call log, so politeness can be asserted rather than assumed.

    ``calls`` records both kinds of interaction in order: ``("robots", url)`` and ``("get", url)``.
    Every URL absent from ``pages`` raises ``http_error``, which is what a real 404 looks like to
    the crawler.
    """

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        *,
        robots: dict[str, bool] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.pages = dict(pages or {})
        self.robots = dict(robots or {})
        self.errors = dict(errors or {})
        self.calls: list[tuple[str, str]] = []

    def robots_allows(self, url: str) -> bool:
        self.calls.append(("robots", url))
        return self.robots.get(url, True)

    def get(self, url: str) -> FetchResult:
        self.calls.append(("get", url))
        if url in self.errors:
            raise self.errors[url]
        if url not in self.pages:
            raise ResearchFetchError(url, "http_error", "404")
        return result(url, self.pages[url])

    @property
    def fetched(self) -> list[str]:
        return [url for kind, url in self.calls if kind == "get"]


def fake_extract(fetch_result: FetchResult) -> PageContent:
    return PageContent(
        url=fetch_result.url,
        title="page",
        description="",
        text=fetch_result.text,
        fetched_at=fetch_result.fetched_at,
    )


def plan_of(*urls: str) -> CrawlPlan:
    return CrawlPlan(start_url=HOME, pages=urls, reasons={url: "test" for url in urls})


# -----------------------------------------------------------------------------
# Planning
# -----------------------------------------------------------------------------


def test_plan_starts_at_the_homepage_then_follows_the_priority_order():
    plan = plan_crawl(HOME, fetcher=FakeFetcher(), max_pages=8)

    assert plan.pages[0] == HOME
    assert plan.reasons[HOME] == "home"
    assert plan.pages[1:] == (ABOUT, SERVICES, WORK, CONTACT, TEAM, PRICING, BLOG)
    assert plan.reasons[ABOUT] == "keyword:/about"
    assert plan.reasons[PRICING] == "keyword:/pricing"


def test_plan_never_exceeds_max_pages():
    assert len(plan_crawl(HOME, fetcher=FakeFetcher(), max_pages=3).pages) == 3
    assert plan_crawl(HOME, fetcher=FakeFetcher(), max_pages=0).pages == ()


def test_plan_accepts_a_bare_host_and_a_tracking_query():
    for start in ("studio.example", "https://studio.example/?utm_source=x#top"):
        plan = plan_crawl(start, fetcher=FakeFetcher(), max_pages=2)
        assert plan.pages[0] == HOME, start


def test_plan_dedupes_a_page_reachable_two_ways():
    fetcher = FakeFetcher(
        {
            "https://studio.example/sitemap.xml": (
                "<urlset><url><loc>https://studio.example/about/</loc></url></urlset>"
            ),
            HOME: '<html><a href="/about">About</a></html>',
        }
    )

    plan = plan_crawl(HOME, fetcher=fetcher, max_pages=8)

    assert plan.pages.count(ABOUT) == 0
    assert [page for page in plan.pages if "about" in page] == ["https://studio.example/about/"]


def test_plan_never_leaves_the_host():
    fetcher = FakeFetcher(
        {
            "https://studio.example/sitemap.xml": (
                "<urlset>"
                "<url><loc>https://evil.example/about</loc></url>"
                "<url><loc>https://studio.example/about-us</loc></url>"
                "</urlset>"
            ),
            HOME: '<a href="https://evil.example/services">Services</a>',
        }
    )

    plan = plan_crawl(HOME, fetcher=fetcher, max_pages=8)

    assert all(page.startswith("https://studio.example/") for page in plan.pages)
    assert "https://studio.example/about-us" in plan.pages


def test_plan_skips_binary_and_media_paths():
    fetcher = FakeFetcher(
        {
            "https://studio.example/sitemap.xml": (
                "<urlset>"
                "<url><loc>https://studio.example/about.pdf</loc></url>"
                "<url><loc>https://studio.example/work/logo.png</loc></url>"
                "<url><loc>https://studio.example/case-studies/acme</loc></url>"
                "</urlset>"
            ),
            HOME: '<a href="/services-deck.pdf">deck</a><a href="/services">Services</a>',
        }
    )

    plan = plan_crawl(HOME, fetcher=fetcher, max_pages=8)

    assert not [page for page in plan.pages if page.endswith((".pdf", ".png"))]
    assert "https://studio.example/case-studies/acme" in plan.pages


def test_plan_skips_auth_and_logout_paths():
    fetcher = FakeFetcher(
        {HOME: '<a href="/login">l</a><a href="/logout">o</a><a href="/account/billing">b</a>'}
    )

    plan = plan_crawl(HOME, fetcher=fetcher, max_pages=8)

    assert plan.pages == (HOME, ABOUT, SERVICES, WORK, CONTACT, TEAM, PRICING, BLOG)


def test_plan_merges_matching_sitemap_entries_in_group_order():
    fetcher = FakeFetcher(
        {
            "https://studio.example/sitemap.xml": (
                "<urlset>"
                "<url><loc>https://studio.example/about-us</loc></url>"
                "<url><loc>https://studio.example/pricing</loc></url>"
                "<url><loc>https://studio.example/case-studies/acme-bank</loc></url>"
                "</urlset>"
            )
        }
    )

    plan = plan_crawl(HOME, fetcher=fetcher, max_pages=8)

    assert "https://studio.example/about-us" in plan.pages
    assert plan.reasons["https://studio.example/about-us"] == "sitemap"
    assert plan.pages.index("https://studio.example/about-us") < plan.pages.index(PRICING)
    # Discovery proved /about-us exists, so the canonical /about guess is not spent on a 404.
    assert ABOUT not in plan.pages
    assert "https://studio.example/case-studies/acme-bank" in plan.pages


def test_plan_uses_homepage_links_when_the_sitemap_is_missing():
    fetcher = FakeFetcher(
        {HOME: '<a href="/insights">Insights</a><a href="https://other.example/contact">c</a>'}
    )

    plan = plan_crawl(HOME, fetcher=fetcher, max_pages=8)

    assert "https://studio.example/insights" in plan.pages
    assert plan.reasons["https://studio.example/insights"] == "link-from-home"
    assert not [page for page in plan.pages if "other.example" in page]


def test_plan_tolerates_a_sitemap_that_raises():
    fetcher = FakeFetcher(
        errors={"https://studio.example/sitemap.xml": ResearchFetchError("x", "timeout", "slow")}
    )

    plan = plan_crawl(HOME, fetcher=fetcher, max_pages=4)

    assert plan.pages == (HOME, ABOUT, SERVICES, WORK)


def test_plan_is_deterministic():
    fetcher = FakeFetcher({HOME: '<a href="/about-us">a</a><a href="/insights">i</a>'})
    first = plan_crawl(HOME, fetcher=fetcher, max_pages=8)
    second = plan_crawl(HOME, fetcher=FakeFetcher({HOME: fetcher.pages[HOME]}), max_pages=8)

    assert first.pages == second.pages
    assert first.reasons == second.reasons


# -----------------------------------------------------------------------------
# Crawling
# -----------------------------------------------------------------------------


def test_crawl_returns_pages_and_keeps_the_plan():
    fetcher = FakeFetcher({HOME: "home text", ABOUT: "about text"})
    plan = plan_of(HOME, ABOUT)

    outcome = crawl(plan, fetcher, extract=fake_extract)

    assert isinstance(outcome, CrawlResult)
    assert outcome.urls == (HOME, ABOUT)
    assert outcome.plan is plan
    assert outcome.failures == ()
    assert outcome.ok is True


def test_crawl_records_a_failed_page_instead_of_raising():
    fetcher = FakeFetcher(
        {HOME: "home text", SERVICES: "services text"},
        errors={ABOUT: ResearchFetchError(ABOUT, "http_error", "500")},
    )

    outcome = crawl(plan_of(HOME, ABOUT, SERVICES), fetcher, extract=fake_extract)

    assert outcome.urls == (HOME, SERVICES)
    assert len(outcome.failures) == 1
    assert isinstance(outcome.failures[0], CrawlFailure)
    assert (outcome.failures[0].url, outcome.failures[0].reason) == (ABOUT, "http_error")


def test_crawl_asks_robots_before_every_fetch_and_honours_a_refusal():
    fetcher = FakeFetcher(
        {HOME: "home text", ABOUT: "about text", PRICING: "pricing text"},
        robots={PRICING: False},
    )

    outcome = crawl(plan_of(HOME, ABOUT, PRICING), fetcher, extract=fake_extract)

    assert outcome.urls == (HOME, ABOUT)
    assert outcome.failed_urls == (PRICING,)
    assert outcome.failures[0].reason == "robots"
    assert PRICING not in fetcher.fetched
    for url in (HOME, ABOUT):
        kinds = [kind for kind, called in fetcher.calls if called == url]
        assert kinds == ["robots", "get"], url


def test_crawl_never_fetches_a_url_twice():
    fetcher = FakeFetcher({HOME: "home text", ABOUT: "about text"})

    outcome = crawl(plan_of(HOME, ABOUT, ABOUT, "https://studio.example/about/"), fetcher,
                    extract=fake_extract)

    assert fetcher.fetched == [HOME, ABOUT]
    assert outcome.urls == (HOME, ABOUT)


def test_crawl_refuses_an_off_host_page_from_a_hand_made_plan():
    fetcher = FakeFetcher({HOME: "home text"})

    outcome = crawl(
        plan_of(HOME, "https://evil.example/about"), fetcher, extract=fake_extract
    )

    assert fetcher.fetched == [HOME]
    assert outcome.failed_urls == ("https://evil.example/about",)
    assert outcome.failures[0].reason == "blocked"


def test_crawl_caps_fetches_at_max_pages():
    fetcher = FakeFetcher({url: "text" for url in (HOME, ABOUT, SERVICES, WORK, CONTACT)})

    outcome = crawl(
        plan_of(HOME, ABOUT, SERVICES, WORK, CONTACT), fetcher, extract=fake_extract, max_pages=2
    )

    assert len(fetcher.fetched) == 2
    assert outcome.urls == (HOME, ABOUT)


def test_crawl_records_an_extractor_failure_as_a_page_failure():
    # A "page" the extractor cannot parse is data about the page, not a failed run.
    from research.research_types import FetchResult as _FetchResult

    def broken_extract(fetch_result: _FetchResult) -> PageContent:
        raise ValueError("not html after all")

    fetcher = FakeFetcher({HOME: "home text"})

    outcome = crawl(plan_of(HOME), fetcher, extract=broken_extract)

    assert outcome.pages == ()
    assert outcome.failures[0].reason == "non_html"
    assert outcome.ok is False


def test_crawl_skips_a_redirect_onto_a_page_already_read():
    fetcher = FakeFetcher({HOME: "home text"})
    fetcher.get = lambda url: result(ABOUT, "redirected")

    outcome = crawl(plan_of(HOME, ABOUT), fetcher, extract=fake_extract)

    assert len(outcome.pages) == 1
    assert outcome.urls == (ABOUT,)


def test_crawl_records_an_unexpected_transport_error_as_a_failure():
    # A transport that raises something other than ResearchFetchError is still one bad page,
    # not a dead run: the plan's remaining pages must still be fetched.
    fetcher = FakeFetcher({SERVICES: "services text"}, errors={ABOUT: OSError("connection reset")})

    outcome = crawl(plan_of(ABOUT, SERVICES), fetcher, extract=fake_extract)

    assert outcome.urls == (SERVICES,)
    assert outcome.failures[0].reason == "network"
