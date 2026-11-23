"""Human Gate 2: persistence as the qualifier, decision as the human, attribution by the DB.

The fake client models the rules the database actually enforces on ``lead_qualifications``,
so a test cannot pass by ignoring one:

- ``chk_lead_qual_review_human_gate``: the reviewer pair is present only on ``approved``;
- ``fn_enforce_lead_qualification_rules``: an approval must carry ``auth.uid()`` as the
  reviewer, a rejection needs notes/reasons, and the trigger stamps ``reviewed_at``;
- ``fn_audit_log_capture``: every write lands an immutable ``audit_logs`` row naming the
  actor — the only reviewer record a rejection can have;
- ``uq_lead_qualifications_approved``: a second approved scorecard for one company is
  refused with 23505.

The insufficient-evidence test runs the real loader, engine and repository against a fake
database: the refusal has to come from the production code path, before any write.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from integrations.postgres.client import DatabaseError
from operations import cli
from operations import qualifications as gate2
from operations.qualifications import (
    QualificationGateError,
    QualificationNotMeasured,
    QualifierIdentityError,
    QualifierWriteDenied,
)

OPERATOR_ID = "e1cd1ff2-0000-4000-8000-00000000aaaa"
COMPANY_ID = "f87394df-8b53-41ee-ab72-e99ff1266b16"
QUALIFICATION_ID = "66666666-6666-6666-6666-666666666661"
QUALIFIER_EMAIL = "qualifier@agents.internal"
AGENT_ID = "hermes-qualifier-v1"

DIMENSION_KEYS = (
    "customer_flow",
    "development_demand",
    "repeatability_orders",
    "technical_fit",
    "business_size",
    "decision_maker_accessibility",
    "commercial_fit",
)

EVIDENCE = {
    key: {"score": 0, "max": 10, "evidence": f"finding for {key}", "finding_ids": ["f-1"]}
    for key in DIMENSION_KEYS
}

ENV = {
    "ERYNDEX_DB_URL": "postgresql://u:***@127.0.0.1:5433/eryndex",
    "ERYNDEX_ENV": "development",
    "ERYNDEX_JWT_SECRET": "test-secret",
    "QUALIFIER_EMAIL": QUALIFIER_EMAIL,
    "QUALIFIER_PASSWORD": "secret",
}


def qualification(**over: Any) -> dict:
    row = {
        "id": QUALIFICATION_ID,
        "company_id": COMPANY_ID,
        "evaluator_type": "agent",
        "evaluator_identity": AGENT_ID,
        "overall_score": 62.0,
        "customer_flow_score": 15.0,
        "development_demand_score": 10.0,
        "repeatability_orders_score": 9.0,
        "technical_fit_score": 10.0,
        "business_size_score": 6.0,
        "decision_maker_accessibility_score": 6.0,
        "commercial_fit_score": 6.0,
        "dimension_evidence": {k: dict(v) for k, v in EVIDENCE.items()},
        "qualification_tier": "tier_2_prospective",
        "disqualification_reasons": [],
        "disqualification_notes": None,
        "recommended_next_action": "Approach with wholesale backend capacity.",
        "review_status": "draft",
        "reviewed_by_human_id": None,
        "reviewed_at": None,
        "created_at": "2026-11-23T10:00:00+00:00",
        "updated_at": "2026-11-23T10:00:00+00:00",
    }
    row.update(over)
    return row


class FakeClient:
    """In-memory lead_qualifications + audit_logs with the trigger rules applied."""

    def __init__(
        self,
        *,
        qualifications: list[dict] | None = None,
        audit: list[dict] | None = None,
        actor_id: str = OPERATOR_ID,
        misattribute: bool = False,
        patch_error: Exception | None = None,
        tables: Mapping[str, list[dict]] | None = None,
    ) -> None:
        self.qualifications = [dict(row) for row in (qualifications or [])]
        self.audit = [dict(row) for row in (audit or [])]
        self.actor_id = actor_id
        self.misattribute = misattribute
        self.patch_error = patch_error
        self.tables = {name: [dict(r) for r in rows] for name, rows in (tables or {}).items()}
        self.calls: list[tuple] = []
        self._seq = len(self.audit)

    # -- helpers ---------------------------------------------------------
    def close(self) -> None:  # pragma: no cover - presence mirrors PostgresClient
        self.calls.append(("CLOSE",))

    def _match(self, rows: list[dict], params: Mapping[str, Any] | None) -> list[dict]:
        out = list(rows)
        params = dict(params or {})
        for key, value in params.items():
            if key in {"select", "order", "limit", "offset"}:
                continue
            if not isinstance(value, str) or not value.startswith("eq."):
                raise AssertionError(f"fake does not model filter {key}={value!r}")
            expected = value[3:]
            out = [row for row in out if str(row.get(key)) == expected]
        order = params.get("order")
        if order:
            for item in reversed(str(order).split(",")):
                column, _, direction = item.strip().partition(".")
                out.sort(
                    key=lambda row: str(row.get(column) or ""),
                    reverse=direction.lower() == "desc",
                )
        offset = int(params.get("offset") or 0)
        if offset:
            out = out[offset:]
        if params.get("limit") is not None:
            out = out[: int(params["limit"])]
        return [dict(row) for row in out]

    # -- reads -----------------------------------------------------------
    def get(self, table: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        self.calls.append(("GET", table, dict(params or {})))
        if table == gate2.TABLE:
            return self._match(self.qualifications, params)
        if table == gate2.AUDIT_TABLE:
            return self._match(self.audit, params)
        if table in self.tables:
            return self._match(self.tables[table], params)
        raise AssertionError(f"unexpected GET {table}")

    # -- writes ----------------------------------------------------------
    def post(self, table: str, data: Any, prefer: str = "") -> list[dict]:
        self.calls.append(("POST", table, dict(data)))
        if table != gate2.TABLE:
            raise AssertionError(f"unexpected POST {table}")
        row = dict(data)
        row.setdefault("id", "lq-new")
        row.setdefault("created_at", "2026-11-23T11:00:00+00:00")
        self.qualifications.append(row)
        self._audit("INSERT", row, None)
        return [dict(row)]

    def patch(
        self,
        table: str,
        data: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
        prefer: str = "",
    ) -> list[dict]:
        self.calls.append(("PATCH", table, dict(data), dict(params or {})))
        if table != gate2.TABLE:
            raise AssertionError(f"unexpected PATCH {table}")
        if self.patch_error is not None:
            raise self.patch_error
        updated: list[dict] = []
        for row in self._match(self.qualifications, params):
            target = next(r for r in self.qualifications if r["id"] == row["id"])
            before = dict(target)
            target.update(dict(data))

            # fn_enforce_lead_qualification_rules
            if target["review_status"] == "approved":
                if str(target.get("reviewed_by_human_id") or "") != self.actor_id:
                    raise DatabaseError(
                        "reviewed_by_human_id must match the authenticated operator", code="P0001"
                    )
                target["reviewed_at"] = "2026-11-23T12:00:00+00:00"
                if self.misattribute:
                    target["reviewed_by_human_id"] = "someone-else"
                action = "APPROVAL_GRANTED"
            else:
                target["reviewed_by_human_id"] = None
                target["reviewed_at"] = None
                if target["review_status"] == "rejected" and not (
                    (target.get("disqualification_notes") or "").strip()
                    or target.get("disqualification_reasons")
                ):
                    raise DatabaseError(
                        "Rejection of lead qualification requires documented "
                        "disqualification reasons or notes.",
                        code="P0001",
                    )
                action = "UPDATE"

            target["updated_at"] = "2026-11-23T12:00:00+00:00"
            self._audit(action, target, before)
            updated.append(dict(target))
        return updated

    def _audit(self, action: str, new: Mapping[str, Any], old: Mapping[str, Any] | None) -> None:
        self._seq += 1
        self.audit.append(
            {
                "id": self._seq,
                "table_name": gate2.TABLE,
                "record_id": new["id"],
                "action": action,
                "actor_type": "human",
                "actor_identity": self.actor_id,
                "old_values": dict(old) if old else None,
                "new_values": dict(new),
                "created_at": "2026-11-23T12:00:00+00:00",
            }
        )


class _StubAuth:
    """Stands in for PostgresAuthClient; the contract is the claims it returns."""

    claims: ClassVar[dict] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def sign_in_with_password(self, email: str, password: str) -> dict:
        return {
            "access_token": "token",
            "user": {"id": "u-1", "email": email},
            "claims": self.claims,
        }


def qualifier_claims(**app_overrides: Any) -> dict:
    app = {"user_role": "agent", "subagent_type": "qualifier", "agent_id": AGENT_ID}
    app.update(app_overrides)
    return {"sub": "u-qualifier", "role": "authenticated", "app_metadata": app}


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "pending_human_review"])
def test_approval_is_allowed_from_the_two_undecided_states(status: str):
    client = FakeClient(qualifications=[qualification(review_status=status)])
    row = gate2.approve(client, QUALIFICATION_ID, operator_id=OPERATOR_ID, note="fits ICP")
    assert row["review_status"] == "approved"
    assert row["reviewed_by_human_id"] == OPERATOR_ID
    assert row["reviewed_at"], "the reviewer pair must be written with the status"
    assert "fits ICP" in row["disqualification_notes"]


def test_an_approved_scorecard_cannot_be_approved_again_and_keeps_its_reviewer():
    client = FakeClient(qualifications=[qualification()])
    first = gate2.approve(client, QUALIFICATION_ID, operator_id=OPERATOR_ID)
    patches = [c for c in client.calls if c[0] == "PATCH"]
    second = gate2.approve(client, QUALIFICATION_ID, operator_id="someone-else")
    assert second["reviewed_at"] == first["reviewed_at"]
    assert second["reviewed_by_human_id"] == OPERATOR_ID, "a repeat must not rewrite the reviewer"
    assert len(patches) == 1, "a repeat must not issue a second write"


def test_an_approved_row_without_a_reviewer_is_refused():
    client = FakeClient(qualifications=[qualification(review_status="approved")])
    with pytest.raises(QualificationGateError) as exc:
        gate2.approve(client, QUALIFICATION_ID, operator_id=OPERATOR_ID)
    assert "carries no reviewer" in str(exc.value)
    assert not [c for c in client.calls if c[0] == "PATCH"]


def test_a_rejected_scorecard_cannot_be_approved():
    client = FakeClient(
        qualifications=[
            qualification(review_status="rejected", disqualification_notes="no backend demand")
        ]
    )
    with pytest.raises(QualificationGateError) as exc:
        gate2.approve(client, QUALIFICATION_ID, operator_id=OPERATOR_ID)
    assert "'rejected'" in str(exc.value)
    assert "qualifications show" in str(exc.value)
    assert not [c for c in client.calls if c[0] == "PATCH"]


def test_approval_refuses_when_the_database_recorded_another_reviewer():
    client = FakeClient(qualifications=[qualification()], misattribute=True)
    with pytest.raises(QualificationGateError) as exc:
        gate2.approve(client, QUALIFICATION_ID, operator_id=OPERATOR_ID)
    assert "not the operator who signed in" in str(exc.value)


def test_approval_explains_the_second_approved_scorecard_constraint():
    client = FakeClient(
        qualifications=[qualification()],
        patch_error=DatabaseError(
            'duplicate key value violates unique constraint "uq_lead_qualifications_approved"',
            code="23505",
        ),
    )
    with pytest.raises(QualificationGateError) as exc:
        gate2.approve(client, QUALIFICATION_ID, operator_id=OPERATOR_ID)
    assert "already has an approved scorecard" in str(exc.value)
    assert "uq_lead_qualifications_approved" in str(exc.value)


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def test_rejection_requires_a_reason():
    client = FakeClient(qualifications=[qualification()])
    with pytest.raises(QualificationGateError) as exc:
        gate2.reject(client, QUALIFICATION_ID, operator_id=OPERATOR_ID, reason="   ")
    assert "must state a reason" in str(exc.value)
    assert not [c for c in client.calls if c[0] == "PATCH"], "nothing may be written"


def test_rejection_is_attributed_through_the_audit_ledger():
    client = FakeClient(qualifications=[qualification()])
    row = gate2.reject(
        client, QUALIFICATION_ID, operator_id=OPERATOR_ID, reason="no documented backend demand"
    )
    assert row["review_status"] == "rejected"
    assert row["reviewed_by_human_id"] is None, "the schema forbids a reviewer id here"
    assert "no documented backend demand" in row["disqualification_notes"]
    audit = row["audit_rejection"]
    assert audit["actor_identity"] == OPERATOR_ID
    assert audit["new_values"]["review_status"] == "rejected"


def test_rejection_is_idempotent_and_keeps_the_first_reason():
    client = FakeClient(qualifications=[qualification()])
    first = gate2.reject(client, QUALIFICATION_ID, operator_id=OPERATOR_ID, reason="first")
    patches = [c for c in client.calls if c[0] == "PATCH"]
    second = gate2.reject(client, QUALIFICATION_ID, operator_id=OPERATOR_ID, reason="second")
    assert second["disqualification_notes"] == first["disqualification_notes"]
    assert len(patches) == 1


def test_an_approved_scorecard_cannot_be_rejected():
    client = FakeClient(
        qualifications=[
            qualification(
                review_status="approved",
                reviewed_by_human_id=OPERATOR_ID,
                reviewed_at="2026-11-23T09:00:00+00:00",
            )
        ]
    )
    with pytest.raises(QualificationGateError) as exc:
        gate2.reject(client, QUALIFICATION_ID, operator_id=OPERATOR_ID, reason="changed my mind")
    assert "'approved'" in str(exc.value)
    assert not [c for c in client.calls if c[0] == "PATCH"]


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


def test_list_filters_by_status_and_company_and_validates_the_status():
    client = FakeClient(
        qualifications=[
            qualification(),
            qualification(id="q-2", company_id="other", review_status="approved"),
        ]
    )
    drafts = gate2.list_qualifications(client, status="draft")
    assert [row["id"] for row in drafts] == [QUALIFICATION_ID]
    mine = gate2.list_qualifications(client, company_id=COMPANY_ID)
    assert [row["id"] for row in mine] == [QUALIFICATION_ID]
    with pytest.raises(QualificationGateError):
        gate2.list_qualifications(client, status="nonsense")


def test_show_includes_dimension_evidence():
    client = FakeClient(qualifications=[qualification()])
    row = gate2.show(client, QUALIFICATION_ID)
    assert row["dimension_evidence"]["customer_flow"]["evidence"] == "finding for customer_flow"
    assert row["evaluator_identity"] == AGENT_ID


def test_show_fails_closed_on_a_missing_row():
    with pytest.raises(QualificationGateError) as exc:
        gate2.show(FakeClient(), QUALIFICATION_ID)
    assert "not found" in str(exc.value)


# ---------------------------------------------------------------------------
# persist — the qualifier agent
# ---------------------------------------------------------------------------


def _fixture_tables() -> dict[str, list[dict]]:
    """The live synthetic fixture: one verified fact, one hypothesis, one gap."""
    return {
        "companies": [
            {
                "id": COMPANY_ID,
                "name": "[SYNTHETIC] Local Gate 1 Fixture",
                "primary_domain": "fixture.example.com",
                "primary_segment": "digital_agency",
                "headcount_min": None,
                "headcount_max": None,
                "country": None,
                "lifecycle_stage": "identified",
            }
        ],
        "research_reports": [
            {
                "id": "r-1",
                "company_id": COMPANY_ID,
                "report_version": 1,
                "overall_confidence": "low",
                "created_at": "2026-11-23T10:00:00+00:00",
            }
        ],
        "research_findings": [
            {
                "id": "f-1",
                "report_id": "r-1",
                "company_id": COMPANY_ID,
                "finding_type": "verified_fact",
                "category": "technology_stack",
                "statement": "The fixture publishes a Webflow site.",
                "evidence_url": "https://fixture.example.com",
                "evidence_excerpt": "Built with Webflow.",
                "confidence": "medium",
                "created_at": "2026-11-23T10:00:00+00:00",
            },
            {
                "id": "f-2",
                "report_id": "r-1",
                "company_id": COMPANY_ID,
                "finding_type": "hypothesis",
                "category": "growth_signals",
                "statement": "May be growing.",
                "evidence_url": None,
                "evidence_excerpt": None,
                "confidence": "low",
                "created_at": "2026-11-23T10:00:00+00:00",
            },
            {
                "id": "f-3",
                "report_id": "r-1",
                "company_id": COMPANY_ID,
                "finding_type": "information_gap_tbd",
                "category": "pricing_rates",
                "statement": "No pricing was measured.",
                "evidence_url": None,
                "evidence_excerpt": None,
                "confidence": "unverified",
                "created_at": "2026-11-23T10:00:00+00:00",
            },
        ],
    }


def test_inconclusive_evidence_is_refused_before_any_write(monkeypatch):
    fake = FakeClient(tables=_fixture_tables())
    _StubAuth.claims = qualifier_claims()
    monkeypatch.setattr("operations.qualifications.PostgresAuthClient", _StubAuth)
    monkeypatch.setattr(
        "operations.qualifications.PostgresClient", lambda *a, **k: fake
    )

    with pytest.raises(QualificationNotMeasured) as exc:
        gate2.persist_scorecard(COMPANY_ID, environ=ENV)

    message = str(exc.value)
    assert "has not been measured" in message
    assert "inconclusive" in message and "ADR-0006" in message
    assert not [c for c in fake.calls if c[0] in {"POST", "PATCH"}], "nothing may be written"


def test_a_company_hidden_by_task_scope_is_explained(monkeypatch):
    fake = FakeClient(tables={"companies": [], "research_reports": [], "research_findings": []})
    _StubAuth.claims = qualifier_claims()
    monkeypatch.setattr("operations.qualifications.PostgresAuthClient", _StubAuth)
    monkeypatch.setattr("operations.qualifications.PostgresClient", lambda *a, **k: fake)

    with pytest.raises(QualificationGateError) as exc:
        gate2.persist_scorecard(COMPANY_ID, environ=ENV)
    assert "not visible to the qualifier" in str(exc.value)
    assert "task-scoped" in str(exc.value)
    assert not [c for c in fake.calls if c[0] in {"POST", "PATCH"}]


def test_a_42501_names_the_missing_claim_or_scope(monkeypatch):
    fake = FakeClient()
    _StubAuth.claims = qualifier_claims()
    monkeypatch.setattr("operations.qualifications.PostgresAuthClient", _StubAuth)
    monkeypatch.setattr("operations.qualifications.PostgresClient", lambda *a, **k: fake)

    def denied(*args: Any, **kwargs: Any) -> Any:
        raise DatabaseError("permission denied for table lead_qualifications", code="42501")

    monkeypatch.setattr("operations.qualifications.qualify_company", denied)

    with pytest.raises(QualifierWriteDenied) as exc:
        gate2.persist_scorecard(COMPANY_ID, environ=ENV)
    assert exc.value.missing_claim
    assert "tasks" in exc.value.missing_claim
    assert "pending or in_progress" in str(exc.value)


def test_persist_refuses_a_human_identity_before_connecting(monkeypatch):
    _StubAuth.claims = {
        "sub": OPERATOR_ID,
        "app_metadata": {"user_role": "operator"},
    }
    monkeypatch.setattr("operations.qualifications.PostgresAuthClient", _StubAuth)

    def _no_client(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("a client must not be built for a non-qualifier identity")

    monkeypatch.setattr("operations.qualifications.PostgresClient", _no_client)

    with pytest.raises(QualifierIdentityError) as exc:
        gate2.persist_scorecard(COMPANY_ID, environ=ENV)
    assert "requires 'agent'" in str(exc.value)
    assert "human operator may review" in str(exc.value)


def test_persist_returns_the_verified_draft(monkeypatch):
    fake = FakeClient()
    _StubAuth.claims = qualifier_claims()
    monkeypatch.setattr("operations.qualifications.PostgresAuthClient", _StubAuth)
    monkeypatch.setattr("operations.qualifications.PostgresClient", lambda *a, **k: fake)

    row = qualification()
    monkeypatch.setattr(
        "operations.qualifications.qualify_company",
        lambda *a, **k: SimpleNamespace(persisted=row),
    )

    assert gate2.persist_scorecard(COMPANY_ID, environ=ENV) is row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _session(client: FakeClient) -> SimpleNamespace:
    return SimpleNamespace(
        client=client,
        user_id=OPERATOR_ID,
        email="operator@partnership.internal",
        role="operator",
        claims={"sub": OPERATOR_ID, "app_metadata": {"user_role": "operator"}},
    )


def test_cli_refuses_a_non_qualifier_identity_for_persistence(monkeypatch, capsys):
    _StubAuth.claims = {"sub": OPERATOR_ID, "app_metadata": {"user_role": "operator"}}
    monkeypatch.setattr("operations.qualifications.PostgresAuthClient", _StubAuth)
    monkeypatch.setattr("operations.cli.open_session", lambda *a, **k: _session(FakeClient()))

    def _no_client(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("a client must not be built for a non-qualifier identity")

    monkeypatch.setattr("operations.qualifications.PostgresClient", _no_client)

    code = cli.main(["qualifications", "persist", COMPANY_ID])
    captured = capsys.readouterr()
    assert code == 2
    assert "refused" in captured.err
    assert "requires 'agent'" in captured.err
    assert "Traceback" not in captured.err


def test_cli_lists_scorecards_as_json(monkeypatch, capsys):
    client = FakeClient(qualifications=[qualification()])
    monkeypatch.setattr("operations.cli.open_session", lambda *a, **k: _session(client))

    code = cli.main(["--json", "qualifications", "list", "--status", "draft"])
    captured = capsys.readouterr()
    assert code == 0
    assert QUALIFICATION_ID in captured.out
    assert '"review_status": "draft"' in captured.out


def test_cli_approve_refusal_exits_2_without_a_traceback(monkeypatch, capsys):
    client = FakeClient(
        qualifications=[
            qualification(review_status="rejected", disqualification_notes="no demand")
        ]
    )
    monkeypatch.setattr("operations.cli.open_session", lambda *a, **k: _session(client))

    code = cli.main(["qualifications", "approve", QUALIFICATION_ID])
    captured = capsys.readouterr()
    assert code == 2
    assert "refused" in captured.err
    assert "'rejected'" in captured.err
    assert "Traceback" not in captured.err


def test_cli_show_prints_dimension_evidence(monkeypatch, capsys):
    client = FakeClient(qualifications=[qualification()])
    monkeypatch.setattr("operations.cli.open_session", lambda *a, **k: _session(client))

    code = cli.main(["qualifications", "show", QUALIFICATION_ID])
    captured = capsys.readouterr()
    assert code == 0
    assert "dimension_evidence" in captured.out
    assert "finding for customer_flow" in captured.out
