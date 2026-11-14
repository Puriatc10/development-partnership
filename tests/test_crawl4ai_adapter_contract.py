"""Crawl4AI adapter contract tests (Directive 03.5).

Deterministic: a fake transport feeds fixture payloads shaped exactly as
``crawl4ai==0.9.5`` serializes them (``CrawlResult.model_dump()`` emits
``markdown`` as a nested ``MarkdownGenerationResult``). Nothing here contacts a
server, so these tests prove the adapter's contract, never the vendor's
behaviour — ``endpoint_verified`` stays ``False``.

The fixtures are hand-built from the upstream models, so a change in the upstream
shape shows up here as a failed expectation rather than a silently wrong excerpt.
"""

from __future__ import annotations

import hashlib

import pytest

from contracts.errors import ProviderError
from contracts.provider import ProviderRequest
from integrations.base import (
    HttpTransport,
    ProviderConfig,
    TransportResponse,
    TransportTimeout,
    TransportUnavailable,
)
from integrations.crawl4ai import Crawl4AIProvider
from integrations.crawl4ai.adapter import (
    MARKDOWN_TEXT_KEYS,
    RUN_CONFIG_FIELDS,
    normalize_markdown,
)

MD = {
    "raw_markdown": "# Example Domain\n\nThis domain is for use in illustrative examples.",
    "markdown_with_citations": "# Example Domain [citation]",
    "references_markdown": "[1]: https://example.com",
    "fit_markdown": "Example Domain. Illustrative examples.",
    "fit_html": "<h1>Example Domain</h1>",
}


def page(**over):
    """A v0.9.5 CrawlResult after model_dump(): markdown is a nested object."""
    item = {
        "url": "https://example.com/",
        "success": True,
        "markdown": dict(MD),
        "metadata": {"title": "Example Domain"},
        "links": {"internal": [{"href": "https://example.com/a"}], "external": [{"href": "https://iana.org"}]},
        "status_code": 200,
    }
    item.update(over)
    return item


class FakeTransport(HttpTransport):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, json_body=None, headers=None, timeout=60.0):
        self.calls.append({"method": method, "url": url, "body": json_body, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider_with(*responses):
    transport = FakeTransport(responses)
    provider = Crawl4AIProvider(
        ProviderConfig(name="crawl4ai", base_url="http://crawl4ai:11235", max_retries=2),
        transport=transport,
        sleep=lambda _: None,
    )
    return provider, transport


def request(payload, timeout=5.0):
    return ProviderRequest(capability="crawl", payload=payload, idempotency_key="k1", timeout_s=timeout, trace_id="t1")


def ok(items):
    return TransportResponse(200, __import__("json").dumps({"success": True, "results": items}))


def body_of(transport):
    assert transport.calls, "no request was made"
    return transport.calls[0]["body"]


# --- 1/2/3: markdown normalization ---------------------------------------


def test_markdown_returned_as_nested_dict_becomes_real_text():
    provider, transport = provider_with(ok([page()]))
    result = provider.execute(request({"urls": ["https://example.com/"]}))

    text = result.data["pages"][0]["text"]
    assert text.startswith("# Example Domain")
    assert "raw_markdown" not in text and "{" not in text
    # the excerpt must be the text, never a dict repr
    assert result.evidence[0].excerpt == MD["raw_markdown"]


def test_markdown_returned_as_plain_string_is_used_verbatim():
    provider, _ = provider_with(ok([page(markdown="plain markdown body")]))
    result = provider.execute(request({"urls": ["https://example.com/"]}))
    assert result.data["pages"][0]["text"] == "plain markdown body"


def test_missing_markdown_falls_back_then_fails_closed():
    # a dict with only the readability variant still yields text
    provider, _ = provider_with(ok([page(markdown={"fit_markdown": "fit text only"})]))
    assert provider.execute(request({"urls": ["https://example.com/"]})).data["pages"][0]["text"] == "fit text only"

    # a dict with no string body is not usable text -> fail closed.
    # An invalid_response is retried exactly once by the base contract, hence two payloads.
    unusable = ok([page(markdown={"references_markdown": "", "fit_html": "<p/>"})])
    provider, transport = provider_with(unusable, unusable)
    with pytest.raises(ProviderError) as exc:
        provider.execute(request({"urls": ["https://example.com/"]}))
    assert exc.value.code == "invalid_response"
    assert len(transport.calls) == 2


def test_normalize_markdown_never_stringifies_a_mapping():
    assert normalize_markdown({"unknown": "x"}) == ""
    assert normalize_markdown(None) == ""
    assert normalize_markdown({"markdown": {"raw_markdown": "nested once"}}) == "nested once"
    assert normalize_markdown({"raw_markdown": "   "}) == ""


# --- 4: cache configuration ---------------------------------------------


@pytest.mark.parametrize("bypass,expected", [(True, "bypass"), (False, "enabled")])
def test_cache_mode_is_sent_lowercase_in_a_typed_envelope(bypass, expected):
    provider, transport = provider_with(ok([page()]))
    provider.execute(request({"urls": ["https://example.com/"], "bypass_cache": bypass}))

    cfg = body_of(transport)["crawler_config"]
    assert cfg["type"] == "CrawlerRunConfig"
    assert cfg["params"]["cache_mode"] == expected
    assert cfg["params"]["cache_mode"] in {"enabled", "disabled", "read_only", "write_only", "bypass"}


# --- 5: extraction reaches the wire as supported fields ------------------


def test_extraction_maps_to_allowlisted_crawler_fields_and_never_invents_a_key():
    provider, transport = provider_with(ok([page()]))
    provider.execute(request({"urls": ["https://example.com/"], "extract": ["text", "links", "metadata"]}))

    body = body_of(transport)
    assert "extract" not in body, "the server has no top-level extract field; inventing one is a contract bug"
    params = body["crawler_config"]["params"]
    assert params["exclude_external_links"] is False
    assert params["exclude_social_media_links"] is False
    assert "only_text" not in params, "links were requested, so text-only extraction would drop them"
    assert set(params) <= RUN_CONFIG_FIELDS


def test_text_only_extraction_sets_only_text_and_returns_links_as_none():
    provider, transport = provider_with(ok([page()]))
    result = provider.execute(request({"urls": ["https://example.com/"], "extract": ["text"]}))
    assert body_of(transport)["crawler_config"]["params"]["only_text"] is True
    assert result.data["pages"][0]["links"] is None


# --- 7: unsupported configuration rejected explicitly -------------------


@pytest.mark.parametrize("bad", [["html"], ["llm"], ["text", "screenshot"], []])
def test_unsupported_extract_modes_are_rejected_not_ignored(bad):
    provider, transport = provider_with(ok([page()]))
    with pytest.raises(ProviderError) as exc:
        provider.execute(request({"urls": ["https://example.com/"], "extract": bad}))
    assert exc.value.code == "schema_violation"
    assert transport.calls == [], "an invalid request must not reach the wire"


# --- 6: browser configuration forwarding --------------------------------


def test_browser_config_is_forwarded_in_a_typed_envelope():
    provider, transport = provider_with(ok([page()]))
    browser = {"viewport_width": 1280, "viewport_height": 720, "headless": True, "user_agent": "eryndex/1"}
    provider.execute(request({"urls": ["https://example.com/"], "browser_config": browser}))

    sent = body_of(transport)["browser_config"]
    assert sent == {"type": "BrowserConfig", "params": browser}


def test_browser_config_is_omitted_when_not_supplied_so_server_defaults_apply():
    provider, transport = provider_with(ok([page()]))
    provider.execute(request({"urls": ["https://example.com/"]}))
    assert "browser_config" not in body_of(transport)


@pytest.mark.parametrize(
    "browser",
    [
        {"viewport_width": 800, "enable_stealth": True, "proxy": "http://evil:8080"},  # unknown key
        {"viewport_width": "800"},  # wrong type
        {"headless": 1},  # bool field given an int
    ],
)
def test_invalid_browser_config_is_rejected_before_the_wire(browser):
    provider, transport = provider_with(ok([page()]))
    with pytest.raises(ProviderError) as exc:
        provider.execute(request({"urls": ["https://example.com/"], "browser_config": browser}))
    assert exc.value.code == "schema_violation"
    assert transport.calls == []


# --- 8: provenance and content hash -------------------------------------


def test_content_hash_is_computed_from_normalized_text_not_a_dict_repr():
    provider, _ = provider_with(ok([page()]))
    result = provider.execute(request({"urls": ["https://example.com/"]}))

    ev = result.evidence[0]
    expected = hashlib.sha256(MD["raw_markdown"].encode("utf-8")).hexdigest()
    assert ev.content_hash == expected
    assert ev.content_hash != hashlib.sha256(str(MD).encode("utf-8")).hexdigest()
    assert ev.provider == "crawl4ai" and ev.provider_version == "0.9.5"
    assert ev.url == "https://example.com/" and ev.title == "Example Domain"
    assert ev.retrieved_at, "retrieval timestamp must be recorded"
    # no provider-issued id exists upstream, so none is invented
    assert result.data["provider_result_id"] is None


def test_every_markdown_field_name_is_known_to_the_normalizer():
    # guards against an upstream rename silently producing empty excerpts
    assert set(MARKDOWN_TEXT_KEYS) == {
        "raw_markdown",
        "markdown_with_citations",
        "references_markdown",
        "fit_markdown",
    }


# --- 9/10: failure handling and taxonomy ---------------------------------


def test_no_results_fails_closed():
    empty = TransportResponse(200, '{"success": true, "results": []}')
    provider, transport = provider_with(empty, empty)  # invalid_response is retried once
    with pytest.raises(ProviderError) as exc:
        provider.execute(request({"urls": ["https://example.com/"]}))
    assert exc.value.code == "invalid_response"
    assert len(transport.calls) == 2


def test_partial_page_failure_is_reported_as_partial_not_as_success():
    provider, _ = provider_with(ok([page(), page(url="https://example.com/broken", success=False, markdown={})]))
    result = provider.execute(request({"urls": ["https://example.com/", "https://example.com/broken"]}))
    assert result.status == "partial"
    assert len(result.evidence) == 1
    assert any("no usable text" in w for w in result.warnings)


def test_client_errors_are_not_retried_and_keep_their_taxonomy():
    provider, transport = provider_with(TransportResponse(401, "unauthorized"))
    with pytest.raises(ProviderError) as exc:
        provider.execute(request({"urls": ["https://example.com/"]}))
    assert exc.value.code == "unauthorized"
    assert len(transport.calls) == 1


def test_timeout_and_unavailable_still_retry_then_surface():
    provider, transport = provider_with(
        TransportTimeout("wall clock"), TransportTimeout("wall clock"), TransportTimeout("wall clock")
    )
    with pytest.raises(ProviderError) as exc:
        provider.execute(request({"urls": ["https://example.com/"]}))
    assert exc.value.code == "timeout"
    assert len(transport.calls) == 3, "1 attempt + max_retries(2), unchanged by this correction"

    provider, transport = provider_with(
        TransportUnavailable("refused"), TransportUnavailable("refused"), TransportUnavailable("refused")
    )
    with pytest.raises(ProviderError) as exc:
        provider.execute(request({"urls": ["https://example.com/"]}))
    assert exc.value.code == "unavailable"
    assert len(transport.calls) == 3


def test_adapter_still_declares_itself_unverified():
    provider, _ = provider_with(ok([page()]))
    result = provider.execute(request({"urls": ["https://example.com/"]}))
    assert provider.endpoint_verified is False
    assert any("not yet verified" in w for w in result.warnings)
