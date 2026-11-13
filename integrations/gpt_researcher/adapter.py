"""GPT Researcher adapter — ``research`` capability.

The adapter returns a *report plus sources*, not findings. Turning prose into
atomic, categorized findings is the intelligence layer's job (it needs evidence
and citation checks); a provider is not trusted to grade its own confidence.

Server surface is **unverified** here (BLK-03: no model/retriever credentials),
so ``endpoint_verified`` is ``False`` and every result carries a warning.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from contracts.errors import ProviderError
from contracts.models import CompanyRef, SEGMENTS
from contracts.provider import Evidence, ProviderRequest, ProviderResult

from ..base import BaseProvider, ProviderConfig

QUESTIONS = {"services", "tech_stack", "clients", "team", "growth", "engineering_gaps", "pricing"}
UNVERIFIED = "gpt-researcher endpoint shape unverified (see reports/eryndex/integration-verification.md)"


class GPTResearcherProvider(BaseProvider):
    name = "gpt_researcher"
    version = "unverified"
    capability = "research"
    health_path = "/health"
    endpoint_verified = False

    def __init__(self, config: ProviderConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.report_path = config.path("report", "/report/")

    def validate(self, payload: Mapping[str, Any]) -> None:
        company = payload.get("company")
        if not isinstance(company, Mapping):
            raise ProviderError("schema_violation", "research requires a 'company' object", provider=self.name)
        name = str(company.get("name") or "").strip()
        domain = str(company.get("primary_domain") or "").strip()
        segment = str(company.get("segment") or "").strip()
        if not name or not domain:
            raise ProviderError(
                "schema_violation", "company.name and company.primary_domain are required", provider=self.name
            )
        if segment and segment not in SEGMENTS:
            raise ProviderError("schema_violation", f"unknown segment {segment!r}", provider=self.name)
        questions = payload.get("questions", [])
        if not isinstance(questions, list) or not set(questions) <= QUESTIONS:
            raise ProviderError(
                "schema_violation", f"'questions' must be a subset of {sorted(QUESTIONS)}", provider=self.name
            )
        max_sources = payload.get("max_sources", 12)
        if not isinstance(max_sources, int) or not 1 <= max_sources <= 50:
            raise ProviderError(
                "schema_violation", "'max_sources' must be between 1 and 50", provider=self.name
            )

    @staticmethod
    def build_task(payload: Mapping[str, Any]) -> str:
        company = payload["company"]
        questions = payload.get("questions") or sorted(QUESTIONS - {"pricing"})
        focus = "; ".join(questions)
        return (
            f"Research the company {company['name']} ({company['primary_domain']}), a "
            f"{company.get('segment') or 'digital agency'}. Focus on: {focus}. "
            "For every claim, record the exact source URL and a verbatim excerpt. "
            "State clearly what could not be verified; never fill gaps with plausible assumptions. "
            "Do not infer pricing, headcount, or client names without a citable source."
        )

    def _call(self, request: ProviderRequest, *, timeout: float) -> ProviderResult:
        payload = request.payload
        body = {
            "task": self.build_task(payload),
            "report_type": payload.get("report_type", "research_report"),
            "report_source": payload.get("report_source", "web"),
            "tone": "objective",
            "max_sources": int(payload.get("max_sources", 12)),
        }
        resp = self._post(self.report_path, body, timeout)
        self._raise_for_status(resp)
        raw = resp.json

        if isinstance(raw, Mapping):
            report = raw.get("report") or raw.get("research_report") or raw.get("summary") or ""
            sources_raw = raw.get("sources") or raw.get("source_urls") or raw.get("citations") or []
        elif isinstance(raw, str):
            report, sources_raw = raw, []
        else:
            raise ProviderError("invalid_response", "unexpected report payload", provider=self.name)

        if not str(report).strip():
            raise ProviderError("invalid_response", "provider returned an empty report", provider=self.name)

        evidence: list[Evidence] = []
        for src in sources_raw if isinstance(sources_raw, list) else []:
            if isinstance(src, Mapping):
                url = str(src.get("url") or src.get("href") or "")
                title = str(src.get("title") or "")
                excerpt = str(src.get("snippet") or src.get("excerpt") or "")
            else:
                url, title, excerpt = str(src), "", ""
            if urlparse(url).scheme in ("http", "https"):
                evidence.append(
                    Evidence(
                        url=url,
                        excerpt=excerpt,
                        provider=self.name,
                        provider_version=self.version,
                        title=title,
                    )
                )

        warnings: list[str] = [] if self.endpoint_verified else [UNVERIFIED]
        status = "ok"
        if not evidence:
            status = "partial"
            warnings.append("report returned without citable sources; findings cannot be evidence-backed")
        elif len(evidence) < 3:
            warnings.append(f"only {len(evidence)} source(s) returned; confidence must be downgraded")

        return ProviderResult(
            status=status,
            data={
                "report": str(report),
                "sources": [e.to_dict() for e in evidence],
                "company": dict(payload["company"]),
            },
            provider=self.info(),
            evidence=tuple(evidence),
            warnings=tuple(dict.fromkeys(warnings)),
        )


def company_ref(company: Mapping[str, Any]) -> CompanyRef:
    """Small helper so callers construct a validated company reference."""
    return CompanyRef(
        name=str(company["name"]),
        primary_domain=str(company["primary_domain"]),
        segment=str(company.get("segment") or "digital_agency"),
    )
