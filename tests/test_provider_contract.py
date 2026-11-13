"""Contract tests for the provider layer (ADR-0003).

These tests exercise the adapter machinery against a fake transport: retries,
timeout normalization, schema validation, health reporting, and the guarantee
that a vendor error never escapes as a raw exception.

They deliberately do NOT claim any vendor integration works. Live verification
lives in reports/eryndex/integration-verification.md.
"""

from __future__ import annotations

import pytest

from contracts.errors import ProviderError
from contracts.provider import Evidence, ProviderRequest, ProviderResult
from integrations.base import (
    BaseProvider,
    HttpTransport,
    ProviderConfig,
    TransportResponse,
    TransportTimeout,
    TransportUnavailable,
)
from integrations.crawl4ai import Crawl4AIProvider
from integrations.gpt_researcher import GPTResearcherProvider
from integrations.opengtm import OpenGTMProvider


class FakeTransport(HttpTransport):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, json_body=None, headers=None, timeout=60.0):
        self.calls.append(
            {"method": method, "url": url, "body": json_body, "timeout": timeout, "headers": dict(headers or {})}
        )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def req(capability, payload, timeout=5.0, key="k1"):
    return ProviderRequest(
        capability=capability, payload=payload, idempotency_key=key, timeout_s=timeout, trace_id="t-1"
    )


def crawl_provider(responses):
    transport = FakeTransport(responses)
    provider = Crawl4AIProvider(
        ProviderConfig(name="crawl4ai", base_url="http://crawl4ai:11235"),
        transport=transport,
        sleep=lambda _: None,
    )
    return provider, transport


def crawl_payload(**over):
    payload = {"urls": ["https://example.com/about"], "max_pages": 3, "extract": ["text", "metadata"]}
    payload.update(over)
    return payload


# --- retry, timeout and error normalization -------------------------------


def test_unavailable_is_retried_then_succeeds():
    provider, transport = crawl_provider(
        [
            TransportUnavailable("connection refused"),
            TransportResponse(200, '{"results": [{"url": "https://example.com/about", "markdown": "hello"}]}'),
        ]
    )
    result = provider.execute(req("crawl", crawl_payload()))
    assert result.status == "ok"
    assert len(transport.calls) == 2
    assert provider.metrics["retries"] == 1


def test_timeout_exhausts_retries_and_raises_normalized_error():
    provider, transport = crawl_provider(
        [TransportTimeout("deadline"), TransportTimeout("deadline"), TransportTimeout("deadline")]
    )
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("crawl", crawl_payload()))
    assert exc.value.code == "timeout"
    assert exc.value.retryable is True
    assert len(transport.calls) == 3  # 1 attempt + 2 retries


def test_429_is_rate_limited_and_retried():
    provider, transport = crawl_provider(
        [
            TransportResponse(429, "slow down"),
            TransportResponse(200, '{"results": [{"url": "https://example.com", "markdown": "ok"}]}'),
        ]
    )
    result = provider.execute(req("crawl", crawl_payload(urls=["https://example.com"])))
    assert result.status == "ok"
    assert len(transport.calls) == 2


def test_401_is_not_retried():
    provider, transport = crawl_provider([TransportResponse(401, "unauthorized")])
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("crawl", crawl_payload()))
    assert exc.value.code == "unauthorized"
    assert exc.value.retryable is False
    assert len(transport.calls) == 1


def test_invalid_payload_fails_before_any_network_call():
    provider, transport = crawl_provider([])
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("crawl", {"urls": [], "max_pages": 3}))
    assert exc.value.code == "schema_violation"
    assert transport.calls == []


def test_wrong_capability_is_rejected():
    provider, transport = crawl_provider([])
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("research", {"company": {"name": "x", "primary_domain": "x.com"}}))
    assert exc.value.code == "schema_violation"
    assert transport.calls == []


# --- health ---------------------------------------------------------------


def test_health_without_base_url_is_configuration_failure_not_exception():
    provider = Crawl4AIProvider(ProviderConfig(name="crawl4ai"), transport=FakeTransport([]))
    health = provider.health()
    assert health.healthy is False
    assert "not_configured" in health.detail


def test_health_never_raises_when_provider_is_down():
    provider, _ = crawl_provider([TransportUnavailable("refused")])
    health = provider.health()
    assert health.healthy is False
    assert "unreachable" in health.detail


def test_health_ok():
    provider, _ = crawl_provider([TransportResponse(200, "{}")])
    assert provider.health().healthy is True


# --- normalization --------------------------------------------------------


def test_crawl_normalizes_pages_and_evidence():
    provider, _ = crawl_provider(
        [
            TransportResponse(
                200,
                '{"results": [{"url": "https://a.com/x", "markdown": "Some page text", '
                '"metadata": {"title": "A"}}]}',
            )
        ]
    )
    result = provider.execute(req("crawl", crawl_payload(urls=["https://a.com/x"])))
    assert result.status == "ok"
    page = result.data["pages"][0]
    assert page["title"] == "A"
    assert page["status"] == "ok"
    assert result.evidence[0].url == "https://a.com/x"
    assert result.evidence[0].content_hash
    assert result.trace_id == "t-1"


def test_crawl_reports_partial_when_a_page_has_no_text():
    provider, _ = crawl_provider(
        [
            TransportResponse(
                200,
                '{"results": [{"url": "https://a.com/x", "markdown": "text"}, '
                '{"url": "https://a.com/y", "success": false}]}',
            )
        ]
    )
    result = provider.execute(
        req("crawl", crawl_payload(urls=["https://a.com/x", "https://a.com/y"], max_pages=2))
    )
    assert result.status == "partial"
    assert result.warnings  # required by the contract for partial results


def test_crawl_with_no_usable_text_is_an_invalid_response():
    provider, transport = crawl_provider(
        [
            TransportResponse(200, '{"results": [{"url": "https://a.com"}]}'),
            TransportResponse(200, '{"results": [{"url": "https://a.com"}]}'),
        ]
    )
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("crawl", crawl_payload(urls=["https://a.com"])))
    assert exc.value.code == "invalid_response"
    # malformed responses are retried exactly once, not for the full retry budget
    assert len(transport.calls) == 2


# --- research -------------------------------------------------------------


def research_provider(responses):
    transport = FakeTransport(responses)
    provider = GPTResearcherProvider(
        ProviderConfig(name="gpt_researcher", base_url="http://researcher:8000"),
        transport=transport,
        sleep=lambda _: None,
    )
    return provider, transport


def research_payload(**over):
    payload = {
        "company": {"name": "Refokus", "primary_domain": "refokus.com", "segment": "webflow_studio"},
        "questions": ["services", "tech_stack"],
        "max_sources": 5,
    }
    payload.update(over)
    return payload


def test_research_returns_report_and_citable_sources():
    provider, transport = research_provider(
        [
            TransportResponse(
                200,
                '{"report": "Refokus is a Webflow enterprise partner.", "sources": '
                '["https://refokus.com/about", "https://webflow.com/partners/refokus"]}',
            )
        ]
    )
    result = provider.execute(req("research", research_payload()))
    assert result.status == "ok"
    assert len(result.evidence) == 2
    assert "webflow" in result.data["report"].lower()
    # the prompt must carry the evidence rule, not just the company name
    assert "never fill gaps" in transport.calls[0]["body"]["task"].lower()


def test_research_without_sources_is_partial_and_warns():
    provider, _ = research_provider([TransportResponse(200, '{"report": "A report with no citations."}')])
    result = provider.execute(req("research", research_payload()))
    assert result.status == "partial"
    assert any("without citable sources" in w for w in result.warnings)


def test_research_rejects_empty_report():
    provider, _ = research_provider(
        [
            TransportResponse(200, '{"report": "   "}'),
            TransportResponse(200, '{"report": "   "}'),
        ]
    )
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("research", research_payload()))
    assert exc.value.code == "invalid_response"


# --- discovery ------------------------------------------------------------


def discover_provider(responses):
    transport = FakeTransport(responses)
    provider = OpenGTMProvider(
        ProviderConfig(
            name="opengtm",
            base_url="http://opengtm:3000",
            # Route is configuration, not assumption: it must be verified upstream first.
            paths={"discover": "/api/v1/discover"},
        ),
        transport=transport,
        sleep=lambda _: None,
    )
    return provider, transport


def discover_payload(**over):
    directive = {
        "segments": ["webflow_studio"],
        "sources": ["webflow_experts"],
        "limit": 10,
        "exclude_domains": [],
    }
    directive.update(over)
    return {"directive": directive}


def test_discovery_normalizes_candidates_and_domains():
    provider, _ = discover_provider(
        [
            TransportResponse(
                200,
                '{"candidates": [{"name": "Acme Studio", "website": "https://www.Acme.com/", '
                '"source_url": "https://webflow.com/experts/acme", "source_name": "webflow_experts"}]}',
            )
        ]
    )
    result = provider.execute(req("discover", discover_payload()))
    candidate = result.data["candidates"][0]
    assert candidate["domain"] == "acme.com"
    assert candidate["normalized_name"] == "acmestudio"
    assert candidate["status"] == "pending_review"


def test_discovery_rejects_unapproved_sources():
    provider, transport = discover_provider([])
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("discover", discover_payload(sources=["scraped_email_list"])))
    assert exc.value.code == "schema_violation"
    assert transport.calls == []


def test_discovery_skips_candidates_without_domain_but_returns_usable_ones():
    provider, _ = discover_provider(
        [
            TransportResponse(
                200,
                '{"candidates": [{"name": "No Domain", "source_url": "https://clutch.co/x"}, '
                '{"name": "Good", "domain": "good.com", "source_url": "https://clutch.co/good"}]}',
            )
        ]
    )
    result = provider.execute(req("discover", discover_payload(sources=["clutch"])))
    assert result.status == "partial"
    assert [c["name"] for c in result.data["candidates"]] == ["Good"]


def test_discovery_without_a_verified_route_fails_closed():
    """No default endpoint: an unverified discovery route is not guessed."""
    with pytest.raises(ProviderError) as exc:
        OpenGTMProvider(ProviderConfig(name="opengtm", base_url="http://opengtm:3000"))
    assert exc.value.code == "not_configured"


# --- provider specifics verified against upstream docs --------------------


def test_crawl4ai_wall_clock_504_is_a_timeout_not_a_generic_failure():
    transport = FakeTransport([TransportResponse(504, '{"detail": "wall clock exceeded"}')])
    provider = Crawl4AIProvider(
        ProviderConfig(name="crawl4ai", base_url="http://crawl4ai:11235", max_retries=0),
        transport=transport,
        sleep=lambda _: None,
    )
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("crawl", crawl_payload()))
    assert exc.value.code == "timeout"
    assert exc.value.retryable is True


def test_research_request_carries_the_fields_the_server_requires():
    provider, transport = research_provider(
        [TransportResponse(200, '{"report": "ok", "sources": ["https://a.com"]}')]
    )
    provider.execute(req("research", research_payload()))
    body = transport.calls[0]["body"]
    assert body["repo_name"] == ""
    assert body["branch_name"] == ""
    assert body["tone"] == "objective"


def test_research_rejects_a_tone_outside_the_provider_enum():
    provider, transport = research_provider([])
    with pytest.raises(ProviderError) as exc:
        provider.execute(req("research", research_payload(tone="sarcastic")))
    assert exc.value.code == "schema_violation"
    assert transport.calls == []


def test_token_provider_supplies_the_bearer_token():
    """OpenGTM uses an OAuth2 password grant, so the token is fetched, not configured."""
    variants = iter(["token-1", "token-2"])
    transport = FakeTransport(
        [
            TransportResponse(200, '{"candidates": [{"name": "A", "domain": "a.com", "source_url": "https://clutch.co/a"}]}'),
            TransportResponse(200, '{"candidates": [{"name": "B", "domain": "b.com", "source_url": "https://clutch.co/b"}]}'),
        ]
    )
    provider = OpenGTMProvider(
        ProviderConfig(
            name="opengtm",
            base_url="http://opengtm:3000",
            paths={"discover": "/api/v1/discover"},
            token_provider=lambda: next(variants),
        ),
        transport=transport,
        sleep=lambda _: None,
    )
    provider.execute(req("discover", discover_payload()))
    provider.execute(req("discover", discover_payload()))
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer token-1"
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer token-2"


# --- result contract ------------------------------------------------------


def test_partial_result_must_explain_itself():
    with pytest.raises(ValueError):
        ProviderResult(status="partial", data={}, provider=None)  # type: ignore[arg-type]


def test_evidence_requires_http_url():
    with pytest.raises(ValueError):
        Evidence(url="ftp://example.com", excerpt="x", provider="p", provider_version="1")


def test_request_requires_idempotency_key():
    with pytest.raises(ValueError):
        ProviderRequest(capability="crawl", payload={}, idempotency_key="")


def test_base_provider_interface_is_abstract():
    class Incomplete(BaseProvider):
        name = "incomplete"

    with pytest.raises(NotImplementedError):
        Incomplete(ProviderConfig(name="incomplete")).execute(req("crawl", {"urls": ["https://a.com"]}))
