"""Offline tests for the research fetch and extract layers.

Nothing here touches the network. Every request goes through :class:`FakeSession`, every page
comes from ``tests/fixtures/research`` or an inline string, and the clock is faked so the
politeness assertions cost no wall-clock time.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

import research.http as http_module
from research.extract import MIN_SENTENCE_LENGTH, extract_page, sentences
from research.http import DEFAULT_USER_AGENT, Fetcher
from research.research_types import FetchResult, ResearchFetchError

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "research"

HOME = "https://northstar.example/"
ABOUT = "https://northstar.example/about"
WORK = "https://northstar.example/work"
ROBOTS = "https://northstar.example/robots.txt"
OTHER_HOME = "https://other.example/"
OTHER_ROBOTS = "https://other.example/robots.txt"


# --------------------------------------------------------------------------- fakes


class FakeClock:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


class FakeResponse:
    """The slice of a ``requests.Response`` the Fetcher is allowed to touch."""

    def __init__(
        self,
        url: str,
        *,
        status_code: int = 200,
        body: str | bytes = "",
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = (
            headers if headers is not None else {"Content-Type": "text/html; charset=utf-8"}
        )
        if chunks is not None:
            self._chunks = list(chunks)
        elif isinstance(body, bytes):
            self._chunks = [body] if body else []
        else:
            self._chunks = [body.encode("utf-8")] if body else []
        self.text = b"".join(self._chunks).decode("utf-8", "replace")
        self.reads = 0
        self.closed = False

    def iter_content(self, chunk_size: int = 65536) -> Iterator[bytes]:
        self.reads += 1
        return iter(list(self._chunks))

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Routes URLs to canned responses, exceptions or factories. Records every call."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        try:
            route = self.routes[url]
        except KeyError:
            raise ConnectionError(f"no fake route for {url}") from None
        if isinstance(route, BaseException):
            raise route
        if callable(route):
            return route(url, kwargs)
        return route

    def urls(self) -> list[str]:
        return [call["url"] for call in self.calls]


def html_page(url: str, body: str, **kwargs: Any) -> FakeResponse:
    return FakeResponse(url, body=body, **kwargs)


def robots(body: str = "", *, status: int = 200) -> FakeResponse:
    return FakeResponse(
        ROBOTS, status_code=status, body=body, headers={"Content-Type": "text/plain"}
    )


def open_robots() -> FakeResponse:
    return robots("User-agent: *\nDisallow:\n")


def make_fetcher(routes: dict[str, Any], **kwargs: Any) -> tuple[Fetcher, FakeSession]:
    session = FakeSession(routes)
    kwargs.setdefault("per_host_delay_s", 0.0)
    return Fetcher(session=session, **kwargs), session


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def page_result(text: str, url: str = WORK) -> FetchResult:
    return FetchResult(
        url=url,
        status=200,
        content_type="text/html; charset=utf-8",
        text=text,
        fetched_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Every test gets a fake clock, so no test can accidentally sleep."""
    fake = FakeClock()
    monkeypatch.setattr(http_module, "_monotonic", fake.monotonic)
    monkeypatch.setattr(http_module, "_sleep", fake.sleep)
    return fake


# --------------------------------------------------------------------------- robots


def test_robots_404_allows_without_warning() -> None:
    fetcher, session = make_fetcher(
        {ROBOTS: robots("not found", status=404), HOME: html_page(HOME, "<p>hello</p>")}
    )

    result = fetcher.get(HOME)

    assert result.status == 200
    assert fetcher.warnings == []
    assert session.urls() == [ROBOTS, HOME]


def test_robots_disallow_blocks_fetch_and_no_page_request() -> None:
    fetcher, session = make_fetcher(
        {
            ROBOTS: robots("User-agent: *\nDisallow: /private\n"),
            f"{HOME}private/report": html_page(f"{HOME}private/report", "<p>secret</p>"),
        }
    )

    with pytest.raises(ResearchFetchError) as caught:
        fetcher.get(f"{HOME}private/report")

    assert caught.value.reason == "robots"
    assert caught.value.url.endswith("/private/report")
    assert session.urls() == [ROBOTS]


def test_robots_allows_paths_outside_the_disallowed_prefix() -> None:
    fetcher, session = make_fetcher(
        {ROBOTS: robots("User-agent: *\nDisallow: /private\n"), ABOUT: html_page(ABOUT, "<p>x</p>")}
    )

    assert fetcher.robots_allows(ABOUT) is True
    assert fetcher.get(ABOUT).status == 200
    assert session.urls() == [ROBOTS, ABOUT]


def test_robots_longest_match_allow_beats_shorter_disallow() -> None:
    fetcher, _ = make_fetcher(
        {ROBOTS: robots("User-agent: *\nDisallow: /docs\nAllow: /docs/open\n")}
    )

    assert fetcher.robots_allows(f"{HOME}docs/open/handbook") is True
    assert fetcher.robots_allows(f"{HOME}docs/internal") is False


def test_robots_wildcard_and_end_anchor() -> None:
    fetcher, _ = make_fetcher({ROBOTS: robots("User-agent: *\nDisallow: /*/draft$\n")})

    assert fetcher.robots_allows(f"{HOME}notes/draft") is False
    assert fetcher.robots_allows(f"{HOME}notes/draft/one") is True


def test_robots_group_for_another_crawler_does_not_apply_to_us() -> None:
    body = "User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /admin\n"
    fetcher, _ = make_fetcher({ROBOTS: robots(body)})

    assert fetcher.robots_allows(HOME) is True
    assert fetcher.robots_allows(f"{HOME}admin/keys") is False


def test_robots_server_error_allows_and_records_warning() -> None:
    fetcher, _ = make_fetcher(
        {ROBOTS: robots("gateway down", status=503), HOME: html_page(HOME, "<p>ok</p>")}
    )

    assert fetcher.get(HOME).status == 200
    assert len(fetcher.warnings) == 1
    assert "robots" in fetcher.warnings[0]
    assert "503" in fetcher.warnings[0]


def test_robots_network_failure_allows_and_records_warning() -> None:
    fetcher, _ = make_fetcher(
        {ROBOTS: ConnectionError("reset by peer"), HOME: html_page(HOME, "<p>ok</p>")}
    )

    assert fetcher.get(HOME).status == 200
    assert "unreadable" in fetcher.warnings[0]


def test_robots_fetched_once_per_host() -> None:
    routes = {
        ROBOTS: open_robots(),
        HOME: html_page(HOME, "<p>home</p>"),
        ABOUT: html_page(ABOUT, "<p>about</p>"),
    }
    fetcher, session = make_fetcher(routes)

    fetcher.get(HOME)
    fetcher.get(ABOUT)

    assert session.urls().count(ROBOTS) == 1


# --------------------------------------------------------------------------- politeness


def test_per_host_delay_between_requests(clock: FakeClock) -> None:
    routes = {
        ROBOTS: open_robots(),
        HOME: html_page(HOME, "<p>home</p>"),
        ABOUT: html_page(ABOUT, "<p>about</p>"),
        WORK: html_page(WORK, "<p>work</p>"),
    }
    fetcher, _ = make_fetcher(routes, per_host_delay_s=1.0)

    fetcher.get(HOME)
    fetcher.get(ABOUT)
    fetcher.get(WORK)

    assert clock.sleeps == [1.0, 1.0]


def test_no_delay_across_hosts(clock: FakeClock) -> None:
    routes = {
        ROBOTS: open_robots(),
        OTHER_ROBOTS: open_robots(),
        HOME: html_page(HOME, "<p>home</p>"),
        OTHER_HOME: html_page(OTHER_HOME, "<p>other</p>"),
    }
    fetcher, _ = make_fetcher(routes, per_host_delay_s=5.0)

    fetcher.get(HOME)
    fetcher.get(OTHER_HOME)

    assert clock.sleeps == []


def test_crawl_delay_sets_a_higher_floor(clock: FakeClock) -> None:
    routes = {
        ROBOTS: robots("User-agent: *\nCrawl-delay: 4\nDisallow:\n"),
        HOME: html_page(HOME, "<p>home</p>"),
        ABOUT: html_page(ABOUT, "<p>about</p>"),
    }
    fetcher, _ = make_fetcher(routes, per_host_delay_s=0.5)

    fetcher.get(HOME)
    fetcher.get(ABOUT)

    assert clock.sleeps == [4.0]


# --------------------------------------------------------------------------- fetch outcomes


def test_redirect_records_the_final_url() -> None:
    fetched = "https://northstar.example/old-about"
    mover = FakeResponse(
        "https://northstar.example/about-us",
        body="<html><body><p>moved</p></body></html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    fetcher, session = make_fetcher({ROBOTS: open_robots(), fetched: mover})

    result = fetcher.get(fetched)

    assert result.url == "https://northstar.example/about-us"
    assert result.status == 200
    assert session.calls[-1]["allow_redirects"] is True


@pytest.mark.parametrize("status", [404, 500])
def test_http_error_reason(status: int) -> None:
    fetcher, _ = make_fetcher(
        {
            ROBOTS: open_robots(),
            HOME: FakeResponse(
                HOME, status_code=status, body="nope", headers={"Content-Type": "text/html"}
            ),
        }
    )

    with pytest.raises(ResearchFetchError) as caught:
        fetcher.get(HOME)

    assert caught.value.reason == "http_error"
    assert str(status) in caught.value.detail
    assert HOME in caught.value.detail, "the operator needs the URL the server actually answered"


def test_timeout_reason() -> None:
    fetcher, _ = make_fetcher({ROBOTS: open_robots(), HOME: TimeoutError("read timed out")})

    with pytest.raises(ResearchFetchError) as caught:
        fetcher.get(HOME)

    assert caught.value.reason == "timeout"
    assert caught.value.retryable is True


def test_network_reason() -> None:
    fetcher, _ = make_fetcher({ROBOTS: open_robots(), HOME: ConnectionError("connection reset")})

    with pytest.raises(ResearchFetchError) as caught:
        fetcher.get(HOME)

    assert caught.value.reason == "network"


def test_non_html_reason() -> None:
    fetcher, _ = make_fetcher(
        {
            ROBOTS: open_robots(),
            f"{HOME}deck.pdf": FakeResponse(
                f"{HOME}deck.pdf", body=b"%PDF-1.7", headers={"Content-Type": "application/pdf"}
            ),
        }
    )

    with pytest.raises(ResearchFetchError) as caught:
        fetcher.get(f"{HOME}deck.pdf")

    assert caught.value.reason == "non_html"
    assert "application/pdf" in caught.value.detail


def test_too_large_from_content_length_header() -> None:
    response = FakeResponse(
        HOME,
        body="<p>small body, huge promise</p>",
        headers={"Content-Type": "text/html", "Content-Length": "9000000"},
    )
    fetcher, _ = make_fetcher({ROBOTS: open_robots(), HOME: response}, max_bytes=1000)

    with pytest.raises(ResearchFetchError) as caught:
        fetcher.get(HOME)

    assert caught.value.reason == "too_large"
    assert response.reads == 0, "the body must not be streamed once the header says it is too big"


def test_too_large_from_streamed_body() -> None:
    response = FakeResponse(
        HOME,
        chunks=[b"a" * 600, b"b" * 600, b"c" * 600],
        headers={"Content-Type": "text/html"},
    )
    fetcher, _ = make_fetcher({ROBOTS: open_robots(), HOME: response}, max_bytes=1000)

    with pytest.raises(ResearchFetchError) as caught:
        fetcher.get(HOME)

    assert caught.value.reason == "too_large"
    assert response.closed is True


def test_request_carries_an_honest_user_agent() -> None:
    assert "Eryndex" in DEFAULT_USER_AGENT
    assert "https://" in DEFAULT_USER_AGENT, "an agent must say where to complain"
    fetcher, session = make_fetcher({ROBOTS: open_robots(), HOME: html_page(HOME, "<p>home</p>")})

    fetcher.get(HOME)

    page_call = session.calls[-1]
    assert page_call["headers"]["User-Agent"] == DEFAULT_USER_AGENT
    assert page_call["timeout"] == fetcher.timeout_s
    assert session.calls[0]["headers"]["User-Agent"] == DEFAULT_USER_AGENT


def test_unsupported_scheme_is_rejected_before_any_request() -> None:
    fetcher, session = make_fetcher({})

    for url in ("ftp://northstar.example/data", "/about", ""):
        with pytest.raises(ResearchFetchError) as caught:
            fetcher.get(url)
        assert caught.value.reason == "network"

    assert session.calls == []
    assert fetcher.robots_allows("mailto:hello@northstar.example") is False


# --------------------------------------------------------------------------- cache


def test_cache_hit_is_served_from_disk_without_another_request(tmp_path: pathlib.Path) -> None:
    routes = {ROBOTS: open_robots(), HOME: html_page(HOME, "<p>cached body</p>")}
    fetcher, session = make_fetcher(routes, cache_dir=tmp_path)

    first = fetcher.get(HOME)
    calls_after_first = len(session.calls)
    second = fetcher.get(HOME)

    assert second == first
    assert len(session.calls) == calls_after_first

    entry = tmp_path / f"{hashlib.sha256(HOME.encode('utf-8')).hexdigest()}.json"
    payload = json.loads(entry.read_text(encoding="utf-8"))
    assert payload["text"] == first.text
    assert payload["content_type"].startswith("text/html")
    assert payload["fetched_at"] == first.fetched_at


def test_corrupt_cache_entry_is_a_miss(tmp_path: pathlib.Path) -> None:
    entry = tmp_path / f"{hashlib.sha256(HOME.encode('utf-8')).hexdigest()}.json"
    entry.write_text("{not json", encoding="utf-8")
    fetcher, session = make_fetcher(
        {ROBOTS: open_robots(), HOME: html_page(HOME, "<p>fresh</p>")}, cache_dir=tmp_path
    )

    result = fetcher.get(HOME)

    assert "fresh" in result.text
    assert session.urls() == [ROBOTS, HOME]


# --------------------------------------------------------------------------- extraction


def test_title_description_and_text_from_homepage() -> None:
    page = extract_page(page_result(fixture_text("agency_home.html"), url=HOME))

    assert page.title == "Northstar Atlas — Product engineering for regulated teams"
    assert page.description.startswith("Northstar Atlas is a synthetic example studio")
    assert "every change is traceable" in page.text
    assert page.has_text()
    assert page.url == HOME
    assert page.fetched_at == "2026-01-01T00:00:00Z"


def test_headings_collected_in_document_order() -> None:
    page = extract_page(page_result(fixture_text("about_page.html"), url=ABOUT))

    assert page.headings == (
        "How we work",
        "Written decisions",
        "Team",
        "Delivery",
        "Hiring",
        "Where we are",
    )


def test_boilerplate_is_stripped_from_the_text() -> None:
    page = extract_page(page_result(fixture_text("agency_home.html"), url=HOME))

    for noise in (
        "Aside copy",
        "analytics bootstrap noise",
        "Template placeholder",
        "Scheepmakershaven",
        "Please enable JavaScript",
        "font-family: serif",
    ):
        assert noise not in page.text


def test_links_are_absolute_deduped_and_same_host_first() -> None:
    page = extract_page(page_result(fixture_text("portfolio_page.html"), url=WORK))

    assert all(link.startswith("https://") for link in page.links)
    assert page.links[:4] == (
        "https://northstar.example/",
        "https://northstar.example/contact",
        "https://northstar.example/work/atlas-ledger",
        "https://northstar.example/work/beacon-cms",
    )
    assert page.links[-1] == "https://partners.example/clinic-scheduler"
    assert page.links.count("https://northstar.example/work/atlas-ledger") == 1
    assert not any("#" in link for link in page.links)
    assert not any(link.startswith("mailto:") for link in page.links)


def test_nav_links_survive_even_though_nav_text_is_dropped() -> None:
    page = extract_page(page_result(fixture_text("agency_home.html"), url=HOME))

    assert "https://northstar.example/work" in page.links
    assert "Work" not in page.text


def test_jsonld_objects_parsed_and_invalid_blocks_ignored() -> None:
    page = extract_page(page_result(fixture_text("agency_home.html"), url=HOME))

    assert len(page.jsonld) == 1, "the trailing-comma block must be dropped, not guessed at"
    assert page.jsonld[0]["@type"] == "Organization"
    assert page.jsonld[0]["name"] == "Northstar Atlas"


def test_emails_from_text_and_mailto_links() -> None:
    about = extract_page(page_result(fixture_text("about_page.html"), url=ABOUT))
    home = extract_page(page_result(fixture_text("agency_home.html"), url=HOME))

    assert set(about.emails) == {"hello@northstar.example", "careers@northstar.example"}
    # This address only exists inside a footer link, which is exactly where contact data lives.
    assert home.emails == ("hello@northstar.example",)


def test_article_preferred_and_messy_html_still_reads() -> None:
    page = extract_page(page_result(fixture_text("about_page.html"), url=ABOUT))

    assert "coordination cost is the thing that quietly kills delivery quality" in page.text
    assert "press release & we say so" in page.text, "&amp; must decode"
    assert "engagement — no anonymous delivery pods" in page.text, "&mdash; must decode"
    assert "café downstairs" in page.text, "&#233; must decode"
    assert "Team leads are named on every engagement" in page.text, "unclosed <p> must not stop us"
    assert "Northstar Atlas — synthetic content" not in page.text, "footer text is chrome"


def test_body_is_used_when_there_is_no_main_or_article() -> None:
    page = extract_page(page_result(fixture_text("portfolio_page.html"), url=WORK))

    assert "Append-only audit trail" in page.text
    assert "offline-first form with a deterministic server reconciliation step" in page.text
    assert "Say hello" not in page.text


def test_body_is_used_when_main_is_only_a_loading_shell() -> None:
    markup = (
        "<html><body>"
        '<main><div id="root">Loading…</div></main>'
        "<p>The server-rendered fallback paragraph is longer than the shell, so the page is "
        "still readable even when the client-side bundle never executes.</p>"
        "</body></html>"
    )

    page = extract_page(page_result(markup))

    # Body text is a superset of the shell, so the fallback keeps the shell's short label but
    # must still surface the paragraph a reader came for.
    assert "The server-rendered fallback paragraph" in page.text
    assert len(page.text) > len("Loading…")


def test_long_attribute_values_do_not_break_extraction() -> None:
    blob = "x" * 40_000
    markup = (
        "<html><head><title>Long attributes</title></head><body>"
        f'<p>Real text survives.</p><div data-blob="{blob}">Still readable.</div>'
        f'<a href="https://northstar.example/case/{blob}">case</a>'
        "</body></html>"
    )

    page = extract_page(page_result(markup))

    assert "Real text survives." in page.text
    assert "Still readable." in page.text
    assert len(page.links) == 1 and len(page.links[0]) > 40_000


def test_entities_decode_to_characters() -> None:
    markup = (
        "<html><body><p>Write &lt;script&gt; only when you mean it, "
        "and never at 3&nbsp;a.m. &copy; 2026.</p></body></html>"
    )

    page = extract_page(page_result(markup))

    assert "Write <script> only when you mean it" in page.text
    assert "© 2026" in page.text


def test_declared_cp1252_charset_is_decoded() -> None:
    body = (FIXTURES / "legacy_cp1252.html").read_bytes()
    response = FakeResponse(
        "https://legacy.example/", chunks=[body], headers={"Content-Type": "text/html"}
    )
    fetcher, _ = make_fetcher({OTHER_ROBOTS: open_robots(), "https://legacy.example/": response})

    result = fetcher.get("https://legacy.example/")
    page = extract_page(result)

    assert "Café Société — twenty years" in result.text
    assert page.title == "Café Société — twenty years of quiet work"
    assert "“Small is a feature,”" in page.text
    assert page.emails == ("atelier@cafe-societe.example",)


def test_empty_document_yields_an_empty_page() -> None:
    page = extract_page(page_result(""))

    assert page.title == ""
    assert page.description == ""
    assert page.text == ""
    assert page.headings == ()
    assert page.links == ()
    assert page.jsonld == ()
    assert page.emails == ()
    assert page.has_text() is False


# --------------------------------------------------------------------------- sentences


def test_sentences_keep_only_long_pieces_in_order() -> None:
    text = (
        "We build platforms that survive an audit. Too short. This sentence is comfortably "
        "longer than the thirty character floor. Another one?"
    )

    kept = sentences(text)

    assert kept == [
        "We build platforms that survive an audit.",
        "This sentence is comfortably longer than the thirty character floor.",
    ]
    assert all(len(piece) >= MIN_SENTENCE_LENGTH for piece in kept)
    assert sentences(text) == kept, "splitting must be deterministic"


def test_sentences_normalise_whitespace_and_reject_empty_text() -> None:
    text = "First claim\n\t runs across\nlines and is long enough to keep.   Short.  "

    kept = sentences(text)

    assert kept == ["First claim runs across lines and is long enough to keep."]
    assert sentences("") == []
    assert sentences("   \n  ") == []
