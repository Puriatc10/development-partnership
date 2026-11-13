"""Crawl4AI adapter — ``crawl`` capability.

Crawl4AI's HTTP surface is **not yet verified** in this environment (BLK-02:
Docker daemon down). The request/response shaping below is written against the
documented ``POST /crawl`` operation and is deliberately tolerant of envelope
variation, so a verified endpoint can be plugged in through
``ProviderConfig.paths`` without touching workflow code.

Verification status is tracked in ``reports/eryndex/integration-verification.md``.
Until that file records a live response, ``endpoint_verified`` stays ``False``
and results carry a warning.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping
from urllib.parse import urlparse

from contracts.errors import ProviderError
from contracts.provider import Evidence, ProviderRequest, ProviderResult

from ..base import BaseProvider, ProviderConfig

VALID_EXTRACT = {"text", "links", "metadata"}
UNVERIFIED = "crawl4ai endpoint shape unverified (see reports/eryndex/integration-verification.md)"


class Crawl4AIProvider(BaseProvider):
    name = "crawl4ai"
    version = "unverified"
    capability = "crawl"
    health_path = "/health"
    endpoint_verified = False

    def __init__(self, config: ProviderConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.crawl_path = config.path("crawl", "/crawl")

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
        if not isinstance(max_pages, int) or max_pages < 1 or max_pages > 50:
            raise ProviderError(
                "schema_violation", "'max_pages' must be an integer between 1 and 50", provider=self.name
            )
        extract = payload.get("extract", ["text"])
        if not isinstance(extract, list) or not set(extract) <= VALID_EXTRACT:
            raise ProviderError(
                "schema_violation",
                f"'extract' must be a subset of {sorted(VALID_EXTRACT)}",
                provider=self.name,
            )

    def _call(self, request: ProviderRequest, *, timeout: float) -> ProviderResult:
        payload = request.payload
        urls = list(dict.fromkeys(payload["urls"]))[: int(payload.get("max_pages", len(payload["urls"])))]
        body = {
            "urls": urls,
            "crawler_config": {"cache_mode": "BYPASS" if payload.get("bypass_cache") else "ENABLED"},
        }
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
            text = (
                item.get("markdown")
                or item.get("raw_markdown")
                or item.get("text")
                or item.get("extracted_content")
                or ""
            )
            ok = item.get("success", True) is not False
            if not ok:
                warnings.append(f"page failed: {url or '<unknown>'}")
            if not text:
                warnings.append(f"page returned no text: {url or '<unknown>'}")

            meta_raw = item.get("metadata")
            meta: Mapping[str, Any] = meta_raw if isinstance(meta_raw, Mapping) else {}
            pages.append(
                {
                    "url": url,
                    "title": str(meta.get("title") or item.get("title") or ""),
                    "text": text,
                    "word_count": len(str(text).split()),
                    "status": "ok" if ok and text else "empty",
                    "metadata": dict(meta),
                }
            )
            if url and text:
                evidence.append(
                    Evidence(
                        url=url,
                        excerpt=str(text)[:600],
                        provider=self.name,
                        provider_version=self.version,
                        content_hash=hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
                        title=str(meta.get("title") or ""),
                    )
                )

        usable = [p for p in pages if p["status"] == "ok"]
        status = "ok" if usable and len(usable) == len(pages) else "partial"
        if not usable:
            raise ProviderError("invalid_response", "no page produced usable text", provider=self.name)
        return ProviderResult(
            status=status,
            data={"pages": pages, "requested": len(urls), "usable": len(usable)},
            provider=self.info(),
            evidence=tuple(evidence),
            warnings=tuple(dict.fromkeys(warnings)),
        )
