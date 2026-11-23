"""Turn fetched HTML into the text, links and structured data a finding can cite.

Pure by construction: :func:`extract_page` takes a :class:`FetchResult` and returns a
:class:`PageContent` with no network, no disk and no clock. That makes it cheap to test against
saved fixtures, and it makes it impossible for extraction to "know" anything the page did not
say — which is the whole point of a first-party engine.

Extraction policy, in one place:

* Chrome is dropped (``script``, ``style``, ``noscript``, ``svg``, ``nav``, ``footer``,
  ``header``, ``aside``, ``form``, ``template``) because a finding grounded in a cookie banner
  is not a finding. ``<main>`` or ``<article>`` wins when it carries real prose; when it is a
  bare SPA shell, the body text (still minus chrome) is the better witness.
* Links are absolute and de-duplicated, same-host first, because the next stage asks "which of
  these pages are worth reading". Links *inside* chrome are still collected: a nav bar is
  exactly how a crawler learns the site's shape, and templates are the only inert content.
* ``jsonld`` keeps only objects, because a JSON-LD block that is not an object cannot be read as
  a claim about the page.

Only :mod:`html.parser` is used: it is in the stdlib, it never executes anything, and it keeps
working when the markup is broken — which, on real sites, it always is.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from research.research_types import FetchResult, PageContent, normalise

logger = logging.getLogger("eryndex.research")

#: Sentences shorter than this are usually labels, not claims.
MIN_SENTENCE_LENGTH = 30

_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "template"}
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3"})
# Block-level tags get a space on either side so "\"We</p><p>build\"" cannot fuse into "Webuild".
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "br",
        "tr",
        "td",
        "th",
        "section",
        "main",
        "article",
        "ul",
        "ol",
        "table",
        "blockquote",
        "dd",
        "dt",
        "pre",
        "figure",
        "figcaption",
        "hr",
        "dl",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
_TEXT_CONTAINERS = frozenset({"main", "article"})

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")


class _PageParser(HTMLParser):
    """Collect the pieces of a page we care about, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description = ""
        self.body: list[str] = []
        self.main: list[str] = []
        self.headings: list[str] = []
        self.hrefs: list[str] = []
        self.jsonld: list[dict] = []

        self._skip_stack: list[str] = []
        self._text_depth = 0
        self._in_title = False
        self._heading: str | None = None
        self._heading_parts: list[str] = []
        self._jsonld_parts: list[str] | None = None

    # -- helpers ---------------------------------------------------------

    def _break_block(self) -> None:
        """Insert a word boundary for a block-level element."""
        self.body.append(" ")
        if self._text_depth > 0:
            self.main.append(" ")

    def _push_skip(self, tag: str) -> None:
        if tag not in _VOID_TAGS:
            self._skip_stack.append(tag)

    def _pop_skip(self, tag: str) -> None:
        """Close ``tag`` and anything left open inside it, mirroring browser recovery."""
        if tag in self._skip_stack:
            while self._skip_stack:
                if self._skip_stack.pop() == tag:
                    break

    def _finish_jsonld(self) -> None:
        """Parse the JSON-LD block we were accumulating and forget it."""
        if self._jsonld_parts is None:
            return
        raw = "".join(self._jsonld_parts).strip()
        self._jsonld_parts = None
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except ValueError:  # invalid block: ignore it rather than guess what it meant
            return
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item:
                self.jsonld.append(item)

    # -- parser callbacks ------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}

        if tag == "script":
            declared = attributes.get("type", "").split(";")[0].strip().lower()
            if declared == "application/ld+json":
                self._jsonld_parts = []
            self._push_skip(tag)  # script text is never page copy
            return

        if tag == "meta":
            label = (attributes.get("name") or attributes.get("property") or "").lower()
            if label == "description" and not self.description:
                self.description = normalise(attributes.get("content", ""))
            return

        if tag == "title":
            self._in_title = True
            return

        if tag == "a":
            href = attributes.get("href", "").strip()
            # Templates are inert; a nav bar is not. Everything else is site structure worth
            # crawling, even when its text is chrome we drop.
            if href and "template" not in self._skip_stack:
                self.hrefs.append(href)
            return

        if tag in _HEADING_TAGS:
            self._break_block()
            if self._heading is None:
                self._heading = tag
                self._heading_parts = []
            return

        if tag in _TEXT_CONTAINERS:
            self._text_depth += 1
            self._break_block()
            return

        if tag in _SKIP_TAGS:
            self._push_skip(tag)
            return

        if tag in _BLOCK_TAGS:
            self._break_block()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """A self-closing tag never opens a scope, so it must not push one."""
        if tag.lower() in _BLOCK_TAGS:
            self._break_block()
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag == "title":
            self._in_title = False
            return

        if tag in _HEADING_TAGS:
            if self._heading == tag:
                text = normalise("".join(self._heading_parts))
                if text:
                    self.headings.append(text)
                self._heading = None
                self._heading_parts = []
            self._break_block()
            return

        if tag == "script":
            self._finish_jsonld()
            self._pop_skip(tag)
            return

        if tag in _TEXT_CONTAINERS:
            self._text_depth = max(0, self._text_depth - 1)
            self._break_block()
            return

        if tag in _SKIP_TAGS:
            self._pop_skip(tag)
            return

        if tag in _BLOCK_TAGS:
            self._break_block()

    def handle_data(self, data: str) -> None:
        if self._jsonld_parts is not None and "script" in self._skip_stack:
            self._jsonld_parts.append(data)
            return
        if self._skip_stack:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if not data.strip():
            return
        if self._heading is not None:
            self._heading_parts.append(data)
        self.body.append(data)
        if self._text_depth > 0:
            self.main.append(data)


def _absolute_links(hrefs: Iterable[str], base_url: str) -> tuple[str, ...]:
    """Resolve, filter and de-duplicate hrefs; same-host links come first."""
    base_host = urlsplit(base_url).netloc.lower()
    seen: set[str] = set()
    same_host: list[str] = []
    other_host: list[str] = []

    for href in hrefs:
        candidate = (href or "").strip()
        if not candidate or candidate.startswith("#"):
            continue
        absolute = urljoin(base_url, candidate)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue  # mailto:, tel:, javascript:, data: are not pages to read
        cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        if cleaned in seen:
            continue
        seen.add(cleaned)
        if parts.netloc.lower() == base_host:
            same_host.append(cleaned)
        else:
            other_host.append(cleaned)

    return tuple(same_host + other_host)


def _emails(text: str, hrefs: Iterable[str]) -> tuple[str, ...]:
    """Addresses printed on the page, plus those only revealed by a ``mailto:`` link."""
    found: list[str] = []
    for href in hrefs:
        if not href.lower().startswith("mailto:"):
            continue
        address = unquote(href[len("mailto:") :].split("?", 1)[0]).strip()
        if _EMAIL.fullmatch(address):
            found.append(address)
    found.extend(_EMAIL.findall(text))

    unique: list[str] = []
    seen: set[str] = set()
    for address in found:
        key = address.lower()
        if key not in seen:
            seen.add(key)
            unique.append(address)
    return tuple(unique)


_PHONE_DIGITS = re.compile(r"\d")
_TEL_HREF = "tel:"


def _phones(hrefs: Iterable[str]) -> tuple[str, ...]:
    """Phone numbers the page asserts through a ``tel:`` link, normalised and de-duplicated.

    A ``tel:`` link is the page telling a machine the number; prose is not. Reading phones from
    links is what stops a postal code appended to a street address from becoming a phone number.
    """
    found: list[str] = []
    for href in hrefs:
        if not href.lower().startswith(_TEL_HREF):
            continue
        raw = unquote(href[len(_TEL_HREF) :].split("?", 1)[0]).strip()
        digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
        if not (9 <= len(digits.lstrip("+")) <= 15):
            continue
        found.append(raw if raw.startswith("+") else "+" + digits.lstrip("+"))

    unique: list[str] = []
    seen: set[str] = set()
    for number in found:
        key = number.replace(" ", "").replace("-", "")
        if key not in seen:
            seen.add(key)
            unique.append(number)
    return tuple(unique)


def extract_page(result: FetchResult) -> PageContent:
    """Read one fetched page into :class:`PageContent`. No I/O, no network, no clock."""
    parser = _PageParser()
    if result.text:
        try:
            parser.feed(result.text)
            parser.close()
        except Exception:
            logger.debug("partial extraction for %s", result.url, exc_info=True)

    body_text = normalise(" ".join(parser.body))
    main_text = normalise(" ".join(parser.main))
    # A <main> holding a real paragraph is the better witness; a <main> that is an empty SPA
    # shell is not, so fall back to the chrome-stripped body.
    text = main_text if len(main_text) >= 200 else (body_text or main_text)

    title = normalise("".join(parser.title_parts))
    return PageContent(
        url=result.url,
        title=title,
        description=parser.description,
        text=text,
        headings=tuple(parser.headings),
        links=_absolute_links(parser.hrefs, result.url),
        jsonld=tuple(parser.jsonld),
        emails=_emails(f"{title} {parser.description} {text}", parser.hrefs),
        phones=_phones(parser.hrefs),
        fetched_at=result.fetched_at,
    )


def sentences(text: str) -> list[str]:
    """Split prose into sentences long enough to be evidence, in the order they appeared.

    Deliberately simple: break after ``.``/``!``/``?`` followed by whitespace. Abbreviation
    handling would need a dictionary and would make the output depend on it; a stable rule that
    occasionally keeps "Mr. Smith" together is more useful than a clever one nobody can predict.
    """
    flat = normalise(text)
    if not flat:
        return []
    kept: list[str] = []
    for piece in _SENTENCE_BREAK.split(flat):
        candidate = piece.strip()
        if len(candidate) >= MIN_SENTENCE_LENGTH and any(ch.isalpha() for ch in candidate):
            kept.append(candidate)
    return kept


__all__ = ["MIN_SENTENCE_LENGTH", "extract_page", "sentences"]
