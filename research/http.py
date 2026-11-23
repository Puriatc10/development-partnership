"""Polite, boring HTTP for first-party research.

The engine only ever reads a company's own public pages, so the interesting questions here are
not "how do we parse a response" but "were we allowed to ask, did we ask gently, and can a
hostile page hurt us". Those are policy decisions, and policy lives in one module with one
seam — the injected ``session`` — so it can be tested with no network at all.

Decisions worth arguing with:

* **A broken robots.txt means allow.** A 5xx, a timeout or a TLS failure on ``/robots.txt``
  records a warning in :attr:`Fetcher.warnings` and lets the fetch proceed. Treating an
  unreachable robots file as "deny everything" would let one flaky host stop a research run,
  which is the worse failure for a first-party engine. A *readable* ``Disallow`` is obeyed.
* **The cache is an aid, not evidence.** It exists so repeated runs and tests do not hammer a
  host. A cached entry is never more true than the live page; delete the directory to refetch.
* **Redirects are the site's instruction.** We record the final URL, but robots is checked for
  the URL we were asked to fetch, not re-litigated on every hop.
* **Size is enforced while streaming**, so a page that lies about ``Content-Length`` still
  cannot exhaust memory.

Charging the politeness budget: only page requests count. ``robots.txt`` is one small request
per host, made once, and is not charged against ``per_host_delay_s``.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import logging
import pathlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from research.research_types import FetchReason, FetchResult, ResearchFetchError

logger = logging.getLogger("eryndex.research")

#: Honest identity: the tool's name, its policy page (a contact URL), and a mailbox that exists.
DEFAULT_USER_AGENT = (
    "EryndexResearchBot/0.1 (+https://eryndex.dev/research-policy; "
    "first-party site research; contact: research@eryndex.dev)"
)

_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    "Accept-Language": "en",
}
_ROBOTS_HEADERS = {"Accept": "text/plain,*/*;q=0.5"}
_CHUNK_SIZE = 65536
_ROBOTS_READ_CAP = 512_000

#: Exception class names that mean "we waited and nobody answered". Matching on names keeps this
#: module importable without ``requests`` while still classifying the fake sessions that tests and
#: callers inject.
_TIMEOUT_NAMES = frozenset(
    {"Timeout", "ConnectTimeout", "ReadTimeout", "TimeoutError", "TimeoutException"}
)

# HTML's charset registry maps the latin-1 labels onto windows-1252; doing the same stops a single
# 0x92 byte from turning a curly quote into a control character.
_ENCODING_ALIASES = {
    "iso-8859-1": "cp1252",
    "iso8859-1": "cp1252",
    "latin-1": "cp1252",
    "latin1": "cp1252",
    "l1": "cp1252",
}
_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_.:\-]+)""",
    re.IGNORECASE,
)
_HEADER_CHARSET = re.compile(r"""charset\s*=\s*["']?([a-zA-Z0-9_.:\-]+)""", re.IGNORECASE)


def _monotonic() -> float:
    """Clock indirection: tests replace this with a fake instead of sleeping for real."""
    return time.monotonic()


def _sleep(seconds: float) -> None:
    """Sleep indirection, same reason as :func:`_monotonic`."""
    time.sleep(seconds)


@dataclass(frozen=True)
class _RobotsRules:
    """The ``User-agent: *`` rules that apply to us."""

    allow: tuple[str, ...] = ()
    disallow: tuple[str, ...] = ()
    crawl_delay: float | None = None

    def permits(self, target: str) -> bool:
        """Longest matching pattern wins; a tie goes to Allow (RFC 9309 §2.2.2)."""
        longest_allow = max(
            (len(pattern) for pattern in self.allow if _pattern_matches(pattern, target)),
            default=-1,
        )
        longest_disallow = max(
            (len(pattern) for pattern in self.disallow if _pattern_matches(pattern, target)),
            default=-1,
        )
        return longest_allow >= longest_disallow


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    """Compile one robots pattern. ``*`` is a run of anything; a trailing ``$`` anchors the end."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile("^" + expression + ("$" if anchored else ""))


def _pattern_matches(pattern: str, target: str) -> bool:
    """True when a robots path pattern covers ``target`` (a path, optionally with a query)."""
    if not pattern:
        return False
    try:
        return _compiled(pattern).match(target) is not None
    except re.error:  # pragma: no cover - pathological pattern from a hostile robots.txt
        return False


def _parse_robots(text: str) -> _RobotsRules:
    """Read the ``User-agent: *`` groups out of a robots.txt body.

    Only the wildcard group is honoured: we do not claim to be Googlebot, so a site that names
    other crawlers explicitly has not given us permission. Consecutive ``User-agent`` lines share
    one group of directives, which is why the parser tracks group boundaries rather than treating
    each line independently.
    """
    allow: list[str] = []
    disallow: list[str] = []
    crawl_delay: float | None = None
    applies = False
    directives_started = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()

        if key == "user-agent":
            if directives_started:  # a new group begins here
                applies = False
                directives_started = False
            applies = applies or value == "*"
            continue
        if not applies:
            continue
        directives_started = True

        if key == "disallow" and value:
            disallow.append(value)
        elif key == "allow" and value:
            allow.append(value)
        elif key == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0:
                crawl_delay = delay

    return _RobotsRules(tuple(allow), tuple(disallow), crawl_delay)


def _origin(parts: Any) -> str:
    """Scheme + host[:port], lowercased: the unit robots.txt is published for."""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _target(parts: Any) -> str:
    """The path a robots pattern is matched against, including the query string."""
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _header(headers: Any, name: str) -> str:
    """Case-insensitive header lookup that tolerates dicts, mapping-likes and pair sequences."""
    if not headers:
        return ""
    wanted = name.lower()
    try:
        pairs: Any = headers.items()
    except AttributeError:
        pairs = headers
    try:
        for key, value in pairs:
            if str(key).lower() == wanted:
                return str(value or "")
    except (TypeError, ValueError):
        return ""
    return ""


def _resolve_encoding(label: str | None) -> str | None:
    """Turn a declared charset label into an encoding Python knows, or ``None``."""
    if not label:
        return None
    candidate = label.strip().strip("\"'").lower()
    candidate = _ENCODING_ALIASES.get(candidate, candidate)
    try:
        codecs.lookup(candidate)
    except LookupError:
        return None
    return candidate


def _charset_from_header(content_type: str) -> str | None:
    """The charset the server declared, if any."""
    match = _HEADER_CHARSET.search(content_type or "")
    return match.group(1) if match else None


def _charset_from_meta(body: bytes) -> str | None:
    """The charset the document declares about itself, read from the first 4 KiB."""
    match = _META_CHARSET.search(body[:4096])
    if not match:
        return None
    return match.group(1).decode("ascii", "ignore")


def _decode_body(body: bytes, content_type: str) -> str:
    """Decode bytes using the declared charset, then UTF-8, then lossy UTF-8.

    Header beats meta (the transport is more authoritative than the document), and a declaration
    that cannot decode the bytes is abandoned rather than trusted: a page that says
    ``windows-1252`` while actually serving UTF-8 is common enough to plan for.
    """
    for label in (_charset_from_header(content_type), _charset_from_meta(body)):
        encoding = _resolve_encoding(label)
        if encoding is None:
            continue
        try:
            return body.decode(encoding).lstrip("\ufeff")
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return body.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace").lstrip("\ufeff")


def _reason_for(exc: BaseException) -> FetchReason:
    """Map a transport exception onto the reason vocabulary the caller reports."""
    if isinstance(exc, TimeoutError) or type(exc).__name__ in _TIMEOUT_NAMES:
        return "timeout"
    return "network"


def _describe(exc: BaseException) -> str:
    """One short line an operator can grep for."""
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:200]


def _now() -> str:
    """Fetched-at stamp: ISO 8601, UTC, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_session() -> Any:
    """A real ``requests`` session, imported lazily so extraction stays stdlib-only."""
    try:
        import requests  # optional at import time; required only when actually fetching
    except ImportError as exc:  # pragma: no cover - requests is in requirements.txt
        raise RuntimeError(
            "Fetcher needs `requests` when no session is injected; pass session=... instead"
        ) from exc
    return requests.Session()


class Fetcher:
    """Fetch pages politely: robots first, one host at a time, size-capped, optionally cached.

    The ``session`` seam exists so tests (and callers with their own transport) can supply any
    object with ``.get(url, **kwargs)`` returning something with ``.status_code``, ``.headers``,
    ``.text``, ``.url`` and ``.iter_content()``.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_s: float = 15.0,
        max_bytes: int = 3_000_000,
        per_host_delay_s: float = 1.0,
        cache_dir: pathlib.Path | None = None,
        session: Any = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("user_agent must name the tool; an empty agent is not honest")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.user_agent = user_agent
        self.timeout_s = float(timeout_s)
        self.max_bytes = int(max_bytes)
        self.per_host_delay_s = float(per_host_delay_s)
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir is not None else None
        self._session = session if session is not None else _default_session()

        #: Robots files we could not read. Kept, not raised: a broken file must not stop research.
        self.warnings: list[str] = []
        self._robots: dict[str, _RobotsRules] = {}
        self._last_request: dict[str, float] = {}

    # ------------------------------------------------------------------ robots

    def robots_allows(self, url: str) -> bool:
        """Whether the site's ``User-agent: *`` group lets us read ``url``.

        Non-HTTP URLs return ``False``: there is no robots.txt to consult, so there is no
        permission to point at.
        """
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        return self._robots_for(_origin(parts)).permits(_target(parts))

    def _robots_for(self, origin: str) -> _RobotsRules:
        """Rules for one origin, fetched at most once per Fetcher instance."""
        cached = self._robots.get(origin)
        if cached is not None:
            return cached
        rules = self._fetch_robots(origin)
        self._robots[origin] = rules
        return rules

    def _fetch_robots(self, origin: str) -> _RobotsRules:
        """Read ``/robots.txt``. Unreadable means allow-and-warn; see the module docstring."""
        url = f"{origin}/robots.txt"
        try:
            response = self._session.get(
                url,
                headers={"User-Agent": self.user_agent, **_ROBOTS_HEADERS},
                timeout=self.timeout_s,
                allow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001 - any transport failure means "unreadable"
            self._warn(f"robots.txt unreadable for {origin} ({_describe(exc)}); allowing")
            return _RobotsRules()

        status = int(getattr(response, "status_code", 0) or 0)
        if 200 <= status < 300:
            text = str(getattr(response, "text", "") or "")[:_ROBOTS_READ_CAP]
            return _parse_robots(text)
        if 400 <= status < 500:
            # 404 in practice: the site publishes no rules, which is permission to read.
            return _RobotsRules()
        self._warn(f"robots.txt at {origin} answered {status}; allowing")
        return _RobotsRules()

    def _warn(self, message: str) -> None:
        self.warnings.append(message)

    # ------------------------------------------------------------------ fetch

    def get(self, url: str) -> FetchResult:
        """Fetch one HTML page or raise :class:`ResearchFetchError` with an actionable reason."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ResearchFetchError(url, "network", "unsupported or relative URL")
        origin = _origin(parts)

        if not self.robots_allows(url):
            raise ResearchFetchError(url, "robots", "disallowed by robots.txt")

        cached = self._read_cache(url)
        if cached is not None:
            return cached

        self._wait(origin)
        try:
            response = self._request(url)
        finally:
            # A failed attempt still touched the host, so it still spends politeness.
            self._last_request[origin] = _monotonic()

        result = self._read_response(url, response)
        self._write_cache(url, result)
        return result

    def _request(self, url: str) -> Any:
        """Issue the GET, translating transport failures into typed reasons."""
        try:
            return self._session.get(
                url,
                headers={"User-Agent": self.user_agent, **_PAGE_HEADERS},
                timeout=self.timeout_s,
                stream=True,
                allow_redirects=True,
            )
        except Exception as exc:
            raise ResearchFetchError(url, _reason_for(exc), _describe(exc)) from exc

    def _read_response(self, url: str, response: Any) -> FetchResult:
        """Validate status, media type and size, then decode the body."""
        status = int(getattr(response, "status_code", 0) or 0)
        final_url = str(getattr(response, "url", "") or url)
        if not 200 <= status < 300:
            self._close(response)
            raise ResearchFetchError(url, "http_error", f"status {status} at {final_url}")

        content_type = _header(response.headers, "content-type")
        media_type = content_type.split(";")[0].strip().lower()
        if media_type not in _HTML_MEDIA_TYPES:
            self._close(response)
            raise ResearchFetchError(url, "non_html", content_type or "no content-type")

        declared = _header(response.headers, "content-length").strip()
        if declared.isdigit() and int(declared) > self.max_bytes:
            self._close(response)
            raise ResearchFetchError(
                url, "too_large", f"content-length {declared} over {self.max_bytes}"
            )

        body = self._read_body(url, response)
        self._close(response)
        text = (
            _decode_body(body, content_type) if body else str(getattr(response, "text", "") or "")
        )

        return FetchResult(
            url=final_url,
            status=status,
            content_type=content_type,
            text=text,
            fetched_at=_now(),
        )

    def _read_body(self, url: str, response: Any) -> bytes:
        """Stream the body into memory, refusing to grow past ``max_bytes``."""
        iter_content = getattr(response, "iter_content", None)
        if iter_content is None:
            return b""

        buffer = bytearray()
        try:
            for chunk in iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                buffer.extend(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
                if len(buffer) > self.max_bytes:
                    self._close(response)
                    raise ResearchFetchError(
                        url, "too_large", f"streamed body over {self.max_bytes} bytes"
                    )
        except ResearchFetchError:
            raise
        except Exception as exc:
            raise ResearchFetchError(url, _reason_for(exc), _describe(exc)) from exc
        return bytes(buffer)

    def _close(self, response: Any) -> None:
        """Release the connection; a response without ``close`` is fine to ignore."""
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("response.close() failed", exc_info=True)

    # ------------------------------------------------------------------ politeness

    def _delay_for(self, origin: str) -> float:
        """Our own floor or the site's Crawl-delay, whichever asks for more patience."""
        rules = self._robots.get(origin)
        crawl_delay = rules.crawl_delay if rules is not None else None
        return max(self.per_host_delay_s, crawl_delay or 0.0)

    def _wait(self, origin: str) -> None:
        """Sleep out whatever is left of the gap since the last request to this origin."""
        delay = self._delay_for(origin)
        if delay <= 0:
            return
        last_seen = self._last_request.get(origin)
        if last_seen is None:
            return
        remaining = delay - (_monotonic() - last_seen)
        if remaining > 0:
            _sleep(remaining)

    # ------------------------------------------------------------------ cache

    def _cache_path(self, url: str) -> pathlib.Path | None:
        """Content-addressed cache file for one URL, or ``None`` when caching is off."""
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> FetchResult | None:
        """A cache hit, or ``None``. A corrupt entry is a miss, never an error."""
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return FetchResult(
                url=str(payload["url"]),
                status=int(payload["status"]),
                content_type=str(payload["content_type"]),
                text=str(payload["text"]),
                fetched_at=str(payload["fetched_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, url: str, result: FetchResult) -> None:
        """Persist one result atomically. Losing the cache must not lose the fetch."""
        path = self._cache_path(url)
        if path is None:
            return
        payload = json.dumps(
            {
                "url": result.url,
                "status": result.status,
                "content_type": result.content_type,
                "text": result.text,
                "fetched_at": result.fetched_at,
            },
            ensure_ascii=False,
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            self._warn(f"cache write failed for {url} ({exc})")


__all__ = ["DEFAULT_USER_AGENT", "Fetcher"]
