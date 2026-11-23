"""Crawl planning: which of a company's own pages are worth reading, and why.

The planner answers one question — *what does this site publish that tells us how the business
works?* — and it answers it from evidence rather than guesswork. It reads exactly two discovery
documents (``sitemap.xml`` and the homepage), then orders candidates by what a page of that kind
usually reveals::

    home -> about -> services -> work -> contact -> team -> pricing -> blog

Two decisions in here are worth stating, because both are easy to "simplify" back into a worse
planner:

1. **Priority is a group, not a URL list.** ``/about`` and ``/about-us`` answer the same
   question. Discovery evidence decides *which* variant exists, so the planner does not spend one
   of its handful of fetches on a variant the sitemap already disproves. The group still decides
   *when* the page is read, which is what priority actually means.
2. **A guess is the fallback, not the plan.** A group with no discovered page falls back to its
   canonical path (``/about``, ``/work`` …) because a 404 costs one fetch and :func:`crawl`
   records it instead of dying.

``crawl`` itself is deliberately tolerant: a page that raises :class:`ResearchFetchError` becomes
a recorded failure and the remaining plan still runs, because one broken URL must not cost the run
its seven good pages. Politeness is enforced here rather than trusted from the caller: at most
``max_pages`` fetches, ``fetcher.robots_allows(url)`` before each one, and never the same URL
twice.

The extraction layer (``research/extract.py``) is imported lazily inside :func:`crawl` and can be
overridden, so this module stays importable and testable while that layer is being written or
while a caller wants to reuse a fetch it already made.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from research.research_types import (
    CrawlPlan,
    FetchResult,
    PageContent,
    ResearchFetchError,
)

#: Reason vocabulary recorded on every planned page. ``keyword:<path>`` marks a canonical guess,
#: ``sitemap`` / ``link-from-home`` mark a page whose existence the site itself confirmed.
REASON_HOME = "home"
REASON_SITEMAP = "sitemap"
REASON_LINK = "link-from-home"

#: Paths that are never content: documents, media, fonts and bundles. Fetching one wastes a page
#: from the budget and cannot produce a quotable sentence.
SKIPPED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif", ".ico", ".bmp",
        ".zip", ".gz", ".tar", ".rar", ".dmg", ".exe", ".bin", ".mp4", ".mov", ".avi", ".webm",
        ".mp3", ".wav", ".m4a", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".css", ".js",
        ".mjs", ".json", ".xml", ".rss", ".atom", ".csv", ".xls", ".xlsx", ".doc", ".docx",
        ".ppt", ".pptx", ".txt", ".map", ".webmanifest",
    }
)

#: Paths behind a login, or that destroy a session. They carry no evidence and some of them
#: perform an action (logout) a research crawl has no business performing.
SKIPPED_PATH_WORDS: frozenset[str] = frozenset(
    {
        "login", "log-in", "signin", "sign-in", "signup", "sign-up", "logout", "log-out",
        "register", "auth", "oauth", "sso", "password", "reset-password", "account", "admin",
        "wp-login", "wp-admin", "cart", "checkout", "billing", "unsubscribe",
    }
)


@dataclass(frozen=True)
class PriorityGroup:
    """One question about the business, and the paths that usually answer it.

    ``keywords`` are matched against whole path segments, in order: a segment that equals the
    keyword, starts with ``keyword-`` or ends with ``-keyword`` counts. ``canonical`` is the path
    guessed when discovery found nothing, chosen as the most common real-world spelling rather
    than the first keyword (``/work``, not ``/portfolio``).
    """

    label: str
    keywords: tuple[str, ...]
    canonical: str
    #: How many URLs from this group may enter the plan. The default keeps a sitemap with forty
    #: case studies from eating the budget before the contact page is reached; the work group is
    #: raised because each case study is a distinct client, which is exactly the evidence the
    #: customer-flow dimension needs.
    max_variants: int = 2

    @property
    def reason(self) -> str:
        return f"keyword:/{self.canonical}"


#: Ordered by how much a page usually tells us about a prospective partner: who they are, what
#: they sell, whose work they show, how to reach them, who does the work, what it costs, what they
#: think. Pricing and blog last because they are frequently absent or stale.
PRIORITY_GROUPS: tuple[PriorityGroup, ...] = (
    PriorityGroup("about", ("about", "about-us", "who-we-are", "company"), "about"),
    PriorityGroup("services", ("services", "what-we-do", "service"), "services"),
    PriorityGroup(
        "work",
        ("portfolio", "work", "projects", "case-studies", "case-study", "our-work"),
        "work",
        max_variants=5,
    ),
    PriorityGroup("contact", ("contact",), "contact"),
    PriorityGroup("team", ("team", "people", "our-team"), "team"),
    PriorityGroup("pricing", ("pricing", "rates", "plans"), "pricing"),
    PriorityGroup("blog", ("blog", "insights", "news", "journal"), "blog"),
)

#: Two variants per group is plenty: a sitemap with forty ``/case-studies/*`` entries must not eat
#: the whole budget before the contact page is even reached.
MAX_PER_GROUP = 2

#: Hard caps on discovery reads, so a pathological sitemap cannot turn into a memory problem.
MAX_DISCOVERED_URLS = 200

_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_HREF = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CrawlFailure:
    """One page that was planned but not usable, with the reason an operator can act on."""

    url: str
    reason: str  # robots | http_error | timeout | too_large | non_html | network | blocked
    detail: str = ""


@dataclass(frozen=True)
class CrawlResult:
    """The pages that came back, the ones that did not, and the plan they came from."""

    pages: tuple[PageContent, ...]
    failures: tuple[CrawlFailure, ...]
    plan: CrawlPlan

    @property
    def urls(self) -> tuple[str, ...]:
        return tuple(page.url for page in self.pages)

    @property
    def failed_urls(self) -> tuple[str, ...]:
        return tuple(failure.url for failure in self.failures)

    @property
    def ok(self) -> bool:
        """True when at least one page was readable; a run of only failures is not research."""
        return bool(self.pages)


# -----------------------------------------------------------------------------
# URL identity
# -----------------------------------------------------------------------------


def _with_scheme(url: str) -> str:
    """Accept ``studio.example`` as well as a full URL: operators paste both."""
    candidate = str(url or "").strip()
    if not candidate:
        raise ValueError("start_url is required")
    if "//" not in candidate:
        candidate = "https://" + candidate.lstrip("/")
    return candidate


def _origin(url: str) -> str:
    parts = urlsplit(_with_scheme(url))
    if not parts.netloc:
        raise ValueError(f"start_url has no host: {url!r}")
    return f"{parts.scheme.lower() or 'https'}://{parts.netloc}"


def _host_key(url: str) -> str:
    """Host identity, tolerant of ``www.`` because sites link to themselves both ways."""
    netloc = urlsplit(url).netloc.lower().rstrip(".")
    netloc = netloc.removeprefix("www.")
    return netloc


def _same_host(url: str, base: str) -> bool:
    """Never leave the host: a partner's site is the only evidence we are allowed to cite."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return False
    return bool(_host_key(url)) and _host_key(url) == _host_key(base)


def _path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def _canonical_key(url: str) -> str:
    """Dedupe identity: ignores the fragment and the query.

    A tracking parameter must not make the crawler fetch a page it has already read, and
    ``/about``/``/about/`` are one page for politeness purposes.
    """
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


def _depth(url: str) -> int:
    return _path_of(url).count("/")


def _is_skipped(url: str) -> bool:
    """Binary/media extension, or an auth/account path."""
    path = _path_of(url).lower()
    name = path.rsplit("/", 1)[-1]
    if "." in name and f".{name.rsplit('.', 1)[-1]}" in SKIPPED_EXTENSIONS:
        return True
    return any(segment in SKIPPED_PATH_WORDS for segment in path.split("/"))


def _is_page_url(url: str, base: str) -> bool:
    return _same_host(url, base) and not _is_skipped(url)


# -----------------------------------------------------------------------------
# Discovery (best effort: a broken sitemap must not break the plan)
# -----------------------------------------------------------------------------


def _robots_ok(fetcher: object, url: str) -> bool:
    """Ask before reading. A refusal to answer counts as a refusal — fail closed."""
    try:
        return bool(fetcher.robots_allows(url))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - discovery is best effort, never fatal
        return False


def _try_get(fetcher: object, url: str) -> FetchResult | None:
    """Discovery read that returns None instead of raising: planning degrades, never fails."""
    try:
        return fetcher.get(url)  # type: ignore[attr-defined]
    except ResearchFetchError:
        return None
    except Exception:  # noqa: BLE001 - a fake or an unusual transport must not kill planning
        return None


def _dedupe(urls: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for url in urls:
        key = _canonical_key(url)
        if key in seen:
            continue
        seen.add(key)
        kept.append(url)
    return tuple(kept)


def _sitemap_entries(fetcher: object, origin: str) -> tuple[str, ...]:
    """Page URLs from ``/sitemap.xml``, filtered to same-host, non-media pages.

    Only the top-level sitemap is read on purpose: following a sitemap index would multiply
    fetches that the planner's budget does not count, and the priority groups rarely need them.
    """
    url = urljoin(origin + "/", "sitemap.xml")
    if not _robots_ok(fetcher, url):
        return ()
    result = _try_get(fetcher, url)
    if result is None:
        return ()
    candidates = [
        match.strip() for match in _SITEMAP_LOC.findall(result.text or "") if match.strip()
    ]
    page_urls = [
        candidate
        for candidate in candidates
        if candidate.lower().startswith(("http://", "https://"))
        and _is_page_url(candidate, origin + "/")
    ]
    return _dedupe(page_urls)[:MAX_DISCOVERED_URLS]


def _homepage_links(fetcher: object, home: str, origin: str) -> tuple[str, ...]:
    """Same-host links found in the homepage HTML.

    This is a bare href scan, not ``extract_page``: planning runs before extraction exists in the
    process, and discovery only needs to know *that* a path exists, not what it says. The
    authoritative link list still comes from ``extract_page`` during :func:`crawl`.
    """
    if not _robots_ok(fetcher, home):
        return ()
    result = _try_get(fetcher, home)
    if result is None:
        return ()
    base = result.url or home
    home_key = _canonical_key(home)
    resolved: list[str] = []
    for href in _HREF.findall(result.text or ""):
        candidate = urljoin(base, href.strip())
        if not _is_page_url(candidate, origin + "/"):
            continue
        if _canonical_key(candidate) == home_key:
            continue
        resolved.append(candidate)
    return _dedupe(resolved)[:MAX_DISCOVERED_URLS]


# -----------------------------------------------------------------------------
# Planning
# -----------------------------------------------------------------------------


def _segment_matches(segment: str, keyword: str) -> bool:
    """A path segment answers a keyword when it is that word, or a hyphenated variant of it."""
    return (
        segment == keyword
        or segment.startswith(keyword + "-")
        or segment.endswith("-" + keyword)
    )


def _keyword_rank(url: str, group: PriorityGroup) -> int | None:
    """Rank of the group keyword this URL's path carries, or None.

    Segment matching (``case-studies``) rather than substring matching, so ``/workshop`` is not
    mistaken for a work page and ``/about-us`` still matches ``about``.
    """
    segments = [segment for segment in _path_of(url).lower().split("/") if segment]
    best: int | None = None
    for index, keyword in enumerate(group.keywords):
        for segment in segments:
            if _segment_matches(segment, keyword) and (best is None or index < best):
                best = index
    return best


def _group_candidates(
    group: PriorityGroup,
    origin: str,
    sitemap_urls: Sequence[str],
    home_links: Sequence[str],
) -> list[tuple[str, str]]:
    """The pages that answer this group's question, best first.

    Confirmed pages (sitemap, then homepage links) win over a guess, shallow paths win over deep
    ones, and the canonical path is used only when the site offered nothing for the group.
    """
    found: list[tuple[int, int, str, str]] = []
    for url in sitemap_urls:
        rank = _keyword_rank(url, group)
        if rank is not None:
            found.append((rank, 0, url, REASON_SITEMAP))
    for url in home_links:
        rank = _keyword_rank(url, group)
        if rank is not None:
            found.append((rank, 1, url, REASON_LINK))

    if not found:
        return [(f"{origin}/{group.canonical}", group.reason)]

    found.sort(key=lambda item: (item[0], item[1], _depth(item[2]), len(item[2]), item[2]))
    return [(url, reason) for _, _, url, reason in found[: group.max_variants]]


_WORK_SEGMENTS = frozenset({"work", "portfolio", "projects", "case-studies", "case-study", "our-work"})


def _segments(url: str) -> tuple[str, ...]:
    return tuple(part for part in _path_of(url).split("/") if part)


def _is_portfolio_index(url: str) -> bool:
    """True for a page that lists work ("/work", "/portfolio") rather than one item of it."""
    segments = _segments(url)
    return len(segments) == 1 and segments[0].lower() in _WORK_SEGMENTS


def _is_case_study(url: str) -> bool:
    """True for a page one level below a work index: "/work/xero", "/case-studies/acme"."""
    segments = _segments(url)
    if len(segments) < 2:
        return False
    if segments[-1].lower() in _WORK_SEGMENTS:
        return False
    return any(segment.lower() in _WORK_SEGMENTS for segment in segments[:-1])


def plan_crawl(start_url: str, *, fetcher, max_pages: int = 8) -> CrawlPlan:
    """Plan up to ``max_pages`` pages, homepage first, each with the reason it is here.

    Discovery costs at most two reads (``sitemap.xml`` and the homepage) and those are not counted
    against ``max_pages``, which bounds what :func:`crawl` fetches. Both discovery reads tolerate
    failure: a site with no sitemap is the normal case, not an error.
    """
    budget = max(0, int(max_pages))
    origin = _origin(start_url)
    home = origin + "/"
    if budget == 0:
        return CrawlPlan(start_url=str(start_url), pages=(), reasons={})

    planned: list[tuple[str, str]] = [(home, REASON_HOME)]
    seen: set[str] = {_canonical_key(home)}

    sitemap_urls: tuple[str, ...] = ()
    home_links: tuple[str, ...] = ()
    if budget > 1:
        sitemap_urls = _sitemap_entries(fetcher, origin)
        home_links = _homepage_links(fetcher, home, origin)

    for group in PRIORITY_GROUPS:
        if len(planned) >= budget:
            break
        for url, reason in _group_candidates(group, origin, sitemap_urls, home_links):
            if len(planned) >= budget:
                break
            key = _canonical_key(url)
            if key in seen:
                continue
            seen.add(key)
            planned.append((url, reason))

    pages = tuple(url for url, _ in planned)
    reasons = {url: reason for url, reason in planned}
    return CrawlPlan(start_url=str(start_url), pages=pages, reasons=reasons)


# -----------------------------------------------------------------------------
# Crawling
# -----------------------------------------------------------------------------

Extractor = Callable[[FetchResult], PageContent]


def _default_extract(result: FetchResult) -> PageContent:
    """Import the extraction layer at call time.

    ``research/extract.py`` is a separate concern with its own tests; keeping the import local
    means ``crawl`` can be exercised with a fake extractor, and this module imports cleanly even
    while that layer is mid-rewrite.
    """
    from research.extract import extract_page

    return extract_page(result)


def crawl(
    plan: CrawlPlan,
    fetcher,
    *,
    extract: Extractor | None = None,
    max_pages: int | None = None,
) -> CrawlResult:
    """Read the planned pages, keeping failures as data instead of exceptions.

    ``max_pages`` defaults to the plan's length (the planner already bounded it); pass it when a
    hand-built plan needs the same guarantee. Every fetch is preceded by
    ``fetcher.robots_allows(url)`` and every URL is fetched at most once, including when a redirect
    lands on a page already read.
    """
    extractor = extract or _default_extract
    limit = len(plan.pages) if max_pages is None else max(0, int(max_pages))
    pages: list[PageContent] = []
    failures: list[CrawlFailure] = []
    seen: set[str] = set()
    fetched = 0

    # The plan is a starting point: a portfolio index page names the individual case studies, and
    # those are where clients and outcomes are actually stated. The queue grows only from pages we
    # have read, only within the same host, only up to the same fetch budget.
    queue: list[str] = list(plan.pages)
    index = 0
    discovered: set[str] = set()

    while index < len(queue):
        url = queue[index]
        index += 1
        if fetched >= limit:
            break
        key = _canonical_key(url)
        if key in seen:
            continue
        seen.add(key)

        if not _same_host(url, plan.start_url):
            # Defensive: the planner never emits an off-host URL, but a hand-made plan might.
            failures.append(CrawlFailure(url, "blocked", "off-host URL in the plan"))
            continue

        if not _robots_ok(fetcher, url):
            failures.append(CrawlFailure(url, "robots", "robots.txt disallows this URL"))
            continue

        fetched += 1
        try:
            result = fetcher.get(url)
        except ResearchFetchError as exc:
            failures.append(CrawlFailure(url, exc.reason, exc.detail))
            continue
        except Exception as exc:  # noqa: BLE001 - an unexpected transport error is still data
            failures.append(CrawlFailure(url, "network", str(exc)))
            continue

        final_key = _canonical_key(result.url or url)
        if final_key != key:
            if final_key in seen:
                continue  # a redirect onto a page already read: one fetch, not two pages
            seen.add(final_key)

        try:
            page = extractor(result)
        except Exception as exc:  # noqa: BLE001 - one unparseable page is not a failed run
            failures.append(CrawlFailure(url, "non_html", f"extraction failed: {exc}"))
            continue
        pages.append(page)

        if fetched < limit and _is_portfolio_index(url):
            for link in page.links:
                if fetched + (len(queue) - index) >= limit:
                    break
                if not link or not _same_host(link, plan.start_url):
                    continue
                if _is_skipped(link) or not _is_page_url(link, plan.start_url):
                    continue
                if not _is_case_study(link):
                    continue
                candidate = _canonical_key(link)
                if candidate in seen or candidate in discovered:
                    continue
                discovered.add(candidate)
                queue.append(link)

    return CrawlResult(pages=tuple(pages), failures=tuple(failures), plan=plan)


__all__ = [
    "MAX_PER_GROUP",
    "PRIORITY_GROUPS",
    "REASON_HOME",
    "REASON_LINK",
    "REASON_SITEMAP",
    "CrawlFailure",
    "CrawlResult",
    "Extractor",
    "PriorityGroup",
    "crawl",
    "plan_crawl",
]
