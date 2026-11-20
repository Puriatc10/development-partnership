"""Crawl4AI adapter — ``crawl`` capability, contract-corrected against v0.9.5.

What was verified against the pinned upstream source (``crawl4ai==0.9.5`` wheel
installed from PyPI), rather than assumed:

* ``MarkdownGenerationResult`` has fields ``raw_markdown``,
  ``markdown_with_citations``, ``references_markdown``, ``fit_markdown``,
  ``fit_html`` (``crawl4ai/models.py:120-128``). ``CrawlResult.markdown`` is
  backed by a private attribute and is re-emitted by the ``model_dump`` override
  (``models.py:254``), so over HTTP it arrives as a **nested object**, not a
  string. Reading it with ``str()`` stores a dict repr as the evidence excerpt
  and hashes that repr instead of the text.
* ``CacheMode`` values are lowercase — ``enabled``, ``disabled``,
  ``read_only``, ``write_only``, ``bypass`` (``cache_context.py:16-20``). The
  server's untrusted-config gate validates ``cache_mode.lower()`` against that
  set (``async_configs.py:462-467``); this adapter sends the canonical lowercase
  value so enum coercion cannot fail.
* Request configuration is the typed envelope ``{"type": <ClassName>,
  "params": {...}}`` parsed by ``CrawlerRunConfig.load(..., provenance=UNTRUSTED)``
  (``async_configs.py:1280-1288``), and untrusted fields are filtered against
  ``UNTRUSTED_FIELD_ALLOWLIST`` (``async_configs.py:283-330``). Only keys on that
  allowlist are sent.
* Extraction is not a top-level request field. In v0.9.5 it is expressed through
  ``CrawlerRunConfig`` fields: ``only_text`` for text-only extraction, and
  ``exclude_external_links`` / ``exclude_social_media_links`` for link
  selection. The adapter maps requested modes onto those fields instead of
  inventing an ``extract`` parameter, and rejects a mode it cannot map.

Provenance limitation: v0.9.5 exposes no stable per-result identifier —
``CrawlResult`` carries ``url``, ``status_code``, ``cache_status`` and the
markdown payload, but no id. Evidence is therefore keyed on provider +
provider_version + url + content_hash + retrieved_at + title, and
``provider_result_id`` is reported as ``None`` rather than fabricated.

**Still not live-verified**: ``endpoint_verified`` stays ``False`` and every
result carries an ``UNVERIFIED`` warning until a real crawl succeeds against a
running server (BLK-02 in ``reports/eryndex/crawl4ai-blocker-analysis.md``).
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping
from urllib.parse import urlparse

from contracts.errors import ProviderError
from contracts.provider import Evidence, ProviderRequest, ProviderResult

from ..base import BaseProvider, ProviderConfig

#: Extraction modes this adapter can honour. Anything else is rejected loudly.
SUPPORTED_EXTRACT = ("text", "links", "metadata")

#: Where the readable body may live in a v0.9.5 payload, best first. Raw markdown
#: is the faithful text; the readability-filtered ``fit_markdown`` is a fallback.
MARKDOWN_TEXT_KEYS = ("raw_markdown", "markdown_with_citations", "references_markdown", "fit_markdown")

#: BrowserConfig fields the server accepts from an untrusted caller
#: (``async_configs.py:284-291``), with the JSON type each must carry.
BROWSER_FIELDS: dict[str, str] = {
    "browser_type": "str",
    "headless": "bool",
    "browser_mode": "str",
    "viewport_width": "int",
    "viewport_height": "int",
    "device_scale_factor": "number",
    "accept_downloads": "bool",
    "java_script_enabled": "bool",
    "text_mode": "bool",
    "light_mode": "bool",
    "enable_stealth": "bool",
    "avoid_ads": "bool",
    "avoid_css": "bool",
    "user_agent": "str",
    "user_agent_mode": "str",
    "memory_saving_mode": "bool",
    "max_pages_before_recycle": "int",
}

UNVERIFIED = "crawl4ai: documented surface (v0.9.5) not yet verified against a live server"
MAX_LINKS_RETURNED = 50
MIN_PAGE_TIMEOUT_MS = 1_000
MAX_PAGE_TIMEOUT_MS = 60_000
#: CrawlerRunConfig fields this adapter is willing to set (all on the server allowlist).
RUN_CONFIG_FIELDS = frozenset(
    {"cache_mode", "only_text", "exclude_external_links", "exclude_social_media_links", "page_timeout"}
)


def _type_ok(value: Any, tag: str) -> bool:
    """JSON-level type check that refuses booleans where numbers are expected."""
    if tag == "bool":
        return isinstance(value, bool)
    if tag == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if tag == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tag == "str":
        return isinstance(value, str)
    return False


def normalize_markdown(value: Any) -> str:
    """Flatten a v0.9.5 markdown payload into real text.

    Accepts a string, a ``MarkdownGenerationResult``-shaped mapping (what
    ``model_dump`` emits), or one level of nesting around either. A mapping is
    never stringified: if it holds no string body the result is ``""``, so the
    caller reports "no usable text" instead of citating ``{'raw_markdown': ...}``.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in MARKDOWN_TEXT_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for key in ("markdown", "result", "data"):
            if key in value:
                nested = normalize_markdown(value[key])
                if nested:
                    return nested
    return ""


def _page_text(item: Mapping[str, Any]) -> str:
    for key in ("markdown", "raw_markdown", "text", "extracted_content"):
        text = normalize_markdown(item.get(key))
        if text:
            return text
    return ""


def _page_links(item: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = item.get("links")
    if not isinstance(raw, Mapping):
        return {"internal": [], "external": []}
    out: dict[str, list[str]] = {}
    for kind in ("internal", "external"):
        entries = raw.get(kind)
        urls: list[str] = []
        if isinstance(entries, list):
            for entry in entries:
                href = entry.get("href") if isinstance(entry, Mapping) else None
                if isinstance(href, str) and href:
                    urls.append(href)
                if len(urls) >= MAX_LINKS_RETURNED:
                    break
        out[kind] = urls
    return out


def _page_title(item: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    for candidate in (meta.get("title"), item.get("title"), meta.get("og:title")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


class Crawl4AIProvider(BaseProvider):
    name = "crawl4ai"
    version = "0.9.5"
    capability = "crawl"
    health_path = "/health"
    endpoint_verified = False

    def __init__(self, config: ProviderConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.crawl_path = config.path("crawl", "/crawl")

    # -- validation --------------------------------------------------------
    def validate(self, payload: Mapping[str, Any]) -> None:
        urls = payload.get("urls")
        if not isinstance(urls, list) or not urls:
            raise ProviderError("schema_violation", "crawl requires a non-empty 'urls' list", provider=self.name)
        for url in urls:
            if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https"):
                raise ProviderError(
                    "schema_violation", f"invalid url in crawl request: {url!r}", provider=self.name
                )

        max_pages = payload.get("max_pages", len(urls))
        if not _type_ok(max_pages, "int") or not 1 <= max_pages <= 50:
            raise ProviderError(
                "schema_violation", "'max_pages' must be an integer between 1 and 50", provider=self.name
            )

        extract = payload.get("extract", ["text"])
        if not isinstance(extract, (list, tuple)) or not extract:
            raise ProviderError("schema_violation", "'extract' must be a non-empty list", provider=self.name)
        unsupported = [mode for mode in extract if mode not in SUPPORTED_EXTRACT]
        if unsupported:
            raise ProviderError(
                "schema_violation",
                f"unsupported extract mode(s) {unsupported}; v0.9.5 supports {list(SUPPORTED_EXTRACT)} "
                "through CrawlerRunConfig fields and this adapter will not invent a request field for the rest",
                provider=self.name,
            )

        if "bypass_cache" in payload and not _type_ok(payload["bypass_cache"], "bool"):
            raise ProviderError("schema_violation", "'bypass_cache' must be a boolean", provider=self.name)

        timeout_ms = payload.get("page_timeout_ms")
        if timeout_ms is not None and (
            not _type_ok(timeout_ms, "int") or not MIN_PAGE_TIMEOUT_MS <= timeout_ms <= MAX_PAGE_TIMEOUT_MS
        ):
            raise ProviderError(
                "schema_violation",
                f"'page_timeout_ms' must be an integer between {MIN_PAGE_TIMEOUT_MS} and {MAX_PAGE_TIMEOUT_MS}",
                provider=self.name,
            )

        self._validate_browser_config(payload.get("browser_config"))

    def _validate_browser_config(self, browser: Any) -> None:
        if browser is None:
            return
        if not isinstance(browser, Mapping):
            raise ProviderError("schema_violation", "'browser_config' must be an object", provider=self.name)
        unknown = sorted(set(browser) - set(BROWSER_FIELDS))
        if unknown:
            raise ProviderError(
                "schema_violation",
                f"'browser_config' contains unsupported field(s) {unknown}; allowed: {sorted(BROWSER_FIELDS)}",
                provider=self.name,
            )
        for key, value in browser.items():
            if not _type_ok(value, BROWSER_FIELDS[key]):
                raise ProviderError(
                    "schema_violation",
                    f"'browser_config.{key}' must be a {BROWSER_FIELDS[key]}",
                    provider=self.name,
                )

    # -- request construction ---------------------------------------------
    def build_body(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Build the wire body. Public so tests can assert what actually goes out."""
        urls = list(dict.fromkeys(payload["urls"]))[: int(payload.get("max_pages", len(payload["urls"])))]
        extract = list(dict.fromkeys(payload.get("extract") or ["text"]))

        run_params: dict[str, Any] = {
            # canonical CacheMode value: lowercase, exactly what the enum defines
            "cache_mode": "bypass" if payload.get("bypass_cache") else "enabled",
        }
        if extract == ["text"]:
            # text-only extraction; asking for links or metadata too means they must survive
            run_params["only_text"] = True
        if "links" in extract:
            run_params["exclude_external_links"] = False
            run_params["exclude_social_media_links"] = False
        if payload.get("page_timeout_ms") is not None:
            run_params["page_timeout"] = int(payload["page_timeout_ms"])

        if not set(run_params) <= RUN_CONFIG_FIELDS:
            raise ProviderError(
                "internal_error", "a crawler_config field escaped the allowlist", provider=self.name
            )
        body: dict[str, Any] = {
            "urls": urls,
            "crawler_config": {"type": "CrawlerRunConfig", "params": run_params},
        }
        browser = payload.get("browser_config")
        if isinstance(browser, Mapping) and browser:
            body["browser_config"] = {"type": "BrowserConfig", "params": dict(browser)}
        return body

    # -- call --------------------------------------------------------------
    def _call(self, request: ProviderRequest, *, timeout: float) -> ProviderResult:
        payload = request.payload
        body = self.build_body(payload)
        requested_extract = list(dict.fromkeys(payload.get("extract") or ["text"]))

        resp = self._post(self.crawl_path, body, timeout)
        self._raise_for_status(resp)
        raw = resp.json

        items = self._as_items(raw, ("results", "data", "pages"))
        if not items:
            raise ProviderError(
                "invalid_response", "crawl response contained no page results", provider=self.name
            )

        pages: list[dict[str, Any]] = []
        evidence: list[Evidence] = []
        warnings: list[str] = [] if self.endpoint_verified else [UNVERIFIED]

        for item in items:
            url = str(item.get("url") or item.get("link") or "")
            meta_raw = item.get("metadata")
            meta: Mapping[str, Any] = meta_raw if isinstance(meta_raw, Mapping) else {}
            title = _page_title(item, meta)
            text = _page_text(item)
            ok = item.get("success", True) is not False

            if not ok:
                warnings.append(f"page failed: {url or '<unknown>'}")
            if not text:
                warnings.append(f"page returned no usable text: {url or '<unknown>'}")

            page: dict[str, Any] = {
                "url": url,
                "title": title,
                "status": "ok" if ok and text else "empty",
                "word_count": len(text.split()) if text else 0,
                "markdown_chars": len(text),
                "metadata": dict(meta),
                "links": _page_links(item) if "links" in requested_extract else None,
            }
            if "text" in requested_extract or "metadata" in requested_extract:
                page["text"] = text
            pages.append(page)

            if url and text:
                evidence.append(
                    Evidence(
                        url=url,
                        excerpt=text[: Evidence.MAX_EXCERPT],
                        provider=self.name,
                        provider_version=self.version,
                        # hash the normalized text, never a dict repr
                        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        title=title,
                    )
                )

        usable = [p for p in pages if p["status"] == "ok"]
        if not usable:
            raise ProviderError(
                "invalid_response",
                "no page produced usable text (markdown may be nested or empty; see normalize_markdown)",
                provider=self.name,
            )

        status = "ok" if len(usable) == len(pages) else "partial"
        return ProviderResult(
            status=status,
            data={
                "pages": pages,
                "requested": len(body["urls"]),
                "usable": len(usable),
                "extract": requested_extract,
                "provider_result_id": None,  # v0.9.5 issues none; documented, not invented
            },
            provider=self.info(),
            evidence=tuple(evidence),
            warnings=tuple(dict.fromkeys(warnings)),
        )
