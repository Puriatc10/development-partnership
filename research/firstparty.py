"""First-party research: read the company's own public pages and cite what was read.

Why this exists
---------------
The fixture provider proves the write path works; an integration provider proves a vendor
answered. Neither proves that anybody read the company's site. This provider is the one that
does the reading itself, with no vendor, no API key and no model in the loop:

    start URL -> plan_crawl -> crawl -> derive_findings -> ResearchPayload

Two rules are load-bearing, because a payload is what a qualification is later allowed to
rest on:

1. **Nothing is cited that was not fetched.** A finding is kept only when its excerpt appears
   verbatim in a page this run pulled (:func:`research.research_types.grounded`) *and* that
   page is present in the crawl result. The second check is redundant while the crawler is the
   only source of pages; it exists so a future planner cannot cite a URL that came from a
   search-result snippet instead of a fetch.
2. **A run that read nothing is a failure, not an empty success.** Zero pages raises
   :class:`~research.executor.ResearchProviderError` naming every reason. A partial run says so
   in the summary and in ``gaps_and_unknowns``, and can never be graded ``high``.

Sibling modules are imported lazily
-----------------------------------
``research/http.py``, ``research/extract.py``, ``research/crawl.py`` and ``research/findings.py``
are resolved when a run first needs them, not at import time. Two reasons: this module stays
importable (and testable) while those files are still being written, and an operator who does
not use this provider never pays for loading them. The resulting :class:`ImportError` names the
missing module and what it was for.

Determinism
-----------
Same pages in, byte-identical payload out: pages, sources and findings are sorted, duplicate
statements are collapsed, and nothing here reads the clock or the network directly (the fetcher
does, and it is injected). That is what makes a rerun comparable to the first run.
"""

from __future__ import annotations

import importlib
import inspect
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from intelligence.ipp.models import CONFIDENCE_LEVELS, FINDING_CATEGORIES, FINDING_TYPES
from research.executor import (
    VERIFIED_FACT_CONFIDENCES,
    Finding,
    ResearchPayload,
    ResearchProviderError,
    Source,
)
from research.research_types import (
    DraftFinding,
    PageContent,
    ResearchFetchError,
    grounded,
    normalise,
)

#: Names the tool and a place to read what it does. ``.example`` is reserved for documentation,
#: so the default can never generate real traffic at somebody's site: an operator running this
#: against a live company sets ``RESEARCH_USER_AGENT`` to a URL that actually answers.
DEFAULT_USER_AGENT = "eryndex-firstparty-research/1.0 (+https://eryndex.example/research-policy)"

DEFAULT_MAX_PAGES = 8
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_DELAY_S = 1.0

#: A typo in ``RESEARCH_MAX_PAGES`` must not turn one research task into a crawl of the whole
#: internet, so the environment value is bounded rather than trusted.
MAX_PAGES_CEILING = 50

ENV_USER_AGENT = "RESEARCH_USER_AGENT"
ENV_MAX_PAGES = "RESEARCH_MAX_PAGES"
ENV_TIMEOUT_S = "RESEARCH_TIMEOUT_S"
ENV_DELAY_S = "RESEARCH_DELAY_S"

#: One source excerpt, and the summary, are stored with the report; both are capped so a single
#: page with a huge text body cannot push the rest of the evidence out of the row.
SOURCE_EXCERPT_CHARS = 280
SUMMARY_CHARS = 4000


# -----------------------------------------------------------------------------
# What one produce() call actually did
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderRun:
    """Accounting for one :meth:`FirstPartyResearchProvider.produce` call.

    The payload says what was concluded; this says how much evidence it rests on, so the CLI
    can print "read 5 of 8 pages, 3 failed" next to the report instead of implying the site was
    read end to end.
    """

    start_url: str = ""
    pages_read: int = 0
    pages_failed: int = 0
    failures: tuple[str, ...] = ()
    findings: int = 0
    verified_findings: int = 0
    overall_confidence: str = "low"

    @property
    def partial(self) -> bool:
        """True when at least one planned page was not read."""
        return self.pages_failed > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_url": self.start_url,
            "pages_read": self.pages_read,
            "pages_failed": self.pages_failed,
            "partial": self.partial,
            "failures": list(self.failures),
            "findings": self.findings,
            "verified_findings": self.verified_findings,
            "overall_confidence": self.overall_confidence,
        }


# -----------------------------------------------------------------------------
# Lazy sibling imports
# -----------------------------------------------------------------------------


def _import_sibling(name: str, *, purpose: str) -> Any:
    """Import ``research.<name>`` on first use, with an error that says what is missing.

    Only a *missing research module* is rewritten. If the sibling exists and its own dependency
    is absent (``requests``, say), the original error is re-raised untouched: that is a
    packaging problem, not a half-built checkout, and hiding it would confuse both.
    """
    module_name = f"research.{name}"
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        missing = str(getattr(exc, "name", "") or "")
        if missing and not missing.startswith("research."):
            raise
        raise ImportError(
            f"the firstparty provider needs {module_name} ({purpose}), which is not importable "
            f"in this checkout: {exc}"
        ) from exc


# -----------------------------------------------------------------------------
# Small coercions
# -----------------------------------------------------------------------------


def start_url_for(company: Mapping[str, Any]) -> str:
    """The page a crawl starts from: the website we have, else ``https://`` + the domain.

    ``website_url`` wins because it is what the company publishes; ``primary_domain`` is the
    CRM's derived fallback and carries no scheme, so one is added rather than guessed. The
    fragment is dropped: ``#pricing`` cannot be fetched and would only create a second,
    identical page in the report.
    """
    website = str(company.get("website_url") or "").strip()
    domain = str(company.get("primary_domain") or "").strip()
    candidate = website or (f"https://{domain}" if domain else "")
    if not candidate:
        raise ResearchProviderError(
            "firstparty research needs company.website_url or company.primary_domain; both are "
            "empty, so there is no site to read"
        )
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ResearchProviderError(f"{candidate!r} is not a readable http(s) URL")
    return candidate.split("#", 1)[0]


def failure_text(failure: Any) -> str:
    """One readable line per unusable page, from whatever the crawler reports.

    The crawler owns the failure type. Rather than pinning a shape this module does not define,
    it accepts a ``ResearchFetchError`` (``url``/``reason``/``detail``), a mapping, a
    ``(url, reason)`` pair or a plain string — the message is what the operator reads, and it
    must survive the sibling's refactor.
    """
    if isinstance(failure, Mapping):
        url = str(failure.get("url") or "").strip()
        reason = str(failure.get("reason") or failure.get("detail") or "").strip()
        detail = str(failure.get("detail") or "").strip()
    elif isinstance(failure, (tuple, list)) and len(failure) >= 2 and not hasattr(failure, "url"):
        url, reason = str(failure[0] or "").strip(), str(failure[1] or "").strip()
        detail = ""
    else:
        url = str(getattr(failure, "url", "") or "").strip()
        reason = str(getattr(failure, "reason", "") or "").strip()
        detail = str(getattr(failure, "detail", "") or "").strip()
    if url and reason:
        return f"{url}: {reason}" + (f" ({detail})" if detail and detail != reason else "")
    return normalise(str(failure)) or "a page could not be read"


def _as_page(item: Any) -> PageContent | None:
    """Accept a :class:`PageContent`, a pair carrying one, or any object with url + text.

    The crawler returns pages this module does not construct, so the only field taken on trust
    is the pair ``(url, text)``; everything else is defaulted. A page without both is skipped
    rather than half-cited.
    """
    if isinstance(item, PageContent):
        return item
    if isinstance(item, Mapping):
        url, text = str(item.get("url") or ""), item.get("text")
        if url and isinstance(text, str):
            return PageContent(
                url=url,
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
                text=text,
            )
        return None
    if isinstance(item, (tuple, list)):
        for member in item:
            page = _as_page(member)
            if page is not None:
                return page
        return None
    url = getattr(item, "url", None)
    text = getattr(item, "text", None)
    if isinstance(url, str) and isinstance(text, str):
        return PageContent(
            url=url,
            title=str(getattr(item, "title", "") or ""),
            description=str(getattr(item, "description", "") or ""),
            text=text,
            headings=tuple(getattr(item, "headings", ()) or ()),
            links=tuple(getattr(item, "links", ()) or ()),
            emails=tuple(getattr(item, "emails", ()) or ()),
            fetched_at=str(getattr(item, "fetched_at", "") or ""),
        )
    return None


def _pages_of(crawl_result: Any) -> list[PageContent]:
    """Every distinct page the crawl read, sorted by URL.

    Sorted and de-duplicated (first URL wins) because two of the same URL would otherwise make
    the payload depend on crawl order, and a deterministic rerun is the point.
    """
    raw: Iterable[Any] = getattr(crawl_result, "pages", ()) or ()
    by_url: dict[str, PageContent] = {}
    for item in raw:
        page = _as_page(item)
        if page is not None and page.url not in by_url:
            by_url[page.url] = page
    return [by_url[url] for url in sorted(by_url)]


def _failures_of(crawl_result: Any) -> list[str]:
    raw: Iterable[Any] = getattr(crawl_result, "failures", ()) or ()
    return sorted({text for text in (failure_text(f) for f in raw) if text})


def _to_finding(draft: DraftFinding) -> Finding:
    """One draft finding in the exact shape ``research_findings`` stores."""
    excerpt = normalise(draft.excerpt)
    return Finding(
        category=str(draft.category or "").strip(),
        finding_type=str(draft.finding_type or "").strip(),
        statement=normalise(draft.statement),
        confidence=str(draft.confidence or "").strip(),
        evidence_url=(str(draft.source_url or "").strip() or None),
        evidence_excerpt=excerpt or None,
    )


def _inadmissible(finding: Finding) -> str | None:
    """Why the executor would refuse this finding, or ``None`` when it is storable.

    The provider has already dropped ungrounded drafts; this is the enum half of the same
    promise. Catching it here means a bad draft costs one finding and a line in the gaps
    instead of a :class:`~research.executor.ResearchValidationError` that loses the whole run.
    """
    if finding.category not in FINDING_CATEGORIES:
        return f"category {finding.category!r} is not a finding_category_type"
    if finding.finding_type not in FINDING_TYPES:
        return f"finding_type {finding.finding_type!r} is not a research_finding_type"
    if finding.confidence not in CONFIDENCE_LEVELS:
        return f"confidence {finding.confidence!r} is not a confidence_level_type"
    if not finding.statement:
        return "the statement is empty"
    if finding.finding_type == "verified_fact":
        if not finding.evidence_url:
            return "a verified_fact needs an http(s) evidence_url"
        if finding.confidence not in VERIFIED_FACT_CONFIDENCES:
            return f"a verified_fact cannot be {finding.confidence!r}"
    return None


def _excerpt_of(page: PageContent) -> str:
    text = normalise(page.text)
    if len(text) <= SOURCE_EXCERPT_CHARS:
        return text
    cut = text[:SOURCE_EXCERPT_CHARS]
    # Prefer a word boundary: an excerpt ending mid-word reads like a truncation bug.
    space = cut.rfind(" ")
    return cut[:space] if space > SOURCE_EXCERPT_CHARS // 2 else cut


def _duration_of(value: str, variable: str, default: float, *, minimum: float) -> float:
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"{variable}={raw!r} is not a number of seconds") from exc
    if number < minimum:
        raise ValueError(f"{variable}={raw!r} must be >= {minimum}")
    return number


def _count_of(value: str, variable: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ValueError(f"{variable}={raw!r} is not a whole number") from exc
    if number < minimum:
        raise ValueError(f"{variable}={raw!r} must be >= {minimum}")
    if number > maximum:
        raise ValueError(
            f"{variable}={raw!r} exceeds the ceiling of {maximum}; a research task must not turn "
            "into a site crawl"
        )
    return number


# -----------------------------------------------------------------------------
# The provider
# -----------------------------------------------------------------------------


class FirstPartyResearchProvider:
    """Reads the company's own site and reports only what those pages support.

    Everything the run depends on is injectable (``fetcher``, ``plan_fn``, ``crawl_fn``,
    ``derive_fn``) for one reason: the tests must be able to exercise the honesty rules with no
    network and no sibling module present. With no injection, the siblings are imported on first
    use.
    """

    name = "firstparty"
    synthetic = False

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        max_pages: int = DEFAULT_MAX_PAGES,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        delay_s: float = DEFAULT_DELAY_S,
        fetcher: Any | None = None,
        plan_fn: Callable[..., Any] | None = None,
        crawl_fn: Callable[..., Any] | None = None,
        derive_fn: Callable[..., Any] | None = None,
    ) -> None:
        if not 1 <= int(max_pages) <= MAX_PAGES_CEILING:
            raise ValueError(
                f"max_pages must be between 1 and {MAX_PAGES_CEILING}, got {max_pages!r}"
            )
        if float(timeout_s) <= 0:
            raise ValueError(f"timeout_s must be > 0, got {timeout_s!r}")
        if float(delay_s) < 0:
            raise ValueError(f"delay_s must be >= 0, got {delay_s!r}")
        self.user_agent = str(user_agent or DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT
        self.max_pages = int(max_pages)
        self.timeout_s = float(timeout_s)
        self.delay_s = float(delay_s)
        self._fetcher = fetcher
        self._plan_fn = plan_fn
        self._crawl_fn = crawl_fn
        self._derive_fn = derive_fn
        #: Accounting for the most recent run; ``None`` until ``produce`` is called.
        self.last_run: ProviderRun | None = None

    # -- construction -------------------------------------------------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> FirstPartyResearchProvider:
        """Build from ``RESEARCH_*`` variables, defaulting to a polite, bounded crawler.

        ``env`` is an argument so a test (or a supervisor passing a filtered environment) does
        not have to mutate ``os.environ``. Bad values raise ``ValueError`` naming the variable:
        a research run that silently used 800 pages or a 0-second timeout is worse than one
        that refuses to start.
        """
        values: Mapping[str, str] = os.environ if env is None else env
        user_agent = str(values.get(ENV_USER_AGENT) or "").strip() or DEFAULT_USER_AGENT
        return cls(
            user_agent=user_agent,
            max_pages=_count_of(
                str(values.get(ENV_MAX_PAGES) or ""),
                ENV_MAX_PAGES,
                DEFAULT_MAX_PAGES,
                minimum=1,
                maximum=MAX_PAGES_CEILING,
            ),
            timeout_s=_duration_of(
                str(values.get(ENV_TIMEOUT_S) or ""), ENV_TIMEOUT_S, DEFAULT_TIMEOUT_S, minimum=0.1
            ),
            delay_s=_duration_of(
                str(values.get(ENV_DELAY_S) or ""), ENV_DELAY_S, DEFAULT_DELAY_S, minimum=0.0
            ),
        )

    def _fetcher_or_default(self) -> Any:
        if self._fetcher is not None:
            return self._fetcher
        http_module = _import_sibling("http", purpose="fetching pages with robots and politeness")
        fetcher_cls = http_module.Fetcher
        self._fetcher = fetcher_cls(
            **_fetcher_kwargs(
                fetcher_cls,
                user_agent=self.user_agent,
                timeout_s=self.timeout_s,
                delay_s=self.delay_s,
            )
        )
        return self._fetcher

    def _crawl_functions(self) -> tuple[Callable[..., Any], Callable[..., Any]]:
        plan_fn, crawl_fn = self._plan_fn, self._crawl_fn
        if plan_fn is None or crawl_fn is None:
            crawl_module = _import_sibling("crawl", purpose="planning and walking the site")
            plan_fn = plan_fn or crawl_module.plan_crawl
            crawl_fn = crawl_fn or crawl_module.crawl
        return plan_fn, crawl_fn

    def _derive_function(self) -> Callable[..., Any]:
        if self._derive_fn is not None:
            return self._derive_fn
        findings_module = _import_sibling("findings", purpose="turning pages into findings")
        return findings_module.derive_findings

    # -- the run ------------------------------------------------------------

    def produce(self, company: Mapping[str, Any]) -> ResearchPayload:
        """Read the site, keep only what the pages support, and account for what was missed."""
        start_url = start_url_for(company)
        fetcher = self._fetcher_or_default()
        pages, failures, planned = self._read_site(start_url, fetcher)
        if not pages:
            self.last_run = ProviderRun(
                start_url=start_url, pages_failed=len(failures), failures=tuple(failures)
            )
            reasons = "; ".join(failures) if failures else "no page was planned or readable"
            raise ResearchProviderError(
                f"firstparty research read no pages from {start_url}: {reasons}"
            )

        drafts = list(self._derive_function()(list(pages)) or ())
        fetched = {page.url for page in pages}
        supported = grounded(drafts, pages)
        supported_ids = {id(draft) for draft in supported}
        # A deriver that invents an excerpt is a defect worth reporting, not just dropping: the
        # operator sees how much of the draft material did not survive grounding.
        ungrounded = sorted(
            f"{normalise(draft.statement)[:80]!r} (the excerpt is not on the page it cites)"
            for draft in drafts
            if id(draft) not in supported_ids
        )
        # Defence in depth: grounded() already requires the citing URL to be one of `pages`, but
        # the check is restated so this module never relies on a sibling's interpretation of it.
        kept = [draft for draft in supported if str(draft.source_url or "").strip() in fetched]

        findings, refused = self._admissible(kept)
        dropped = ungrounded + refused
        if not findings:
            self.last_run = ProviderRun(
                start_url=start_url,
                pages_read=len(pages),
                pages_failed=len(failures),
                failures=tuple(failures),
            )
            because = "; ".join(dropped) if dropped else "no draft finding was grounded in them"
            raise ResearchProviderError(
                f"firstparty research read {len(pages)} page(s) from {start_url} but landed no "
                f"admissible findings: {because}"
            )

        verified = sum(1 for finding in findings if finding.finding_type == "verified_fact")
        confidence = _confidence_for(pages=len(pages), verified=verified, partial=bool(failures))
        sources = tuple(
            Source(url=page.url, title=page.title.strip(), excerpt=_excerpt_of(page))
            for page in pages
        )
        gaps = _gaps_text(
            findings=findings,
            failures=failures,
            dropped=dropped,
            warnings=_fetcher_warnings(fetcher),
            pages_read=len(pages),
            planned=planned,
        )
        payload = ResearchPayload(
            summary=_summary_for(company, start_url, pages, failures),
            recommended_angle=None,
            overall_confidence=confidence,
            sources=sources,
            findings=tuple(findings),
            gaps_and_unknowns=gaps,
        )
        self.last_run = ProviderRun(
            start_url=start_url,
            pages_read=len(pages),
            pages_failed=len(failures),
            failures=tuple(failures),
            findings=len(findings),
            verified_findings=verified,
            overall_confidence=confidence,
        )
        return payload

    # -- internals ----------------------------------------------------------

    def _read_site(
        self, start_url: str, fetcher: Any
    ) -> tuple[list[PageContent], list[str], int]:
        """Plan and walk the site; turn any whole-crawl failure into a reported reason.

        The planned page count comes back too, so a partial run can say "3 of 8" rather than an
        unanchored "3 pages failed".
        """
        plan_fn, crawl_fn = self._crawl_functions()
        try:
            plan = plan_fn(start_url, fetcher=fetcher, max_pages=self.max_pages)
        except ResearchFetchError as exc:
            return [], [failure_text(exc)], 0
        planned = len(tuple(getattr(plan, "pages", ()) or ()))
        # Pass the budget explicitly when the crawler accepts it: `crawl` otherwise caps itself at
        # the planned page count, which leaves no room for the case studies a portfolio index page
        # points at. A crawler without the parameter (a fake in a test, an older implementation)
        # still works, because the plan already bounds it.
        crawl_kwargs = (
            {"max_pages": self.max_pages}
            if "max_pages" in inspect.signature(crawl_fn).parameters
            else {}
        )
        try:
            crawl_result = crawl_fn(plan, fetcher, **crawl_kwargs)
        except ResearchFetchError as exc:
            return [], [failure_text(exc)], planned
        return _pages_of(crawl_result), _failures_of(crawl_result), planned

    def _admissible(self, drafts: Sequence[DraftFinding]) -> tuple[list[Finding], list[str]]:
        """Map drafts 1:1 to stored findings, sorted, without duplicates or refused rows."""
        findings: list[Finding] = []
        dropped: list[str] = []
        mapped = sorted(
            (_to_finding(draft) for draft in drafts),
            key=lambda finding: (
                finding.category,
                finding.finding_type,
                finding.statement,
                finding.evidence_url or "",
            ),
        )
        seen: set[str] = set()
        for finding in mapped:
            reason = _inadmissible(finding)
            if reason is not None:
                dropped.append(f"{finding.statement[:80]!r} ({reason})")
                continue
            if finding.statement in seen:
                continue
            seen.add(finding.statement)
            findings.append(finding)
        return findings, dropped


#: The politeness delay is spelled ``per_host_delay_s`` by ``research.http.Fetcher``; the
#: environment variable here is ``RESEARCH_DELAY_S``. Renaming is fine, dropping is not, so the
#: first spelling the constructor accepts wins.
_DELAY_PARAMETERS: tuple[str, ...] = ("per_host_delay_s", "delay_s")


def _fetcher_kwargs(
    target: Any, *, user_agent: str, timeout_s: float, delay_s: float
) -> dict[str, Any]:
    """Map this provider's settings onto the fetcher's own keyword names.

    The fetcher is a sibling module written in parallel; pinning its constructor here would turn
    a renamed parameter into a silent behaviour change (or a ``TypeError`` before the first
    fetch). So the signature decides the spelling, and an uninspectable constructor gets the
    documented names — the best a caller can do without owning the contract.
    """
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):  # some C callables cannot be introspected
        parameters = None
    if parameters is None or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    ):
        return {"user_agent": user_agent, "timeout_s": timeout_s, "per_host_delay_s": delay_s}
    kwargs: dict[str, Any] = {}
    if "user_agent" in parameters:
        kwargs["user_agent"] = user_agent
    if "timeout_s" in parameters:
        kwargs["timeout_s"] = timeout_s
    for name in _DELAY_PARAMETERS:
        if name in parameters:
            kwargs[name] = delay_s
            break
    return kwargs


def _confidence_for(*, pages: int, verified: int, partial: bool) -> str:
    """Grade the run, never the company.

    ``high`` needs real breadth (>=3 pages) *and* real depth (>=6 verified findings), and is
    refused outright when a planned page went unread — a partial crawl cannot describe a site.
    ``medium`` is breadth without depth (two or more pages), or a verified fact at all;
    ``low`` is everything thinner than that.
    """
    if pages >= 3 and verified >= 6 and not partial:
        return "high"
    if pages and (verified or pages >= 2):
        return "medium"
    return "low"


def _fetcher_warnings(fetcher: Any) -> list[str]:
    """Problems the fetcher itself reported (an unreadable robots.txt, say), de-duplicated.

    They belong in the gaps: a robots file we could not parse changes what "nothing on this page
    mentions pricing" is allowed to mean.
    """
    raw = getattr(fetcher, "warnings", ()) or ()
    if isinstance(raw, str):
        raw = (raw,)
    try:
        items = list(raw)
    except TypeError:  # not a sequence; do not pretend to have read it
        return []
    return sorted({normalise(str(item)) for item in items if str(item).strip()})


def _gaps_text(
    *,
    findings: Sequence[Finding],
    failures: Sequence[str],
    dropped: Sequence[str],
    warnings: Sequence[str] = (),
    pages_read: int,
    planned: int | None,
) -> str | None:
    """Everything this report does *not* know, stated as something an operator can chase."""
    lines: list[str] = []
    for finding in findings:
        if finding.finding_type == "information_gap_tbd":
            lines.append(f"Open question: {finding.statement}")
    if failures:
        scope = (
            f"{len(failures)} of {planned} planned page(s) went unread"
            if planned
            else f"{len(failures)} page(s) went unread"
        )
        lines.append(f"This run is partial: {scope} ({pages_read} read).")
        lines.extend(f"Unread page — {failure}" for failure in failures)
    if dropped:
        lines.append(f"{len(dropped)} draft finding(s) did not survive grounding and were dropped.")
        lines.extend(f"Dropped — {item}" for item in dropped)
    lines.extend(f"Fetching was incomplete — {warning}" for warning in warnings)
    return "; ".join(lines) or None


def _summary_for(
    company: Mapping[str, Any],
    start_url: str,
    pages: Sequence[PageContent],
    failures: Sequence[str],
) -> str:
    """Say exactly which pages were read — no characterisation of the company at all.

    A summary that said "a design studio with a Webflow focus" would be an invented claim
    dressed as a finding. This one can be checked line by line against the sources above it.
    """
    host = urlparse(start_url).netloc or start_url
    label = str(company.get("name") or "").strip() or host
    ordered = sorted(pages, key=lambda item: item.url)
    listed = "; ".join(
        f"{item.title.strip() or '(untitled)'} <{item.url}>" for item in ordered
    )
    summary = (
        f"First-party research for {label}: read {len(pages)} page(s) from {host} — {listed}. "
        "Every finding below quotes one of these pages; nothing else was read."
    )
    if failures:
        summary += (
            f" {len(failures)} further page(s) could not be read and are listed as gaps, so this "
            "run is partial rather than a complete picture of the site."
        )
    if len(summary) > SUMMARY_CHARS:
        summary = summary[: SUMMARY_CHARS - 1].rstrip() + "…"
    return summary


__all__ = [
    "DEFAULT_DELAY_S",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_USER_AGENT",
    "FirstPartyResearchProvider",
    "ProviderRun",
    "failure_text",
    "start_url_for",
]
