"""OpenGTM adapter — ``discover`` capability.

**Two facts must be settled by a human before this adapter is wired up:**

1. **Which OpenGTM.** There are two unrelated projects with this name.
   ``debpalash/OpenGTM`` (v3.0.0, 2026-09-24, **AGPL-3.0**, Go + Postgres 16 +
   Redis, UI/API on :3000, ~4 GB RAM, no prebuilt images, no SaaS, OAuth2
   password grant at ``POST /auth/token`` form-encoded, 376 routes, optional MCP
   server on :3100) versus ``buildingopen/opengtm`` (MIT, Python, stale). AGPL-3.0
   is network copyleft and constrains what Eryndex may ship if the component is
   modified — a licensing decision, not an engineering one.
2. **Which route performs discovery.** The canonical repo exposes settings and
   provider routes; no documented "discover agencies" endpoint was verified.

So: the capability contract is fixed, but the transport is configured, not
assumed. ``ProviderConfig.paths["discover"]`` and a ``token_provider`` (OAuth2
password grant) must be supplied from configuration until a live route is
verified. Without them the adapter raises ``not_configured`` rather than guessing.

OpenGTM produces *candidates*, never companies. It holds no CRM credentials and
cannot write to it; staging and Human Gate 1 happen elsewhere (ADR-0005).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from contracts.errors import ProviderError
from contracts.models import SEGMENTS, Candidate
from contracts.provider import ProviderRequest, ProviderResult

from ..base import BaseProvider, ProviderConfig

APPROVED_SOURCES = {
    "webflow_experts",
    "webflow_showcase",
    "framer_partners",
    "framer_made_in",
    "clutch",
    "dribbble",
    "behance",
}
UNVERIFIED = "opengtm: discovery route and project identity unverified (see integration-verification.md)"


class OpenGTMProvider(BaseProvider):
    name = "opengtm"
    version = "3.0.0"
    capability = "discover"
    health_path = "/healthz"
    endpoint_verified = False

    def __init__(self, config: ProviderConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        # No default route: guessing a discovery endpoint would fabricate a contract.
        self.discover_path = config.path("discover", "")
        if not self.discover_path:
            raise ProviderError(
                "not_configured",
                "opengtm: no discover path configured; supply ProviderConfig.paths['discover'] "
                "after verifying the route (v3.0.0 exposes 376 routes, discovery not documented)",
                provider=self.name,
            )

    def validate(self, payload: Mapping[str, Any]) -> None:
        directive = payload.get("directive")
        if not isinstance(directive, Mapping):
            raise ProviderError(
                "schema_violation", "discover requires a 'directive' object", provider=self.name
            )
        segments = directive.get("segments")
        if not isinstance(segments, list) or not segments or not set(segments) <= SEGMENTS:
            raise ProviderError(
                "schema_violation", f"'segments' must be a non-empty subset of {sorted(SEGMENTS)}", provider=self.name
            )
        sources = directive.get("sources")
        if not isinstance(sources, list) or not sources or not set(sources) <= APPROVED_SOURCES:
            raise ProviderError(
                "schema_violation",
                f"'sources' must be a non-empty subset of approved sources {sorted(APPROVED_SOURCES)}",
                provider=self.name,
            )
        limit = directive.get("limit", 25)
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ProviderError("schema_violation", "'limit' must be between 1 and 200", provider=self.name)

    def _call(self, request: ProviderRequest, *, timeout: float) -> ProviderResult:
        directive = dict(request.payload["directive"])
        body = {
            "segments": list(directive["segments"]),
            "sources": list(directive["sources"]),
            "region": directive.get("region", "global"),
            "limit": int(directive.get("limit", 25)),
            "exclude_domains": list(directive.get("exclude_domains", [])),
        }
        resp = self._post(self.discover_path, body, timeout)
        self._raise_for_status(resp)
        raw = resp.json

        items = self._as_items(raw, ("candidates", "results", "companies", "data"))
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        candidates: list[dict[str, Any]] = []
        warnings: list[str] = [] if self.endpoint_verified else [UNVERIFIED]

        for item in items:
            name = str(item.get("name") or item.get("company_name") or "").strip()
            website = str(item.get("domain") or item.get("website") or item.get("url") or "").strip()
            domain = Candidate.normalize_domain(website)
            source_url = str(item.get("source_url") or website or "")
            source_name = str(item.get("source_name") or item.get("source") or "")
            if not name or not domain:
                warnings.append(f"candidate skipped (missing {'name' if not name else 'domain'}): {source_url[:120]}")
                continue
            if urlparse(source_url).scheme not in ("http", "https"):
                warnings.append(f"candidate {name} has no citable source_url; provenance incomplete")
            candidate = Candidate(
                name=name,
                domain=domain,
                source_name=source_name,
                source_url=source_url,
                discovered_at=str(item.get("discovered_at") or now),
                signals=dict(item.get("signals") or {}),
                normalized_name=Candidate.normalize_name(name),
            )
            candidates.append(candidate.to_row())

        if not candidates:
            raise ProviderError(
                "invalid_response", "discovery returned no usable candidates", provider=self.name
            )

        return ProviderResult(
            status="ok" if not warnings else "partial",
            data={"candidates": candidates, "requested_limit": body["limit"]},
            provider=self.info(),
            warnings=tuple(dict.fromkeys(warnings)),
        )
