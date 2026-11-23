"""Tests for the production outreach ports, the draft generator and the CLI.

What is proven here
-------------------
* the three adapters answer the gate's questions from real column names, and fail
  closed (typed error) on every state that is not an answer: missing row, invisible
  row, wrong type, ambiguous row, unreadable ledger, refused write;
* a whole draft can be created end to end through the *real* adapters and the
  reviewed gate, and the row the database returns satisfies the gate's read-back
  checks (this is the contract that would break on a driver-type mismatch);
* the generator cites a real finding UUID, never mentions price, headcount or a
  deadline, and stays under its length caps;
* a gate rejection surfaces its code through the CLI's own code path;
* the CLI cannot reach dispatch — asserted by reading its source, not by trusting
  a comment.

The database is a stateful fake: it speaks the same ``get``/``post``/``patch``
surface as ``PostgresClient`` and validates every column name against
``crm/schema.sql``, so an adapter that invents a column fails here rather than in
production.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "crm" / "schema.sql"

from outreach.adapters import (  # noqa: E402
    DRAFTABLE_LIFECYCLE_STAGES,
    PostgresDraftWriter,
    PostgresQualificationReader,
    PostgresSuppressionReader,
)
from outreach.drafts import (  # noqa: E402
    MAX_BODY_CHARS,
    MAX_SUBJECT_CHARS,
    DraftGenerationError,
    citations,
    render_draft,
    template_for,
)
from outreach.gate import (  # noqa: E402
    DraftRequest,
    RejectionCode,
    _content_codes,
    create_draft,
    draft_idempotency_key,
    evaluate_draft_eligibility,
)
from outreach.ports import (  # noqa: E402
    QUALIFICATION_DIMENSIONS,
    DraftPersistenceError,
    QualificationLookupError,
    SuppressionLookupError,
)

OPERATOR = "8f6a0d5c-1111-4a11-8a11-111111111111"
COMPANY = "22222222-2222-4222-8222-222222222222"
CONTACT = "44444444-4444-4444-8444-444444444444"
REVIEWER = "33333333-3333-4333-8333-333333333333"
QUALIFICATION = "11111111-1111-4111-8111-111111111111"
CAMPAIGN = "55555555-5555-4555-8555-555555555555"
OPERATOR_CLAIMS = {"sub": OPERATOR, "role": "authenticated", "app_metadata": {"user_role": "operator"}}
AGENT_CLAIMS = {
    "sub": "99999999-9999-4999-8999-999999999999",
    "role": "authenticated",
    "app_metadata": {"user_role": "agent", "subagent_type": "drafter", "agent_id": "hermes-drafter-v1"},
}


def fid(n: int) -> str:
    return f"aaaaaaaa-0000-4000-8000-{n:012d}"


# ---------------------------------------------------------------------------
# The fake database: PostgresClient's query language over real column names
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def schema_columns() -> dict[str, frozenset[str]]:
    """Column names per table, parsed out of ``crm/schema.sql``.

    Reading the deployed DDL instead of hand-copying it means a test cannot pass
    against a column the schema does not have.
    """
    text = SCHEMA.read_text(encoding="utf-8")
    tables: dict[str, frozenset[str]] = {}
    for match in re.finditer(
        r"CREATE TABLE (\w+) \((.*?)\n\);", text, flags=re.DOTALL
    ):
        name, body = match.group(1), match.group(2)
        columns: set[str] = set()
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(
                ("CONSTRAINT", "PRIMARY KEY", "UNIQUE", "CHECK", "FOREIGN KEY", ")", "--")
            ):
                continue
            token = re.match(r"([a-z_][a-z0-9_]*)\s+\S", stripped)
            if token:
                columns.add(token.group(1))
        tables[name] = frozenset(columns)
    return tables


#: Columns psycopg hands back as ``uuid.UUID``.
_UUID_COLUMNS = {
    "id",
    "company_id",
    "contact_id",
    "campaign_id",
    "parent_message_id",
    "reviewed_by_human_id",
    "approved_by_human_id",
    "override_by_human_id",
}


class FakeClient:
    """A stateful in-memory database with PostgresClient's surface.

    ``fail_get`` / ``fail_post`` name tables whose access raises, so the
    fail-closed paths can be exercised without a real connection. ``typed``
    returns ``uuid.UUID`` objects for UUID columns like psycopg does, which is what
    proves the adapters normalise driver types before the gate sees them.
    """

    def __init__(self, tables=None, *, claims=None, fail_get=None, fail_post=None, typed=True):
        self.tables: dict[str, list[dict]] = {
            name: [dict(row) for row in rows] for name, rows in (tables or {}).items()
        }
        self.claims = claims or {}
        self.fail_get = set(fail_get or ())
        self.fail_post = set(fail_post or ())
        self.typed = typed
        self.calls: list[tuple[str, str, dict]] = []
        self._counter = 0

    # -- helpers -----------------------------------------------------------
    def _next_id(self) -> str:
        self._counter += 1
        return str(uuid4())

    def _check_columns(self, table: str, names) -> None:
        known = schema_columns().get(table)
        assert known is not None, f"{table} is not a table in crm/schema.sql"
        unknown = [name for name in names if name not in known]
        assert not unknown, f"{table} has no column(s) {unknown}"

    def _matches(self, row: dict, params: dict) -> bool:
        for key, raw in params.items():
            if key in {"select", "order", "limit", "offset"}:
                continue
            value = row.get(key)
            if raw == "is.null":
                if value is not None:
                    return False
            elif raw == "not.is.null":
                if value is None:
                    return False
            elif raw.startswith("in."):
                wanted = [item.strip().strip("'") for item in raw[3:].strip("()").split(",")]
                if str(value) not in wanted:
                    return False
            elif raw.startswith("eq."):
                if str(value) != raw[3:]:
                    return False
            else:  # pragma: no cover - the adapters never send another operator
                raise AssertionError(f"unsupported filter {key}={raw!r}")
        return True

    def _project(self, table: str, row: dict, selection) -> dict:
        if not selection or selection.strip() == "*":
            return dict(row)
        columns = [part.strip() for part in selection.split(",") if part.strip()]
        self._check_columns(table, columns)
        return {name: row.get(name) for name in columns}

    def _out(self, table: str, row: dict) -> dict:
        if not self.typed:
            return row
        return {
            key: (UUID(value) if key in _UUID_COLUMNS and isinstance(value, str) else value)
            for key, value in row.items()
        }

    # -- surface -----------------------------------------------------------
    def get(self, table, params=None):
        params = dict(params or {})
        self.calls.append(("get", table, params))
        if table in self.fail_get:
            raise RuntimeError(f"connection reset while reading {table}")
        rows = [row for row in self.tables.get(table, []) if self._matches(row, params)]
        ordering = str(params.get("order") or "")
        if "desc" in ordering:
            rows = list(reversed(rows))
        if params.get("limit") is not None:
            rows = rows[: int(params["limit"])]
        return [self._out(table, self._project(table, row, params.get("select"))) for row in rows]

    def post(self, table, data):
        rows = [data] if isinstance(data, dict) else list(data)
        self.calls.append(("post", table, dict(rows[0]) if rows else {}))
        if table in self.fail_post:
            raise RuntimeError("[42501] new row violates row-level security policy")
        stored = []
        for row in rows:
            self._check_columns(table, list(row))
            record = {**self._defaults().get(table, {}), **row}
            record.setdefault("id", self._next_id())
            record.setdefault("created_at", f"2026-11-23T10:00:{len(self.tables.get(table, [])):02d}+00:00")
            self.tables.setdefault(table, []).append(record)
            stored.append(self._out(table, record))
        return stored

    @staticmethod
    def _defaults() -> dict[str, dict]:
        """The database's own defaults for the columns a caller leaves out."""
        return {
            "outreach_messages": {
                "campaign_id": None,
                "approved_by_human_id": None,
                "approved_at": None,
                "rejection_reason": None,
                "is_admin_override": False,
                "override_reason": None,
                "override_by_human_id": None,
                "sending_status": "draft",
                "approval_status": "draft",
                "scheduled_for": None,
                "sent_at": None,
                "external_message_id": None,
                "error_message": None,
                "sequence_step": 1,
                "parent_message_id": None,
                "personalization_evidence_ids": [],
                "proposed_commercial_model": "project_based_wholesale",
            },
            "activities": {"metadata": {}},
        }

    def patch(self, table, data, params=None):
        self.calls.append(("patch", table, dict(data)))
        matched = [row for row in self.tables.get(table, []) if self._matches(row, dict(params or {}))]
        for row in matched:
            row.update(data)
            # fn_enforce_outreach_human_approval stamps the trusted timestamp.
            if table == "outreach_messages" and data.get("approval_status") == "approved":
                row["approved_at"] = "2026-11-23T11:00:00+00:00"
        return [self._out(table, row) for row in matched]

    def close(self) -> None:  # parity with PostgresClient
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DIMENSION_POINTS = {
    "customer_flow": 14.0,
    "development_demand": 14.0,
    "repeatability_orders": 10.0,
    "technical_fit": 12.0,
    "business_size": 8.0,
    "decision_maker_accessibility": 7.0,
    "commercial_fit": 7.0,
}


def dimension_evidence(quote: str = "Portfolio lists three live Webflow builds for named clients.") -> dict:
    return {
        key: {
            "score": _DIMENSION_POINTS[key],
            "max": 20,
            "evidence": f"[{fid(index)}] {quote}",
            "finding_ids": [fid(index)],
            "excerpt": quote,
            "confidence": "high",
        }
        for index, (key, _) in enumerate(QUALIFICATION_DIMENSIONS)
    }


def qualification_row(**over) -> dict:
    row = {
        "id": QUALIFICATION,
        "company_id": COMPANY,
        "overall_score": 72.0,
        "qualification_tier": "tier_2_prospective",
        "dimension_evidence": dimension_evidence(),
        "disqualification_reasons": [],
        "recommended_next_action": "Invite to a scoping call",
        "review_status": "approved",
        "reviewed_by_human_id": REVIEWER,
        "reviewed_at": "2026-11-13T10:00:00+00:00",
        "created_at": "2026-11-13T10:00:00+00:00",
    }
    row.update(over)
    return row


def company_row(**over) -> dict:
    row = {
        "id": COMPANY,
        "name": "Refokus",
        "primary_domain": "refokus.com",
        "lifecycle_stage": "qualified",
        "is_suppressed": False,
        "suppression_reason": None,
        "qualification_tier": "tier_2_prospective",
        "created_at": "2026-11-01T10:00:00+00:00",
    }
    row.update(over)
    return row


def contact_row(**over) -> dict:
    row = {
        "id": CONTACT,
        "company_id": COMPANY,
        "first_name": "Marta",
        "email": "marta@refokus.com",
        "linkedin_url": "https://www.linkedin.com/in/marta-refokus",
        "is_opted_out": False,
        "opt_out_at": None,
        "created_at": "2026-11-01T10:00:00+00:00",
    }
    row.update(over)
    return row


def campaign_row(**over) -> dict:
    row = {
        "id": CAMPAIGN,
        "name": "Webflow studios — Q4",
        "target_segment": "webflow_studio",
        "is_active": True,
        "created_at": "2026-11-01T10:00:00+00:00",
    }
    row.update(over)
    return row


def domain_row(domain: str = "refokus.com", **over) -> dict:
    row = {"id": str(uuid4()), "company_id": COMPANY, "domain": domain, "is_primary": True}
    row.update(over)
    return row


def full_db(**over) -> FakeClient:
    """One qualified, approved, reachable company and its one contact.

    Keyword arguments are table overrides (``contacts=[...]``) unless they name a
    :class:`FakeClient` option (``fail_get``, ``fail_post``, ``typed``, ``claims``).
    """
    options = {key: over.pop(key) for key in ("fail_get", "fail_post", "typed", "claims") if key in over}
    tables = {
        "companies": [company_row()],
        "company_domains": [domain_row()],
        "contacts": [contact_row()],
        "lead_qualifications": [qualification_row()],
        "outreach_campaigns": [campaign_row()],
        "outreach_messages": [],
        "suppression_registry": [],
        "activities": [],
    }
    tables.update(over)
    options.setdefault("claims", OPERATOR_CLAIMS)
    return FakeClient(tables, **options)


def approved_request(**over) -> DraftRequest:
    base = dict(
        company_id=COMPANY,
        contact_id=CONTACT,
        channel="email",
        outreach_event="initial_partnership_intro",
        subject="Backend work behind Refokus's client projects",
        body="Hello,\n\nA short note about the backend layer behind your Webflow builds.",
    )
    base.update(over)
    return DraftRequest(**base)


# ---------------------------------------------------------------------------
# QualificationReader
# ---------------------------------------------------------------------------


def test_qualification_reader_maps_the_approved_row():
    reader = PostgresQualificationReader(full_db())
    record = reader.get_qualification(COMPANY)

    assert record is not None
    assert record.qualification_id == QUALIFICATION
    assert record.review_status == "approved"
    assert record.qualification_tier == "tier_2_prospective"
    assert record.overall_score == 72.0
    assert record.reviewed_by_human_id == REVIEWER
    # The evidence UUIDs are what ``personalization_evidence_ids`` is built from.
    assert tuple(fid(index) for index in range(7)) == record.evidence_refs
    # No column exists for these, so the reader reports "not exposed", never False.
    assert record.needs_human_review is None
    assert record.insufficient_evidence is None


def test_qualification_reader_returns_none_only_when_there_is_no_row():
    db = FakeClient({"lead_qualifications": []})
    assert PostgresQualificationReader(db).get_qualification(COMPANY) is None


def test_qualification_reader_returns_a_pending_row_so_the_gate_can_name_it():
    db = FakeClient(
        {
            "lead_qualifications": [
                qualification_row(review_status="pending_human_review", reviewed_by_human_id=None, reviewed_at=None)
            ]
        }
    )
    record = PostgresQualificationReader(db).get_qualification(COMPANY)
    assert record is not None and record.review_status == "pending_human_review"

    decision = evaluate_draft_eligibility(
        approved_request(),
        PostgresQualificationReader(db),
        PostgresSuppressionReader(full_db(), registry_readable=True),
    )
    assert decision.code is RejectionCode.QUALIFICATION_PENDING_REVIEW


def test_qualification_reader_prefers_the_approved_row_over_a_newer_draft():
    db = FakeClient(
        {
            "lead_qualifications": [
                qualification_row(),
                qualification_row(
                    id=str(uuid4()),
                    review_status="draft",
                    reviewed_by_human_id=None,
                    reviewed_at=None,
                    created_at="2026-11-20T10:00:00+00:00",
                ),
            ]
        }
    )
    record = PostgresQualificationReader(db).get_qualification(COMPANY)
    assert record is not None and record.review_status == "approved"


def test_qualification_reader_refuses_two_approved_rows():
    db = FakeClient(
        {"lead_qualifications": [qualification_row(), qualification_row(id=str(uuid4()))]}
    )
    with pytest.raises(QualificationLookupError) as exc:
        PostgresQualificationReader(db).get_qualification(COMPANY)
    assert "uq_lead_qualifications_approved" in str(exc.value)


def test_qualification_reader_refuses_a_row_for_another_company():
    """A reply that does not belong to the request is refused, not stored as this company's."""

    class LeakyClient(FakeClient):
        def get(self, table, params=None):
            params = dict(params or {})
            if table == "lead_qualifications":
                params.pop("company_id", None)
            return super().get(table, params)

    db = LeakyClient({"lead_qualifications": [qualification_row(company_id=str(uuid4()))]})
    with pytest.raises(QualificationLookupError):
        PostgresQualificationReader(db).get_qualification(COMPANY)


@pytest.mark.parametrize(
    "broken",
    [
        {"dimension_evidence": ["not", "an", "object"]},
        {"disqualification_reasons": "not-an-array"},
        {"overall_score": None},
        {"overall_score": "seventy-two"},
    ],
)
def test_qualification_reader_refuses_a_row_it_cannot_read(broken):
    db = FakeClient({"lead_qualifications": [qualification_row(**broken)]})
    with pytest.raises(QualificationLookupError):
        PostgresQualificationReader(db).get_qualification(COMPANY)


def test_qualification_reader_wraps_a_transport_failure():
    db = FakeClient({"lead_qualifications": []}, fail_get={"lead_qualifications"})
    with pytest.raises(QualificationLookupError) as exc:
        PostgresQualificationReader(db).get_qualification(COMPANY)
    assert "connection reset" in str(exc.value)


def test_qualification_reader_rows_are_normalised_before_the_gate_sees_them():
    """psycopg returns UUID objects; the gate compares against strings."""
    record = PostgresQualificationReader(full_db()).get_qualification(COMPANY)
    assert record is not None
    assert isinstance(record.company_id, str) and record.company_id == COMPANY


# ---------------------------------------------------------------------------
# SuppressionReader
# ---------------------------------------------------------------------------


def test_suppression_reader_reports_clear_and_names_every_source():
    report = PostgresSuppressionReader(full_db()).check(COMPANY, CONTACT)
    assert report.state == "clear"
    assert set(report.sources_checked) == {
        "contacts.is_opted_out",
        "companies.is_suppressed",
        "company_domains",
        "suppression_registry",
    }
    assert report.findings == ()


def test_suppression_reader_reports_a_contact_opt_out():
    db = full_db(contacts=[contact_row(is_opted_out=True, opt_out_at="2026-11-20T10:00:00+00:00")])
    report = PostgresSuppressionReader(db).check(COMPANY, CONTACT)
    assert report.state == "suppressed"
    assert report.targets() == ("contact_email",)


def test_suppression_reader_reports_a_suppressed_company_as_a_company():
    db = full_db(
        companies=[company_row(is_suppressed=True, suppression_reason="Asked us never to contact")]
    )
    report = PostgresSuppressionReader(db).check(COMPANY, CONTACT)
    assert report.state == "suppressed"
    assert report.targets() == ("company",)
    assert "never to contact" in report.detail


def test_suppression_reader_reports_a_suppressed_domain_from_the_registry():
    db = full_db(
        suppression_registry=[
            {"target_type": "domain", "identifier": "refokus.com", "reason": "manual", "source": "manual_blacklist"}
        ]
    )
    report = PostgresSuppressionReader(db).check(COMPANY, CONTACT)
    assert report.state == "suppressed"
    assert report.targets() == ("domain",)


def test_suppression_reader_reports_a_suppressed_email_from_the_registry():
    db = full_db(
        suppression_registry=[
            {"target_type": "email", "identifier": "marta@refokus.com", "reason": "opt-out", "source": "opt_out_request"}
        ]
    )
    report = PostgresSuppressionReader(db).check(COMPANY, CONTACT)
    assert report.targets() == ("contact_email",)


def test_suppression_reader_checks_alternate_domains_not_just_the_primary():
    db = full_db(
        company_domains=[domain_row(), domain_row(domain="refokus.dev", is_primary=False)],
        suppression_registry=[
            {"target_type": "domain", "identifier": "refokus.dev", "reason": "manual", "source": "manual_blacklist"}
        ],
    )
    assert PostgresSuppressionReader(db).check(COMPANY, CONTACT).state == "suppressed"


def test_suppression_reader_refuses_an_invisible_contact():
    db = full_db(contacts=[])
    with pytest.raises(SuppressionLookupError):
        PostgresSuppressionReader(db).check(COMPANY, CONTACT)


def test_suppression_reader_refuses_a_contact_that_belongs_to_another_company():
    db = full_db(contacts=[contact_row(company_id=str(uuid4()))])
    with pytest.raises(SuppressionLookupError):
        PostgresSuppressionReader(db).check(COMPANY, CONTACT)


def test_suppression_reader_refuses_an_invisible_company():
    db = full_db(companies=[])
    with pytest.raises(SuppressionLookupError):
        PostgresSuppressionReader(db).check(COMPANY, CONTACT)


def test_suppression_reader_refuses_a_company_with_no_domain_to_check():
    db = full_db(companies=[company_row(primary_domain="")], company_domains=[])
    with pytest.raises(SuppressionLookupError):
        PostgresSuppressionReader(db).check(COMPANY, CONTACT)


def test_suppression_reader_refuses_when_the_ledger_is_unreadable():
    db = full_db(fail_get={"suppression_registry"})
    with pytest.raises(SuppressionLookupError):
        PostgresSuppressionReader(db).check(COMPANY, CONTACT)


def test_suppression_reader_refuses_an_agent_identity_instead_of_reading_zero_rows():
    """The deployed RLS gives agents no path to suppression_registry (L1835-1836)."""
    db = full_db()
    db.claims = AGENT_CLAIMS
    reader = PostgresSuppressionReader(db)
    assert reader.registry_readable is False
    with pytest.raises(SuppressionLookupError) as exc:
        reader.check(COMPANY, CONTACT)
    assert "is_identifier_suppressed" in str(exc.value)
    # Nothing was read, so nothing could be mistaken for a clean answer.
    assert db.calls == []


def test_suppression_reader_refuses_an_unattributable_registry_row():
    db = full_db(
        suppression_registry=[
            {"target_type": "other", "identifier": "refokus.com", "reason": "?", "source": "manual"}
        ]
    )
    with pytest.raises(SuppressionLookupError):
        PostgresSuppressionReader(db).check(COMPANY, CONTACT)


def test_suppression_failure_is_a_gate_rejection_not_a_pass():
    db = full_db(fail_get={"contacts"})
    decision = evaluate_draft_eligibility(
        approved_request(),
        PostgresQualificationReader(full_db()),
        PostgresSuppressionReader(db),
    )
    assert decision.code is RejectionCode.SUPPRESSION_LOOKUP_FAILED
    assert not decision.eligible


# ---------------------------------------------------------------------------
# DraftWriter
# ---------------------------------------------------------------------------


def _writer_payload(db: FakeClient):
    reader = PostgresQualificationReader(db)
    suppression = PostgresSuppressionReader(db)
    writer = PostgresDraftWriter(db, OPERATOR)
    outcome = create_draft(approved_request(), reader, suppression, writer)
    return outcome, writer


def test_writer_happy_path_stores_a_clean_draft_with_evidence():
    db = full_db()
    outcome, _ = _writer_payload(db)
    assert outcome.status == "created", outcome.detail
    assert outcome.draft_id

    stored = db.tables["outreach_messages"]
    assert len(stored) == 1
    row = stored[0]
    assert row["approval_status"] == "draft"
    assert row["sending_status"] == "draft"
    assert row["company_id"] == COMPANY and row["contact_id"] == CONTACT
    assert row["drafted_by_agent_identity"] == OPERATOR
    assert row["personalization_evidence_ids"] == [fid(index) for index in range(7)]
    assert row["proposed_commercial_model"] == "project_based_wholesale"


def test_writer_is_idempotent_for_the_same_slot_and_reports_a_conflict():
    db = full_db()
    outcome, _ = _writer_payload(db)
    assert outcome.status == "created"

    second, _ = _writer_payload(db)
    assert second.status == "already_exists"
    assert second.draft_id == outcome.draft_id
    assert second.content_matches is True
    assert len(db.tables["outreach_messages"]) == 1


def test_writer_refuses_a_company_that_has_not_reached_a_draftable_stage():
    db = full_db(companies=[company_row(lifecycle_stage="researching")])
    writer = PostgresDraftWriter(db, OPERATOR)
    payload = _payload_for(db)
    with pytest.raises(DraftPersistenceError) as exc:
        writer.write(payload)
    assert "lifecycle_stage" in str(exc.value)
    assert list(DRAFTABLE_LIFECYCLE_STAGES) == ["qualified", "outreach_queued", "in_outreach"]
    assert db.tables["outreach_messages"] == []


def test_writer_refuses_a_contact_owned_by_another_company():
    db = full_db(contacts=[contact_row(company_id=str(uuid4()))])
    writer = PostgresDraftWriter(db, OPERATOR)
    with pytest.raises(DraftPersistenceError) as exc:
        writer.write(_payload_for(db))
    assert "fk_outreach_contact_company" in str(exc.value)


def test_writer_refuses_an_opted_out_contact():
    db = full_db(contacts=[contact_row(is_opted_out=True, opt_out_at="2026-11-20T10:00:00+00:00")])
    writer = PostgresDraftWriter(db, OPERATOR)
    with pytest.raises(DraftPersistenceError) as exc:
        writer.write(_payload_for(db))
    assert "opted out" in str(exc.value)


def test_writer_refuses_a_missing_contact():
    db = full_db(contacts=[])
    writer = PostgresDraftWriter(db, OPERATOR)
    with pytest.raises(DraftPersistenceError) as exc:
        writer.write(_payload_for(db))
    assert "contact_id is NOT NULL" in str(exc.value)


def test_writer_refuses_a_suppressed_company():
    db = full_db(companies=[company_row(is_suppressed=True, suppression_reason="x")])
    with pytest.raises(DraftPersistenceError):
        PostgresDraftWriter(db, OPERATOR).write(_payload_for(db))


def test_writer_refuses_a_missing_or_inactive_campaign():
    db = full_db(outreach_campaigns=[])
    with pytest.raises(DraftPersistenceError) as exc:
        PostgresDraftWriter(db, OPERATOR).write(_payload_for(db, campaign_id=CAMPAIGN))
    assert "does not exist" in str(exc.value)

    db = full_db(outreach_campaigns=[campaign_row(is_active=False)])
    with pytest.raises(DraftPersistenceError) as exc:
        PostgresDraftWriter(db, OPERATOR).write(_payload_for(db, campaign_id=CAMPAIGN))
    assert "inactive" in str(exc.value)


def test_writer_refuses_a_payload_carrying_another_identity():
    db = full_db()
    payload = _payload_for(db)
    writer = PostgresDraftWriter(db, "another-identity")
    with pytest.raises(DraftPersistenceError) as exc:
        writer.write(payload)
    assert "verified identity" in str(exc.value)


def test_writer_refuses_to_construct_without_an_identity():
    with pytest.raises(DraftPersistenceError):
        PostgresDraftWriter(full_db(), "")


def test_writer_wraps_a_policy_refusal_with_its_sqlstate():
    db = full_db(fail_post={"outreach_messages"})
    with pytest.raises(DraftPersistenceError) as exc:
        PostgresDraftWriter(db, OPERATOR).write(_payload_for(db))
    assert "42501" in str(exc.value)


def test_writer_refuses_a_row_that_came_back_approved(monkeypatch):
    db = full_db()
    writer = PostgresDraftWriter(db, OPERATOR)
    original = writer._insert

    def tampered(row):
        stored = original(row)
        stored["approval_status"] = "approved"
        return stored

    monkeypatch.setattr(writer, "_insert", tampered)
    with pytest.raises(DraftPersistenceError) as exc:
        writer.write(_payload_for(db))
    assert "not a draft" in str(exc.value)


def test_writer_find_existing_refuses_when_the_scan_window_is_full():
    rows = [
        {
            "id": str(uuid4()),
            "company_id": str(uuid4()),
            "contact_id": str(uuid4()),
            "campaign_id": None,
            "channel": "email",
            "sequence_step": 1,
            "subject": "s",
            "body": "b",
            "approval_status": "draft",
            "sending_status": "draft",
        }
        for _ in range(3)
    ]
    db = full_db(outreach_messages=rows)
    writer = PostgresDraftWriter(db, OPERATOR, slot_scan_limit=3)
    with pytest.raises(DraftPersistenceError) as exc:
        writer.find_existing("0" * 64)
    assert "cannot prove the slot is free" in str(exc.value)


def test_writer_find_existing_matches_a_stored_row_by_its_own_columns():
    db = full_db()
    writer = PostgresDraftWriter(db, OPERATOR)
    key = draft_idempotency_key(approved_request())
    assert writer.find_existing(key) is None

    writer.write(_payload_for(db))
    found = writer.find_existing(key)
    assert found is not None and found["company_id"] == COMPANY


def _payload_for(db: FakeClient, *, campaign_id: str | None = None):
    record = PostgresQualificationReader(db).get_qualification(COMPANY)
    assert record is not None
    return render_draft(
        company={"id": COMPANY, "name": "Refokus"},
        qualification=record,
        contact={"id": CONTACT, "first_name": "Marta"},
        agent_identity=OPERATOR,
        campaign_id=campaign_id,
        signer="Puria",
    )


# ---------------------------------------------------------------------------
# Gate + adapters together
# ---------------------------------------------------------------------------


def test_gate_refuses_a_company_with_no_scorecard_and_says_so():
    db = full_db(lead_qualifications=[])
    decision = evaluate_draft_eligibility(
        approved_request(),
        PostgresQualificationReader(db),
        PostgresSuppressionReader(db),
    )
    assert decision.code is RejectionCode.QUALIFICATION_MISSING
    assert COMPANY in decision.detail


def test_gate_and_real_writer_cannot_be_smuggled_past_a_missing_scorecard():
    db = full_db(lead_qualifications=[])
    outcome = create_draft(
        approved_request(),
        PostgresQualificationReader(db),
        PostgresSuppressionReader(db),
        PostgresDraftWriter(db, OPERATOR),
    )
    assert outcome.status == "rejected"
    assert outcome.decision.code is RejectionCode.QUALIFICATION_MISSING
    assert db.tables["outreach_messages"] == []


def test_a_suppressed_domain_stops_the_draft_before_any_write():
    db = full_db(
        suppression_registry=[
            {"target_type": "domain", "identifier": "refokus.com", "reason": "manual", "source": "manual_blacklist"}
        ]
    )
    outcome = create_draft(
        approved_request(),
        PostgresQualificationReader(db),
        PostgresSuppressionReader(db),
        PostgresDraftWriter(db, OPERATOR),
    )
    assert outcome.decision.code is RejectionCode.SUPPRESSED_DOMAIN
    assert db.tables["outreach_messages"] == []


# ---------------------------------------------------------------------------
# Draft generator
# ---------------------------------------------------------------------------


def _record(db: FakeClient | None = None):
    db = db or full_db()
    record = PostgresQualificationReader(db).get_qualification(COMPANY)
    assert record is not None
    return record


def test_generator_cites_a_real_finding_uuid_and_quotes_its_evidence():
    payload = render_draft(
        company={"id": COMPANY, "name": "Refokus"},
        qualification=_record(),
        contact={"id": CONTACT, "first_name": "Marta"},
        agent_identity=OPERATOR,
        signer="Puria",
    )
    cited = [ref for ref in payload.personalization_evidence_ids]
    assert cited and all(re.fullmatch(r"[0-9a-f-]{36}", ref) for ref in cited)
    assert any(ref in payload.body for ref in cited)
    assert "Portfolio lists three live Webflow builds" in payload.body
    assert payload.body.startswith("Hi Marta,")
    assert payload.personalization_rationale.startswith("Qualified lead provenance")


@pytest.mark.parametrize("step", [1, 2, 3, 7])
def test_generator_output_is_deterministic_and_under_the_caps(step):
    kwargs = dict(
        company={"id": COMPANY, "name": "Refokus"},
        qualification=_record(),
        contact={"id": CONTACT, "first_name": "Marta"},
        agent_identity=OPERATOR,
        step=step,
        signer="Puria",
    )
    first = render_draft(**kwargs)
    second = render_draft(**kwargs)
    assert first == second
    assert len(first.body) <= MAX_BODY_CHARS
    assert len(first.subject) <= MAX_SUBJECT_CHARS
    assert "\n" not in first.subject
    assert first.outreach_event == template_for(step).outreach_event


FORBIDDEN = (
    ("$", "price"),
    ("€", "price"),
    ("pricing", "price"),
    ("per hour", "rate"),
    ("hourly", "rate"),
    ("retainer", "rate"),
    ("deadline", "deadline"),
    ("within 5 days", "deadline"),
    ("asap", "deadline"),
    ("team of", "headcount"),
    ("our team", "headcount"),
    ("dedicated capacity", "capacity"),
    ("guarantee", "guarantee"),
    ("revenue share", "revenue sharing"),
    ("we have delivered", "track record"),
)


@pytest.mark.parametrize("step", [1, 2, 3])
def test_generator_never_emits_price_headcount_or_deadline_claims(step):
    payload = render_draft(
        company={"id": COMPANY, "name": "Refokus"},
        qualification=_record(),
        contact={"id": CONTACT, "first_name": "Marta"},
        agent_identity=OPERATOR,
        step=step,
        signer="Puria",
    )
    text = f"{payload.subject}\n{payload.body}".lower()
    for needle, kind in FORBIDDEN:
        assert needle not in text, f"step {step} leaked a {kind} claim: {needle!r}"


def test_generator_output_passes_the_gate_content_guards():
    payload = render_draft(
        company={"id": COMPANY, "name": "Refokus"},
        qualification=_record(),
        contact={"id": CONTACT, "first_name": "Marta"},
        agent_identity=OPERATOR,
        signer="Puria",
    )
    request = DraftRequest(
        company_id=payload.company_id,
        contact_id=payload.contact_id,
        channel=payload.channel,
        outreach_event=payload.outreach_event,
        subject=payload.subject,
        body=payload.body,
        sequence_step=payload.sequence_step,
    )
    assert _content_codes(request) == []


def test_generator_skips_a_citation_the_gate_would_refuse():
    """An excerpt that claims a team size must not be quoted; the next one is used."""
    record = _record()
    evidence = dimension_evidence()
    evidence["customer_flow"]["excerpt"] = "Our team of twelve senior developers ships every week."
    record = type(record)(**{**record.__dict__, "dimension_evidence": evidence})

    payload = render_draft(
        company={"id": COMPANY, "name": "Refokus"},
        qualification=record,
        contact={"id": CONTACT, "first_name": "Marta"},
        agent_identity=OPERATOR,
        signer="Puria",
    )
    assert "team of twelve" not in payload.body
    assert fid(1) in payload.body


def test_generator_skips_a_citation_that_quotes_a_price():
    record = _record()
    evidence = dimension_evidence()
    evidence["customer_flow"]["excerpt"] = "Projects start at $10,000 with a monthly retainer."
    record = type(record)(**{**record.__dict__, "dimension_evidence": evidence})

    payload = render_draft(
        company={"id": COMPANY, "name": "Refokus"},
        qualification=record,
        contact={"id": CONTACT, "first_name": "Marta"},
        agent_identity=OPERATOR,
        signer="Puria",
    )
    assert "$10,000" not in payload.body
    assert "retainer" not in payload.body.lower()


def test_generator_refuses_when_no_citation_survives_the_guards():
    record = _record()
    evidence = dimension_evidence(quote="Our team of twelve senior developers ships every week.")
    record = type(record)(**{**record.__dict__, "dimension_evidence": evidence})
    with pytest.raises(DraftGenerationError) as exc:
        render_draft(
            company={"id": COMPANY, "name": "Refokus"},
            qualification=record,
            contact={"id": CONTACT, "first_name": "Marta"},
            agent_identity=OPERATOR,
            signer="Puria",
        )
    assert "content guard" in str(exc.value) or "acceptable draft" in str(exc.value)


def test_generator_refuses_a_scorecard_without_uuid_evidence():
    record = _record()
    evidence = {
        key: {**entry, "finding_ids": ["research-finding-17"]}
        for key, entry in record.dimension_evidence.items()
    }
    record = type(record)(**{**record.__dict__, "dimension_evidence": evidence})
    assert citations(record) == ()
    with pytest.raises(DraftGenerationError) as exc:
        render_draft(
            company={"id": COMPANY, "name": "Refokus"},
            qualification=record,
            contact={"id": CONTACT, "first_name": "Marta"},
            agent_identity=OPERATOR,
        )
    assert "UUID" in str(exc.value)


def test_generator_refuses_a_pending_scorecard():
    record = _record()
    record = type(record)(**{**record.__dict__, "review_status": "pending_human_review"})
    with pytest.raises(DraftGenerationError) as exc:
        render_draft(
            company={"id": COMPANY, "name": "Refokus"},
            qualification=record,
            contact={"id": CONTACT, "first_name": "Marta"},
            agent_identity=OPERATOR,
        )
    assert "approved" in str(exc.value)


def test_generator_requires_a_contact_because_the_column_is_not_null():
    with pytest.raises(DraftGenerationError):
        render_draft(
            company={"id": COMPANY, "name": "Refokus"},
            qualification=_record(),
            contact=None,
            agent_identity=OPERATOR,
        )


def test_generator_refuses_a_multiline_signer():
    with pytest.raises(DraftGenerationError):
        render_draft(
            company={"id": COMPANY, "name": "Refokus"},
            qualification=_record(),
            contact={"id": CONTACT, "first_name": "Marta"},
            agent_identity=OPERATOR,
            signer="Puria\nBcc: someone@else.test",
        )


def test_generator_refuses_a_scorecard_for_another_company():
    with pytest.raises(DraftGenerationError):
        render_draft(
            company={"id": str(uuid4()), "name": "Other"},
            qualification=_record(),
            contact={"id": CONTACT, "first_name": "Marta"},
            agent_identity=OPERATOR,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    spec = importlib.util.spec_from_file_location(
        "outreach_cli_under_test", REPO / "scripts" / "outreach_cli.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _session(db: FakeClient):
    return SimpleNamespace(
        client=db,
        claims=OPERATOR_CLAIMS,
        user_id=OPERATOR,
        email="operator@partnership.internal",
        role="operator",
    )


def _draft_args(**over):
    base = dict(
        company_id=COMPANY,
        contact_id=CONTACT,
        campaign_id=None,
        step=1,
        channel="email",
        signer=None,
        json=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_cli_never_imports_dispatch():
    source = (REPO / "scripts" / "outreach_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    assert not [name for name in imported if "dispatch" in name.lower()]
    for forbidden in ("smtplib", "http.client", "socket"):
        assert forbidden not in imported
        assert forbidden not in source

    # And it does not reach the dispatch surface indirectly either.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"send", "queue", "schedule"}:
            raise AssertionError(f"the CLI references .{node.attr}(); this build must not send")


def test_cli_states_that_dispatch_is_disabled():
    module = _cli()
    assert "disabled" in module.DISPATCH_STATE.lower()
    assert "no sending path" in module.DISPATCH_STATE.lower()


def test_cli_declares_the_human_gates_it_acts_on():
    source = (REPO / "scripts" / "outreach_cli.py").read_text(encoding="utf-8")
    assert 'assert_gate(\n        "G3"' in source
    assert 'assert_gate(\n        "G4"' in source


def test_cli_draft_command_creates_one_draft_and_prints_why(capsys):
    module = _cli()
    db = full_db()
    assert module._cmd_draft(_session(db), _draft_args()) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "created"
    assert payload["message_id"]
    assert payload["allowed_because"].startswith("qualified, human-approved")
    assert payload["dispatch"] == "disabled"
    # The row names the operator that caused it, not an identity this build cannot verify.
    assert payload["drafted_by_agent_identity"] == OPERATOR
    assert len(db.tables["outreach_messages"]) == 1
    assert db.tables["outreach_messages"][0]["approval_status"] == "draft"


def test_cli_never_asserts_its_own_claims_or_builds_its_own_client():
    """A CLI that constructed a client would be asserting an identity locally."""
    source = (REPO / "scripts" / "outreach_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "open_session" in called
    assert "PostgresClient" not in called
    assert "from integrations.postgres.client import" not in source


def test_cli_draft_reports_the_gate_code_when_there_is_no_scorecard(capsys):
    module = _cli()
    db = full_db(lead_qualifications=[])
    assert module._cmd_draft(_session(db), _draft_args()) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rejected"
    assert payload["rejection_code"] == RejectionCode.QUALIFICATION_MISSING.value
    assert db.tables["outreach_messages"] == []


def test_cli_draft_names_the_suppression_code(capsys):
    module = _cli()
    db = full_db(companies=[company_row(is_suppressed=True, suppression_reason="never contact")])
    assert module._cmd_draft(_session(db), _draft_args()) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["rejection_code"] == RejectionCode.SUPPRESSED_COMPANY.value


def test_cli_list_and_show_read_the_store(capsys):
    module = _cli()
    db = full_db()
    assert module._cmd_draft(_session(db), _draft_args()) == 0
    message_id = json.loads(capsys.readouterr().out)["message_id"]

    assert module._cmd_list(_session(db), argparse.Namespace(company_id=COMPANY, limit=10, json=True)) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["count"] == 1 and listing["messages"][0]["id"] == message_id

    assert module._cmd_show(_session(db), argparse.Namespace(message_id=message_id, json=True)) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["message"]["id"] == message_id
    assert shown["dispatch"] == "disabled"


def test_cli_approve_records_gate_three_and_keeps_the_message_a_draft(capsys):
    module = _cli()
    db = full_db()
    assert module._cmd_draft(_session(db), _draft_args()) == 0
    message_id = json.loads(capsys.readouterr().out)["message_id"]

    assert (
        module._cmd_approve(
            _session(db), argparse.Namespace(message_id=message_id, note="scoped and checked", json=True)
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["approval_status"] == "approved"
    assert result["approved_by_human_id"] == OPERATOR
    assert result["sending_status"] == "draft"
    assert result["dispatch"] == "disabled"
    assert result["note_activity_id"]

    stored = db.tables["outreach_messages"][0]
    assert stored["approval_status"] == "approved"
    assert stored["sending_status"] == "draft"
    assert stored["sent_at"] is None
    notes = [row for row in db.tables["activities"] if row["activity_type"] == "note"]
    assert notes and notes[0]["performed_by_actor_type"] == "human"


def test_cli_approve_refuses_a_message_that_is_already_approved():
    module = _cli()
    db = full_db()
    assert module._cmd_draft(_session(db), _draft_args()) == 0
    message_id = db.tables["outreach_messages"][0]["id"]
    db.tables["outreach_messages"][0]["approval_status"] = "approved"

    with pytest.raises(Exception) as exc:
        module._cmd_approve(_session(db), argparse.Namespace(message_id=message_id, note=None, json=True))
    assert "approval_status" in str(exc.value)


def test_cli_authorize_records_only_the_decision(capsys):
    module = _cli()
    db = full_db()
    assert module._cmd_draft(_session(db), _draft_args()) == 0
    message_id = json.loads(capsys.readouterr().out)["message_id"]
    assert module._cmd_approve(
        _session(db), argparse.Namespace(message_id=message_id, note=None, json=True)
    ) == 0
    capsys.readouterr()

    assert module._cmd_authorize(_session(db), argparse.Namespace(message_id=message_id, json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "authorised"
    assert result["dispatch"] == "disabled"
    assert result["authorisation_activity_id"]

    stored = db.tables["outreach_messages"][0]
    assert stored["sending_status"] == "draft" and stored["sent_at"] is None
    activity = [row for row in db.tables["activities"] if row["metadata"].get("gate") == "G4"]
    assert len(activity) == 1
    assert "No message was transmitted" in activity[0]["description"]


def test_cli_authorize_refuses_an_unapproved_message():
    module = _cli()
    db = full_db()
    assert module._cmd_draft(_session(db), _draft_args()) == 0
    message_id = db.tables["outreach_messages"][0]["id"]

    with pytest.raises(Exception) as exc:
        module._cmd_authorize(_session(db), argparse.Namespace(message_id=message_id, json=True))
    assert "G3" in str(exc.value)


def test_cli_parser_requires_the_identifiers_the_gate_needs():
    module = _cli()
    parser = module.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["draft", "--company-id", COMPANY])
    args = parser.parse_args(["draft", "--company-id", COMPANY, "--contact-id", CONTACT])
    assert args.step == 1 and args.channel == "email" and args.campaign_id is None
