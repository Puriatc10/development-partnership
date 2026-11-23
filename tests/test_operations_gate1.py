"""Human Gate 1 contract: review transitions, registration, idempotency, attribution.

The fake client models the constraints that actually decide these outcomes in PostgreSQL —
the review pair, ``companies.primary_domain`` uniqueness, ``company_domains.domain``
uniqueness — so a test cannot pass by ignoring a rule the database would enforce.

What these tests do NOT prove: live RLS, real PostgREST filter semantics, or that the
operator JWT carries the claims the policies read. Those are verified separately against
Development and are called out in the Gate 1 report.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from discovery.identity import normalize_name
from operations import candidates as gate1
from operations.candidates import Gate1Error
from operations.registration import register_candidate
from operations.session import OperatorSession, OperatorSessionError, open_session

OPERATOR_ID = "e1cd1ff2-0000-4000-8000-00000000aaaa"
CANDIDATE_ID = "c0000000-0000-4000-8000-000000000001"


class FakeClient:
    """In-memory PostgREST with the constraints Gate 1 depends on."""

    def __init__(self, *, candidates: list[dict] | None = None, companies: list[dict] | None = None):
        self.candidates = candidates if candidates is not None else []
        self.companies = companies if companies is not None else []
        self.domains: list[dict] = [
            {"company_id": c["id"], "domain": c["primary_domain"], "is_primary": True}
            for c in self.companies
        ]
        self.evidence: list[dict] = []
        self.calls: list[tuple[str, str, Any]] = []
        self._seq = 0

    def _id(self) -> str:
        self._seq += 1
        return f"id-{self._seq:04d}"

    def _match(self, rows: list[dict], params: Mapping[str, Any] | None) -> list[dict]:
        out = list(rows)
        for key, value in (params or {}).items():
            if key in {"select", "order", "limit"}:
                continue
            if not isinstance(value, str) or not value.startswith("eq."):
                continue
            expected = value[3:]
            out = [row for row in out if str(row.get(key)) == expected]
        if params and params.get("limit"):
            out = out[: int(params["limit"])]
        return out

    def get(self, table: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        self.calls.append(("GET", table, dict(params or {})))
        if table == "discovery_candidates":
            return self._match(self.candidates, params)
        if table == "discovery_candidate_evidence":
            return self._match(self.evidence, params)
        if table == "companies":
            return self._match(self.companies, params)
        if table == "company_domains":
            return self._match(self.domains, params)
        raise AssertionError(f"unexpected GET {table}")

    def post(self, table: str, data: Any) -> list[dict]:
        self.calls.append(("POST", table, data))
        if table != "companies":
            raise AssertionError(f"unexpected POST {table}")
        row = dict(data)
        if any(c["primary_domain"] == row["primary_domain"] for c in self.companies):
            raise RuntimeError('duplicate key value violates unique constraint "companies_primary_domain_key"')
        if any(d["domain"] == row["primary_domain"] for d in self.domains):
            raise RuntimeError("Duplicate domain: already registered to another company")
        row.setdefault("id", self._id())
        row.setdefault("lifecycle_stage", "identified")
        row.setdefault("qualification_tier", None)
        self.companies.append(row)
        self.domains.append(
            {"company_id": row["id"], "domain": row["primary_domain"], "is_primary": True}
        )
        return [row]

    def patch(self, table: str, data: Mapping[str, Any], params: Mapping[str, Any] | None = None) -> list[dict]:
        self.calls.append(("PATCH", table, dict(data)))
        if table != "discovery_candidates":
            raise AssertionError(f"unexpected PATCH {table}")
        matched = self._match(self.candidates, params)
        merged: list[dict] = []
        for row in matched:
            row.update(dict(data))
            # chk_discovery_candidate_review_pair
            if row.get("status") != "pending_review" and not (
                row.get("reviewed_by_human_id") and row.get("reviewed_at")
            ):
                raise RuntimeError("new row violates check constraint chk_discovery_candidate_review_pair")
            merged.append(row)
        return merged


def candidate(**overrides: Any) -> dict:
    row = {
        "id": CANDIDATE_ID,
        "name": "Alpha Studio",
        "normalized_name": normalize_name("Alpha Studio"),
        "domain": "alpha.example.com",
        "source_name": "manual_fixture",
        "source_url": "https://alpha.example.com",
        "discovered_at": "2026-11-20T10:00:00+00:00",
        "signals": {"segment": "digital_agency"},
        "status": "pending_review",
        "batch_key": "batch-1",
        "discovered_by_agent_identity": "hermes-discovery-v1",
        "reviewed_by_human_id": None,
        "reviewed_at": None,
        "review_notes": None,
        "converted_company_id": None,
    }
    row.update(overrides)
    return row


def test_approval_records_the_reviewer_and_timestamp():
    client = FakeClient(candidates=[candidate()])
    row = gate1.approve_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID, review_notes="fits ICP")
    assert row["status"] == "approved"
    assert row["reviewed_by_human_id"] == OPERATOR_ID
    assert row["reviewed_at"], "the review pair must be written with the status"
    assert row["review_notes"] == "fits ICP"


def test_approval_is_idempotent_and_keeps_the_original_reviewer():
    client = FakeClient(candidates=[candidate()])
    first = gate1.approve_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    patches_before = [c for c in client.calls if c[0] == "PATCH"]
    second = gate1.approve_candidate(client, CANDIDATE_ID, operator_id="someone-else")
    assert second["reviewed_at"] == first["reviewed_at"]
    assert second["reviewed_by_human_id"] == OPERATOR_ID, "a repeat must not rewrite the reviewer"
    assert len(patches_before) == 1, "a repeat must not issue a second write"


def test_a_rejected_candidate_cannot_be_approved():
    client = FakeClient(candidates=[candidate(status="rejected", reviewed_by_human_id=OPERATOR_ID, reviewed_at="2026-11-20T10:00:00+00:00")])
    with pytest.raises(Gate1Error) as exc:
        gate1.approve_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert "'rejected'" in str(exc.value) and "candidates show" in str(exc.value)


def test_rejection_requires_a_reason_that_is_recorded():
    client = FakeClient(candidates=[candidate()])
    with pytest.raises(Gate1Error) as exc:
        gate1.reject_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID, reason="   ")
    assert "must state a reason" in str(exc.value)
    row = gate1.reject_candidate(
        client, CANDIDATE_ID, operator_id=OPERATOR_ID, reason="no backend demand"
    )
    assert row["status"] == "rejected" and row["review_notes"] == "no backend demand"


def test_registration_requires_an_approved_candidate():
    client = FakeClient(candidates=[candidate()])
    with pytest.raises(Gate1Error) as exc:
        register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert "Gate 1 requires an explicit approval" in str(exc.value)
    assert client.companies == []


def test_registration_creates_the_company_with_provenance_and_links_the_candidate():
    client = FakeClient(candidates=[candidate(status="approved", reviewed_by_human_id=OPERATOR_ID, reviewed_at="2026-11-20T11:00:00+00:00")])
    result = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)

    assert result.created and not result.duplicate
    company = result.company
    assert company["primary_domain"] == "alpha.example.com"
    assert company["normalized_name"] == normalize_name("Alpha Studio")
    assert company["primary_segment"] == "digital_agency", "taken from the candidate's own signals"
    assert company["lifecycle_stage"] == "identified"
    details = company["discovery_details"]
    assert details["candidate_id"] == CANDIDATE_ID
    assert details["gate"] == "G1"
    assert details["reviewed_by"] == OPERATOR_ID
    assert details["discovered_by_agent_identity"] == "hermes-discovery-v1"
    assert result.candidate["converted_company_id"] == company["id"]
    assert client.domains[0]["domain"] == "alpha.example.com", "the primary domain row is created"


def test_a_second_registration_creates_nothing():
    client = FakeClient(candidates=[candidate(status="approved", reviewed_by_human_id=OPERATOR_ID, reviewed_at="2026-11-20T11:00:00+00:00")])
    first = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    posts_after_first = [c for c in client.calls if c[0] == "POST"]
    second = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert second.created is False
    assert second.company_id == first.company_id
    assert len([c for c in client.calls if c[0] == "POST"]) == len(posts_after_first)
    assert len(client.companies) == 1


def test_a_known_primary_domain_is_recorded_as_duplicate_not_recreated():
    existing = {
        "id": "company-existing",
        "name": "Alpha Studio",
        "primary_domain": "alpha.example.com",
        "lifecycle_stage": "identified",
        "qualification_tier": None,
    }
    client = FakeClient(
        candidates=[candidate(status="approved", reviewed_by_human_id=OPERATOR_ID, reviewed_at="2026-11-20T11:00:00+00:00")],
        companies=[existing],
    )
    result = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert result.duplicate and not result.created
    assert result.company_id == "company-existing"
    assert result.candidate["status"] == "duplicate"
    assert result.candidate["converted_company_id"] == "company-existing"
    assert len(client.companies) == 1


def test_a_domain_registered_as_someone_elses_secondary_is_also_a_duplicate():
    client = FakeClient(
        candidates=[candidate(status="approved", reviewed_by_human_id=OPERATOR_ID, reviewed_at="2026-11-20T11:00:00+00:00")],
        companies=[
            {
                "id": "company-other",
                "name": "Other Co",
                "primary_domain": "other.example.com",
                "lifecycle_stage": "identified",
                "qualification_tier": None,
            }
        ],
    )
    client.domains.append(
        {"company_id": "company-other", "domain": "alpha.example.com", "is_primary": False}
    )
    result = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert result.duplicate and not result.created
    assert result.company_id == "company-other"
    assert len(client.companies) == 1


def test_a_missing_segment_fails_closed_instead_of_guessing():
    client = FakeClient(
        candidates=[
            candidate(
                status="approved",
                reviewed_by_human_id=OPERATOR_ID,
                reviewed_at="2026-11-20T11:00:00+00:00",
                signals={},
            )
        ]
    )
    with pytest.raises(Gate1Error) as exc:
        register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert "--segment" in str(exc.value)
    assert client.companies == []

    with pytest.raises(Gate1Error) as exc:
        register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID, segment="not_a_segment")
    assert "unknown segment" in str(exc.value)


def test_an_explicit_segment_overrides_the_candidate_signal():
    client = FakeClient(candidates=[candidate(status="approved", reviewed_by_human_id=OPERATOR_ID, reviewed_at="2026-11-20T11:00:00+00:00")])
    result = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID, segment="webflow_studio")
    assert result.company["primary_segment"] == "webflow_studio"


def test_the_queue_lists_pending_candidates_only_and_can_be_asked_for_any_status():
    client = FakeClient(
        candidates=[
            candidate(),
            candidate(id="c2", domain="beta.example.com", status="approved"),
        ]
    )
    pending = gate1.list_candidates(client)
    assert [row["id"] for row in pending] == [CANDIDATE_ID]
    everything = gate1.list_candidates(client, status="any")
    assert len(everything) == 2
    with pytest.raises(Gate1Error):
        gate1.list_candidates(client, status="nonsense")


class _StubAuth:
    """Stands in for PostgresAuthClient: the session's contract is the claims it returns."""

    claims: dict = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def sign_in_with_password(self, email: str, password: str) -> dict:
        return {
            "access_token": "token",
            "user": {"id": "u-1", "email": email},
            "claims": self.claims,
        }

    def verify_token(self, token: str) -> dict:  # pragma: no cover - not used by the session
        return self.claims


def _env() -> dict[str, str]:
    return {
        "ERYNDEX_DB_URL": "postgresql://u:p@127.0.0.1:5433/eryndex",
        "ERYNDEX_ENV": "development",
        "ERYNDEX_JWT_SECRET": "test-secret",
        "OPERATOR_EMAIL": "operator@partnership.internal",
        "OPERATOR_PASSWORD": "secret",
    }


def test_an_agent_identity_cannot_open_an_operator_session(monkeypatch):
    _StubAuth.claims = {"sub": "u-1", "app_metadata": {"user_role": "agent", "subagent_type": "discovery"}}
    monkeypatch.setattr("operations.session.PostgresAuthClient", _StubAuth)
    with pytest.raises(OperatorSessionError) as exc:
        open_session(_env())
    assert "not a human operator" in str(exc.value)
    assert "agent" in str(exc.value)


def test_an_operator_session_reports_its_identity(monkeypatch):
    _StubAuth.claims = {"sub": "u-1", "app_metadata": {"user_role": "operator"}}
    monkeypatch.setattr("operations.session.PostgresAuthClient", _StubAuth)
    session = open_session(_env())
    assert isinstance(session, OperatorSession)
    assert session.user_id == "u-1" and session.role == "operator"


def test_a_missing_operator_configuration_names_the_variables_only():
    env = {k: v for k, v in _env().items() if k != "OPERATOR_PASSWORD"}
    with pytest.raises(OperatorSessionError) as exc:
        open_session(env)
    assert "OPERATOR_PASSWORD" in str(exc.value)
    assert "secret" not in str(exc.value)


# --- regressions from the independent adversarial review -------------------


def test_link_company_refuses_a_candidate_that_was_never_approved():
    """A company may not be linked to an unreviewed candidate.

    Without this, a pending row could carry converted_company_id while still shadowing its
    domain, and register_candidate would later report it as "already registered".
    """
    client = FakeClient(candidates=[candidate()])
    with pytest.raises(Gate1Error) as exc:
        gate1.link_company(client, CANDIDATE_ID, company_id="company-1")
    assert "only an approved candidate" in str(exc.value)
    assert client.candidates[0]["converted_company_id"] is None


def test_registration_refuses_a_link_on_an_unreviewed_candidate():
    client = FakeClient(
        candidates=[candidate(converted_company_id="company-1")],
        companies=[{"id": "company-1", "name": "Alpha", "primary_domain": "alpha.example.com",
                    "lifecycle_stage": "identified", "qualification_tier": None}],
    )
    with pytest.raises(Gate1Error) as exc:
        register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert "inconsistent row" in str(exc.value)


def test_registration_refuses_when_the_approval_names_no_reviewer():
    client = FakeClient(
        candidates=[candidate(status="approved", reviewed_by_human_id=None, reviewed_at=None)]
    )
    with pytest.raises(Gate1Error) as exc:
        register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert "reviewer" in str(exc.value)
    assert client.companies == []


def test_a_name_match_on_a_different_domain_is_a_conflict_not_a_duplicate():
    client = FakeClient(
        candidates=[candidate(status="approved", reviewed_by_human_id=OPERATOR_ID,
                              reviewed_at="2026-11-20T11:00:00+00:00")],
        companies=[
            {
                "id": "company-named",
                "name": "Alpha Studio",
                "normalized_name": normalize_name("Alpha Studio"),
                "primary_domain": "alpha-studio.example.com",
                "lifecycle_stage": "identified",
                "qualification_tier": None,
            }
        ],
    )
    result = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID)
    assert result.created is False and result.duplicate is False
    assert result.candidate["status"] == "conflict"
    assert "name matches company company-named" in result.candidate["review_notes"]
    assert len(client.companies) == 1


def test_provenance_records_the_reviewer_not_the_registering_operator():
    reviewer = "reviewer-human-2222"
    client = FakeClient(
        candidates=[candidate(status="approved", reviewed_by_human_id=reviewer,
                              reviewed_at="2026-11-20T11:00:00+00:00")]
    )
    result = register_candidate(client, CANDIDATE_ID, operator_id=OPERATOR_ID, segment="digital_agency")
    assert result.company["discovery_details"]["reviewed_by"] == reviewer
    assert result.company["discovery_details"]["registration_operator"] == OPERATOR_ID
