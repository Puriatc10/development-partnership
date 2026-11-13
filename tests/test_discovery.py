"""Discovery identity, dedupe and service behaviour.

These are the rules that decide whether a candidate becomes a company or a
duplicate; a wrong answer here writes the wrong record into the CRM.
"""

from __future__ import annotations

import json

import pytest

from discovery.identity import (
    DOMAIN_CONFLICT,
    EXACT_DOMAIN,
    EXACT_NAME,
    NO_MATCH,
    normalize_domain,
    normalize_name,
    resolve_identity,
)
from discovery.ports import JsonlOutboxSink, StagingUnavailable, SupabaseCandidateSink
from discovery.service import DiscoveryRequest, DiscoveryRequestError, run_discovery


class ListSource:
    name = "fixture"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self, request):
        return list(self.rows)


class RecordingSink:
    def __init__(self):
        self.writes = []

    def write(self, request, candidates, evidence):
        self.writes.append({"candidates": list(candidates), "evidence": list(evidence)})
        return len(candidates)


def request(**over):
    base = {"segments": ["webflow_studio"], "sources": ["webflow_experts"], "limit": 10}
    base.update(over)
    return DiscoveryRequest(**base)


# --- normalization --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.Acme.com/about?x=1", "acme.com"),
        ("http://acme.com:8080/", "acme.com"),
        ("ACME.COM.", "acme.com"),
        ("https://user:pass@acme.com/x", "acme.com"),
        ("acme.com", "acme.com"),
    ],
)
def test_domain_normalization(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "acme", "not a domain", "localhost"])
def test_domains_that_cannot_be_deduped_are_rejected(raw):
    assert normalize_domain(raw) == ""


@pytest.mark.parametrize(
    "left,right",
    [
        ("Acme Studio, LLC", "Acme  Studio"),
        ("ACME Studio GmbH", "acme studio"),
        ("Acme Studio Inc.", "Acme Studio"),
    ],
)
def test_name_keys_collapse_legal_forms(left, right):
    assert normalize_name(left) == normalize_name(right)


def test_different_names_do_not_collapse():
    assert normalize_name("Acme Studio") != normalize_name("Acme Labs")


# --- existing-company resolution -----------------------------------------


EXISTING = [
    {"id": "c1", "name": "Acme Studio", "normalized_name": "acmestudio", "primary_domain": "acme.com"},
    {"id": "c2", "name": "Beta Labs", "normalized_name": "betalabs", "primary_domain": "beta.io"},
]


def test_exact_domain_match_wins():
    verdict, match = resolve_identity("https://www.acme.com", "somethingelse", EXISTING)
    assert verdict == EXACT_DOMAIN
    assert match["id"] == "c1"


def test_exact_name_match_is_a_duplicate_even_without_domain():
    verdict, match = resolve_identity("newdomain.com", "acmestudio", EXISTING)
    assert verdict == EXACT_NAME
    assert match["id"] == "c1"


def test_domain_belonging_to_another_company_is_a_conflict():
    verdict, match = resolve_identity("acme.com", "betalabs", EXISTING)
    assert verdict == DOMAIN_CONFLICT
    assert match is None


def test_unknown_company_matches_nothing():
    assert resolve_identity("gamma.dev", "gamma", EXISTING)[0] == NO_MATCH


# --- service --------------------------------------------------------------


def test_accepts_new_candidates_and_stages_only_those():
    source = ListSource(
        [
            {"name": "Gamma Dev", "domain": "gamma.dev", "source_url": "https://webflow.com/experts/gamma"},
            {"name": "Delta Co", "domain": "https://delta.co/", "source_url": "https://webflow.com/experts/delta"},
        ]
    )
    sink = RecordingSink()
    outcome = run_discovery(request(), source, sink, existing_companies=EXISTING)
    assert [c["domain"] for c in outcome.accepted] == ["gamma.dev", "delta.co"]
    assert outcome.staged == 2
    assert sink.writes[0]["candidates"] == list(outcome.accepted)
    assert outcome.stats["existing_matches"] == 0


def test_duplicate_within_batch_is_counted_not_staged():
    source = ListSource(
        [
            {"name": "Gamma Dev", "domain": "gamma.dev"},
            {"name": "Gamma Development LLC", "domain": "www.gamma.dev/about"},
        ]
    )
    sink = RecordingSink()
    outcome = run_discovery(request(), source, sink)
    assert outcome.staged == 1
    assert len(outcome.duplicates_in_batch) == 1
    assert outcome.duplicates_in_batch[0]["reason"] == "duplicate_in_batch"


def test_existing_company_is_matched_not_staged():
    source = ListSource([{"name": "Acme Studio", "domain": "acme.com"}])
    outcome = run_discovery(request(), ListSource([]), RecordingSink())  # sanity: empty run
    outcome = run_discovery(request(), source, RecordingSink(), existing_companies=EXISTING)
    assert outcome.staged == 0
    assert outcome.existing_matches[0]["matched_company_id"] == "c1"


def test_candidate_without_a_domain_is_rejected_not_invented():
    source = ListSource([{"name": "No Site", "source_url": "https://clutch.co/no-site"}])
    outcome = run_discovery(request(), source, RecordingSink())
    assert outcome.accepted == ()
    assert outcome.rejected[0]["reason"].startswith("unidentifiable")


def test_excluded_domains_are_dropped():
    source = ListSource([{"name": "Acme", "domain": "acme.com"}, {"name": "Gamma", "domain": "gamma.dev"}])
    outcome = run_discovery(request(exclude_domains=["https://www.acme.com"]), source, RecordingSink())
    assert [c["domain"] for c in outcome.accepted] == ["gamma.dev"]
    assert any(r["reason"] == "excluded by directive" for r in outcome.rejected)


def test_conflicting_identity_goes_to_review_not_to_staging():
    source = ListSource([{"name": "Beta Labs", "domain": "acme.com"}])
    outcome = run_discovery(request(), source, RecordingSink(), existing_companies=EXISTING)
    assert outcome.staged == 0
    assert outcome.conflicts[0]["reason"].startswith("domain owned by one company")


def test_limit_is_respected():
    rows = [{"name": f"Studio {i}", "domain": f"studio{i}.com"} for i in range(10)]
    outcome = run_discovery(request(limit=3), ListSource(rows), RecordingSink())
    assert len(outcome.accepted) == 3


def test_outcome_is_idempotent_for_the_same_candidate_set():
    rows = [{"name": "Gamma Dev", "domain": "gamma.dev"}, {"name": "Delta Co", "domain": "delta.co"}]
    first = run_discovery(request(campaign_id="camp-1"), ListSource(rows), RecordingSink())
    second = run_discovery(request(campaign_id="camp-1"), ListSource(list(reversed(rows))), RecordingSink())
    assert first.idempotency_key == second.idempotency_key


def test_request_rejects_unapproved_sources_and_bad_limits():
    with pytest.raises(DiscoveryRequestError):
        request(sources=["scraped_email_list"])
    with pytest.raises(DiscoveryRequestError):
        request(limit=0)
    with pytest.raises(DiscoveryRequestError):
        request(segments=["crypto_startup"])


# --- sinks ----------------------------------------------------------------


def test_jsonl_outbox_is_labelled_and_not_a_crm(tmp_path):
    path = tmp_path / "outbox.jsonl"
    source = ListSource(
        [
            {
                "name": "Gamma Dev",
                "domain": "gamma.dev",
                "evidence": [{"url": "https://gamma.dev/about", "excerpt": "We build Webflow sites."}],
            }
        ]
    )
    outcome = run_discovery(request(), source, JsonlOutboxSink(path))
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert line["sink"] == "jsonl_outbox"
    assert line["candidate_evidence"][0]["url"] == "https://gamma.dev/about"
    assert outcome.staged == 1


def test_missing_staging_table_produces_an_actionable_error():
    class MissingTableClient:
        def post(self, table, rows):
            raise RuntimeError('PGRST205: Could not find the table public.discovery_candidates')

    sink = SupabaseCandidateSink(MissingTableClient())
    with pytest.raises(StagingUnavailable) as exc:
        sink.write(request(), [{"name": "A", "domain": "a.com"}], [])
    assert "discovery_candidates" in str(exc.value)
