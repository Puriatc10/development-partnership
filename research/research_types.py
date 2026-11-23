"""Shared types for the first-party research engine.

The engine collects evidence from a company's own public pages, with no vendor and no API key, so
that "research" means "we read the site and cited what we read" rather than "a fixture said so".

Layering (each module owns one question, and imports only this file):

    http.py       can we fetch this URL at all?           -> FetchResult / FetchError
    extract.py    what is on this page, as text and links? -> PageContent
    crawl.py      which pages are worth reading?           -> CrawlPlan, crawl()
    findings.py   what does that evidence support?         -> DraftFinding + grounding check
    firstparty.py what does the provider hand the executor?-> ResearchPayload

Grounding rule: a finding is only admissible if its ``excerpt`` appears verbatim in the page it
cites (after whitespace normalisation). That is what stops the engine from writing plausible prose
and calling it research.

Precision rule: a fact that is *shaped* like evidence but is not (a postal code glued to a phone
number, a hero headline read as a client name, a testimonial read as a service offering) is worse
than no fact at all — it enters a briefing as true. Each extractor therefore prefers the page's
machine-readable statement (``tel:``/``mailto:``/JSON-LD) over a regex guess at prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

#: Why a page could not be used. Each maps to a sentence an operator can act on.
FetchReason = Literal[
    "robots",       # robots.txt disallows it
    "http_error",   # 4xx/5xx
    "timeout",      # no answer inside the budget
    "too_large",    # beyond the byte cap
    "non_html",     # PDF, image, JSON…
    "network",      # DNS/TLS/reset
    "blocked",      # our own politeness policy refused (rate, host budget)
]

Confidence = Literal["low", "medium", "high"]


class ResearchFetchError(RuntimeError):
    """A page could not be fetched, for a reason the caller can report and retry on."""

    def __init__(self, url: str, reason: FetchReason, detail: str = "") -> None:
        self.url = url
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {url}{' — ' + detail if detail else ''}")

    @property
    def retryable(self) -> bool:
        return self.reason in ("timeout", "network", "http_error", "blocked")


@dataclass(frozen=True)
class FetchResult:
    """One successfully fetched HTML page."""

    url: str          # final URL, after redirects
    status: int
    content_type: str
    text: str
    fetched_at: str    # ISO 8601, UTC


@dataclass(frozen=True)
class PageContent:
    """What a page says, stripped of chrome."""

    url: str
    title: str
    description: str
    text: str                    # main content, whitespace-normalised
    headings: tuple[str, ...] = ()
    links: tuple[str, ...] = ()   # absolute, same-host first
    jsonld: tuple[dict, ...] = ()
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()   # from `tel:` links: the only phone a page actually asserts
    fetched_at: str = ""

    def has_text(self, minimum: int = 200) -> bool:
        return len(self.text) >= minimum


@dataclass(frozen=True)
class CrawlPlan:
    """The pages worth reading, and why each one is on the list."""

    start_url: str
    pages: tuple[str, ...]
    reasons: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DraftFinding:
    """A candidate finding, before the executor validates and persists it."""

    statement: str
    excerpt: str
    source_url: str
    category: str           # IPP dimension vocabulary, see intelligence/ipp/models.py
    finding_type: str       # verified_fact | hypothesis | information_gap_tbd
    confidence: Confidence = "medium"
    kind: str = "observation"   # service | portfolio | team | contact | technology | client


def normalise(text: str) -> str:
    """Collapse whitespace so an excerpt can be compared with the page it came from."""
    return " ".join(str(text or "").split())


def excerpt_is_grounded(excerpt: str, page_text: str) -> bool:
    """True when ``excerpt`` appears verbatim (whitespace-insensitive) in ``page_text``."""
    needle, haystack = normalise(excerpt), normalise(page_text)
    if len(needle) < 12:
        return False
    return needle in haystack


def grounded(findings: Sequence[DraftFinding], pages: Sequence[PageContent]) -> list[DraftFinding]:
    """Drop any finding whose excerpt is not verbatim in the page it cites."""
    by_url = {page.url: page.text for page in pages}
    kept: list[DraftFinding] = []
    for finding in findings:
        text = by_url.get(finding.source_url)
        if text is not None and excerpt_is_grounded(finding.excerpt, text):
            kept.append(finding)
    return kept
