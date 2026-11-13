"""Idempotency key stability (ADR-0004)."""

from __future__ import annotations

from orchestration.idempotency import (
    content_key,
    provider_request_key,
    research_idempotency_key,
    strip_volatile,
)

FINDINGS = [
    {
        "finding_type": "verified_fact",
        "category": "technology_stack",
        "statement": "Refokus is a Webflow Enterprise Partner.",
        "evidence_url": "https://webflow.com/partners/refokus",
    },
    {
        "finding_type": "hypothesis",
        "category": "pain_points",
        "statement": "Webflow-only delivery may cap backend scope.",
        "evidence_url": "",
    },
]


def test_key_is_stable_for_identical_input():
    a = research_idempotency_key("comp-1", 1, FINDINGS)
    b = research_idempotency_key("comp-1", 1, [dict(f) for f in FINDINGS])
    assert a == b


def test_key_is_order_insensitive():
    a = research_idempotency_key("comp-1", 1, FINDINGS)
    b = research_idempotency_key("comp-1", 1, list(reversed(FINDINGS)))
    assert a == b


def test_key_ignores_whitespace_and_url_case_differences():
    a = research_idempotency_key("comp-1", 1, FINDINGS)
    mutated = [
        {**FINDINGS[0], "statement": "Refokus  is a Webflow   Enterprise Partner.",
         "evidence_url": "https://WEBFLOW.com/partners/refokus/"},
        FINDINGS[1],
    ]
    assert research_idempotency_key("comp-1", 1, mutated) == a


def test_key_changes_when_content_changes():
    a = research_idempotency_key("comp-1", 1, FINDINGS)
    changed = [dict(FINDINGS[0], statement="Refokus is a Webflow partner."), FINDINGS[1]]
    assert research_idempotency_key("comp-1", 1, changed) != a


def test_key_changes_with_report_version_and_company():
    a = research_idempotency_key("comp-1", 1, FINDINGS)
    assert research_idempotency_key("comp-1", 2, FINDINGS) != a
    assert research_idempotency_key("comp-2", 1, FINDINGS) != a


def test_provider_key_ignores_run_metadata_but_not_payload():
    payload = {"urls": ["https://a.com"], "trace_id": "run-1", "started_at": "now"}
    other = {"urls": ["https://a.com"], "trace_id": "run-2", "started_at": "later"}
    assert provider_request_key("crawl", payload) == provider_request_key("crawl", other)
    assert strip_volatile(payload) == {"urls": ["https://a.com"]}
    assert provider_request_key("crawl", {"urls": ["https://b.com"]}) != provider_request_key("crawl", payload)


def test_content_key_is_canonical():
    assert content_key({"b": 1, "a": 2}) == content_key({"a": 2, "b": 1})
