"""Outreach drafting gate: every rejection path, and the dispatch that cannot happen.

No database, no network, no credentials. The ports are implemented as small
fakes at the bottom of this file; the schema contract tests read
``crm/schema.sql`` as text so a made-up column or enum value fails here rather
than in production.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from contracts.keys import content_key
from outreach import dispatch as dispatch_module
from outreach.gate import (
    REJECTION_CODES,
    DraftRejected,
    DraftRequest,
    DraftRequestError,
    EligibilityDecision,
    RejectionCode,
    build_draft_payload,
    create_draft,
    draft_idempotency_key,
    evaluate_draft_eligibility,
    format_provenance,
    row_idempotency_key,
)
from outreach.ports import (
    COMMERCIAL_MODELS,
    OUTREACH_CHANNELS,
    QUALIFICATION_DIMENSIONS,
    DraftPersistenceError,
    QualificationRecord,
    SuppressionFinding,
    SuppressionReport,
)

REPO = Path(__file__).resolve().parent.parent
SCHEMA = (REPO / "crm" / "schema.sql").read_text(encoding="utf-8")

COMPANY = "11111111-1111-4111-8111-111111111111"
COMPANY_B = "11111111-1111-4111-8111-222222222222"
CONTACT = "22222222-2222-4222-8222-222222222222"
OTHER_CONTACT = "22222222-2222-4222-8222-333333333333"
REVIEWER = "99999999-9999-4999-8999-999999999999"
QUALIFICATION = "33333333-3333-4333-8333-333333333333"
OTHER_CAMPAIGN = "66666666-6666-4666-8666-666666666666"
SECOND_CAMPAIGN = "66666666-6666-4666-8666-777777777777"
AGENT = "drafter:hermes:outreach"


def finding_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def evidence(finding: str | None = None, text: str = "Documented client roster with three Webflow builds"):
    block = {"score": 12.0, "max": 20, "evidence": text, "confidence": "high"}
    if finding:
        block["finding_ids"] = [finding]
    return block


def approved_record(**over) -> QualificationRecord:
    dims = {key: evidence(finding_id(i)) for i, (key, _) in enumerate(QUALIFICATION_DIMENSIONS)}
    base = dict(
        qualification_id=QUALIFICATION,
        company_id=COMPANY,
        review_status="approved",
        qualification_tier="tier_2_prospective",
        overall_score=72.0,
        reviewed_by_human_id=REVIEWER,
        reviewed_at="2026-11-13T10:00:00+00:00",
        dimension_evidence=dims,
        disqualification_reasons=(),
        recommended_next_action="Invite to a scoping call",
    )
    base.update(over)
    return QualificationRecord(**base)


def valid_request(**over) -> DraftRequest:
    base = dict(
        company_id=COMPANY,
        contact_id=CONTACT,
        channel="email",
        outreach_event="initial_partnership_intro",
        subject="Wholesale development capacity for your Webflow projects",
        body=(
            "Hello,\n\nI build backend work behind design studios: APIs, auth and "
            "database-backed flows for Webflow and Framer builds. If a current "
            "project needs that layer, I can scope it as a fixed-scope engagement."
        ),
    )
    base.update(over)
    return DraftRequest(**base)


# ---------------------------------------------------------------------------
# Fakes — the ports, with failures the real ones can also produce
# ---------------------------------------------------------------------------


class FakeQualificationReader:
    def __init__(self, record=None, error=None):
        self.record = record
        self.error = error
        self.calls = []

    def get_qualification(self, company_id):
        self.calls.append(company_id)
        if self.error is not None:
            raise self.error
        return self.record


class FakeSuppressionReader:
    def __init__(self, report=None, error=None):
        self.report = report
        self.error = error
        self.calls = []

    def check(self, company_id, contact_id):
        self.calls.append((company_id, contact_id))
        if self.error is not None:
            raise self.error
        return self.report


class FakeDraftWriter:
    """Stores rows in memory; ``tamper`` simulates a writer that returns junk."""

    def __init__(self, agent_identity=AGENT, tamper=None, existing=None):
        self.agent_identity = agent_identity
        self.tamper = tamper
        self.existing = existing
        self.rows = []
        self.writes = 0

    def find_existing(self, idempotency_key):
        if self.existing is not None:
            return self.existing
        for row in self.rows:
            if row["idempotency_key"] == idempotency_key:
                return row
        return None

    def write(self, draft):
        self.writes += 1
        row = draft.to_row()
        row.update(
            {
                "id": f"44444444-4444-4444-8444-{self.writes:012d}",
                "approved_by_human_id": None,
                "approved_at": None,
                "sent_at": None,
                "idempotency_key": draft.idempotency_key,
            }
        )
        if self.tamper:
            row.update(self.tamper)
        self.rows.append(row)
        return row


def clean() -> SuppressionReport:
    return SuppressionReport(
        state="clear",
        sources_checked=(
            "contacts.is_opted_out",
            "companies.is_suppressed",
            "company_domains",
            "suppression_registry",
        ),
    )


_UNSET = object()


def evaluate(record=_UNSET, request=None, report=None, reader_error=None, suppression_error=None):
    if record is _UNSET:
        record = approved_record()
    return evaluate_draft_eligibility(
        request or valid_request(),
        FakeQualificationReader(record, error=reader_error),
        FakeSuppressionReader(clean() if report is None else report, error=suppression_error),
    )


# ---------------------------------------------------------------------------
# 1-4: qualification preconditions
# ---------------------------------------------------------------------------


def test_missing_qualification_is_rejected():
    decision = evaluate(record=None)
    assert decision.code is RejectionCode.QUALIFICATION_MISSING
    assert not decision.eligible


def test_pending_review_qualification_is_rejected():
    decision = evaluate(approved_record(review_status="pending_human_review"))
    assert decision.code is RejectionCode.QUALIFICATION_PENDING_REVIEW
    assert not decision.eligible


def test_draft_status_scorecard_is_not_approval():
    decision = evaluate(approved_record(review_status="draft", reviewed_by_human_id=None, reviewed_at=None))
    assert decision.code is RejectionCode.QUALIFICATION_PENDING_REVIEW


def test_disqualified_tier_is_rejected():
    decision = evaluate(
        approved_record(qualification_tier="disqualified", disqualification_reasons=("client_roster_is_agency_only",))
    )
    assert decision.code is RejectionCode.QUALIFICATION_DISQUALIFIED


def test_disqualification_reason_with_a_passing_tier_is_still_rejected():
    decision = evaluate(approved_record(disqualification_reasons=("hard_disqualifier",)))
    assert RejectionCode.QUALIFICATION_DISQUALIFIED in decision.codes


def test_approved_without_a_human_reviewer_is_not_approval():
    decision = evaluate(approved_record(reviewed_by_human_id=None, reviewed_at=None, needs_human_review=True))
    assert decision.code is RejectionCode.HUMAN_REVIEWER_MISSING


def test_approved_with_reviewer_but_no_timestamp_is_incomplete_provenance():
    decision = evaluate(approved_record(reviewed_at=None))
    assert RejectionCode.PROVENANCE_INCOMPLETE in decision.codes


@pytest.mark.parametrize("status", ["rejected", "something_else"])
def test_rejected_or_unknown_review_status_is_rejected(status):
    decision = evaluate(approved_record(review_status=status, reviewed_by_human_id=None, reviewed_at=None))
    assert decision.code is RejectionCode.QUALIFICATION_NOT_APPROVED


def test_unreadable_qualification_is_a_rejection_not_a_pass():
    decision = evaluate(reader_error=RuntimeError("PGRST301 JWT expired"))
    assert decision.code is RejectionCode.QUALIFICATION_LOOKUP_FAILED
    assert "RuntimeError" in decision.detail


def test_insufficient_evidence_scorecard_is_unmeasured():
    decision = evaluate(approved_record(insufficient_evidence=True))
    assert decision.code is RejectionCode.QUALIFICATION_UNMEASURED


# ---------------------------------------------------------------------------
# 5-8: suppression, including every way the answer can be missing
# ---------------------------------------------------------------------------


def test_suppressed_contact_is_rejected():
    report = SuppressionReport(
        state="suppressed",
        findings=(SuppressionFinding("contact_email", "hello@studio.example", "contacts.is_opted_out"),),
        detail="contacts.is_opted_out = true",
    )
    decision = evaluate(report=report)
    assert decision.code is RejectionCode.SUPPRESSED_CONTACT


def test_suppressed_domain_is_rejected_with_its_own_code():
    report = SuppressionReport(
        state="suppressed",
        findings=(SuppressionFinding("domain", "studio.example", "suppression_registry"),),
        detail="domain on the permanent ledger",
    )
    decision = evaluate(report=report)
    assert decision.code is RejectionCode.SUPPRESSED_DOMAIN


def test_suppressed_company_is_rejected_with_its_own_code():
    report = SuppressionReport(
        state="suppressed",
        findings=(SuppressionFinding("company", COMPANY, "companies.is_suppressed"),),
        detail="companies.is_suppressed = true",
    )
    decision = evaluate(report=report)
    assert decision.code is RejectionCode.SUPPRESSED_COMPANY


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("timeout talking to supabase"),
        TimeoutError("read timed out"),
        PermissionError("42501 permission denied for table suppression_registry"),
    ],
)
def test_suppression_lookup_failure_fails_closed(error):
    decision = evaluate(suppression_error=error)
    assert decision.code is RejectionCode.SUPPRESSION_LOOKUP_FAILED
    assert type(error).__name__ in decision.detail


def test_ambiguous_suppression_is_rejected():
    report = SuppressionReport(state="ambiguous", detail="two registry rows disagree after normalization")
    decision = evaluate(report=report)
    assert decision.code is RejectionCode.SUPPRESSION_AMBIGUOUS


def test_missing_suppression_report_is_not_clean():
    decision = evaluate_draft_eligibility(
        valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(None)
    )
    assert decision.code is RejectionCode.SUPPRESSION_LOOKUP_FAILED


def test_clear_verdict_carrying_findings_is_ambiguous():
    report = SuppressionReport(
        state="clear", findings=(SuppressionFinding("domain", "studio.example", "company_domains"),)
    )
    decision = evaluate(report=report)
    assert decision.code is RejectionCode.SUPPRESSION_AMBIGUOUS


def test_suppression_is_not_consulted_when_the_qualification_already_fails():
    reader = FakeSuppressionReader(clean())
    evaluate_draft_eligibility(valid_request(), FakeQualificationReader(None), reader)
    assert reader.calls == []


# ---------------------------------------------------------------------------
# 9-10: the happy path and idempotency
# ---------------------------------------------------------------------------


def test_valid_approved_qualification_plus_clean_suppression_creates_a_draft_with_provenance():
    writer = FakeDraftWriter()
    outcome = create_draft(
        valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer
    )

    assert outcome.status == "created"
    assert outcome.created
    assert len(writer.rows) == 1

    row = writer.rows[0]
    assert row["company_id"] == COMPANY
    assert row["contact_id"] == CONTACT
    assert row["channel"] == "email"
    assert row["proposed_commercial_model"] == "project_based_wholesale"
    assert row["approval_status"] == "draft"
    assert row["sending_status"] == "draft"
    assert row["approved_by_human_id"] is None and row["approved_at"] is None and row["sent_at"] is None
    assert row["drafted_by_agent_identity"] == AGENT

    # Provenance: score, tier, reviewer, evidence, rubric version.
    provenance = outcome.payload.provenance
    assert provenance["qualification_id"] == QUALIFICATION
    assert provenance["overall_score"] == 72.0
    assert provenance["qualification_tier"] == "tier_2_prospective"
    assert provenance["reviewed_by_human_id"] == REVIEWER
    assert provenance["reviewed_at"] == "2026-11-13T10:00:00+00:00"
    assert provenance["rubric_version"] == "2.0.1"
    assert provenance["evidence_coverage"] == 1.0
    assert len(provenance["evidence_refs"]) == len(QUALIFICATION_DIMENSIONS)

    # The rationale column is the audit trail, because the schema has no
    # provenance column.
    rationale = row["personalization_rationale"]
    assert QUALIFICATION in rationale
    assert REVIEWER in rationale
    assert "rubric_version=2.0.1" in rationale
    assert row["personalization_evidence_ids"] == list(provenance["evidence_refs"])


def test_repeated_identical_request_does_not_create_a_second_draft():
    writer = FakeDraftWriter()
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())

    first = create_draft(valid_request(), qualified, suppression, writer)
    second = create_draft(valid_request(), qualified, suppression, writer)

    assert first.status == "created"
    assert second.status == "already_exists"
    assert second.draft_id == first.draft_id
    assert writer.writes == 1
    assert len(writer.rows) == 1


def test_the_same_event_for_a_second_contact_is_still_one_draft():
    """The key is the slot, not the recipient: one message per slot."""
    writer = FakeDraftWriter()
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())

    create_draft(valid_request(), qualified, suppression, writer)
    again = create_draft(valid_request(contact_id=OTHER_CONTACT), qualified, suppression, writer)

    assert again.status == "already_exists"
    assert "different contact" in again.detail
    assert writer.writes == 1


def test_a_different_sequence_step_is_a_new_draft():
    writer = FakeDraftWriter()
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())

    create_draft(valid_request(), qualified, suppression, writer)
    outcome = create_draft(valid_request(sequence_step=2), qualified, suppression, writer)

    assert outcome.status == "created"
    assert writer.writes == 2


def test_a_different_event_in_the_same_slot_is_refused_not_duplicated():
    """Explicit decision: `outreach_event` has no column, so it cannot be part of
    the key a writer recomputes from a row. Two events aimed at the same step of
    the same campaign are therefore one slot, and the second is refused (a human
    moves it to another step) rather than duplicated."""
    writer = FakeDraftWriter()
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())

    create_draft(valid_request(), qualified, suppression, writer)
    outcome = create_draft(valid_request(outreach_event="follow_up_after_silence"), qualified, suppression, writer)

    assert outcome.status == "already_exists"
    assert writer.writes == 1


def test_a_different_campaign_is_a_new_draft():
    writer = FakeDraftWriter()
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())

    create_draft(valid_request(campaign_id=OTHER_CAMPAIGN), qualified, suppression, writer)
    outcome = create_draft(valid_request(campaign_id=SECOND_CAMPAIGN), qualified, suppression, writer)

    assert outcome.status == "created"
    assert writer.writes == 2


def test_idempotency_key_uses_the_shared_content_addressed_helper():
    request = valid_request()
    expected = content_key(
        {
            "company_id": COMPANY,
            "campaign_id": None,
            "channel": "email",
            "sequence_step": 1,
        }
    )
    assert draft_idempotency_key(request) == expected
    # Body text is not part of the identity of the event.
    assert draft_idempotency_key(valid_request(body="A completely different message")) == expected


def test_the_idempotency_key_can_be_recomputed_from_the_persisted_row():
    """The dedupe key must survive a round-trip: the deployed table has no
    idempotency column and no outreach_event column, so a real writer can only
    hash the row's own columns."""
    payload = build_draft_payload(
        valid_request(),
        EligibilityDecision(qualification=approved_record(), evidence_refs=approved_record().evidence_refs),
        agent_identity=AGENT,
    )
    row = payload.to_row()
    assert "idempotency_key" not in row and "outreach_event" not in row
    assert row_idempotency_key(row) == draft_idempotency_key(valid_request())
    assert row_idempotency_key({**row, "sequence_step": 2}) != draft_idempotency_key(valid_request())
    # The event is still recorded, in the only text column there is.
    assert "outreach_event=initial_partnership_intro" in row["personalization_rationale"]


def test_two_writers_can_both_write_the_same_slot_until_a_unique_index_exists():
    """Accepted risk (S2): dedupe is a read-then-write in the caller, so two
    writers that do not share state both pass `find_existing` and both insert.
    This test pins the limitation so it cannot be mistaken for a guarantee."""
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())
    first, second = FakeDraftWriter(), FakeDraftWriter()

    assert create_draft(valid_request(), qualified, suppression, first).status == "created"
    assert create_draft(valid_request(), qualified, suppression, second).status == "created"
    assert len(first.rows) + len(second.rows) == 2


# ---------------------------------------------------------------------------
# 11: draft creation can never dispatch
# ---------------------------------------------------------------------------


def test_dispatch_raises_rather_than_returning():
    with pytest.raises(dispatch_module.DispatchDisabled):
        dispatch_module.dispatch(object())
    with pytest.raises(dispatch_module.DispatchDisabled):
        dispatch_module.queue_for_dispatch(object())
    with pytest.raises(dispatch_module.DispatchDisabled):
        dispatch_module.DispatchChannel()
    assert dispatch_module.DISPATCH_AVAILABLE is False


def test_gate_does_not_reference_the_dispatch_module():
    import outreach.gate as gate_module

    tree = ast.parse(Path(gate_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("dispatch" in name for name in imported), imported
    # The only package-level names bound in the gate are its ports and the
    # shared contracts; a dispatch handle would have to appear here.
    assert not any("Dispatch" in name for name in gate_module.__dict__)


def test_creating_a_draft_never_imports_the_dispatch_module():
    """Proved in a fresh interpreter: after a successful draft, dispatch is absent."""
    script = """
import sys

from outreach.gate import DraftRequest, create_draft
from outreach.ports import QUALIFICATION_DIMENSIONS, QualificationRecord, SuppressionReport

dims = {
    key: {"score": 5.0, "evidence": "documented", "finding_ids": ["00000000-0000-4000-8000-000000000001"]}
    for key, _ in QUALIFICATION_DIMENSIONS
}
record = QualificationRecord(
    qualification_id="33333333-3333-4333-8333-333333333333",
    company_id="11111111-1111-4111-8111-111111111111",
    review_status="approved",
    qualification_tier="tier_3_deferred",
    overall_score=45.0,
    reviewed_by_human_id="99999999-9999-4999-8999-999999999999",
    reviewed_at="2026-11-13T10:00:00+00:00",
    dimension_evidence=dims,
)


class Reader:
    def get_qualification(self, company_id):
        return record


class Suppression:
    def check(self, company_id, contact_id):
        return SuppressionReport(
            state="clear",
            sources_checked=("contacts.is_opted_out", "companies.is_suppressed",
                             "company_domains", "suppression_registry"),
        )


class Writer:
    agent_identity = "drafter:subprocess"

    def find_existing(self, key):
        return None

    def write(self, draft):
        row = draft.to_row()
        row.update({"id": "44444444-4444-4444-8444-000000000001",
                    "approved_by_human_id": None, "approved_at": None, "sent_at": None})
        return row


outcome = create_draft(
    DraftRequest(
        company_id="11111111-1111-4111-8111-111111111111",
        contact_id="22222222-2222-4222-8222-222222222222",
        channel="email",
        outreach_event="initial_partnership_intro",
        subject="Backend support for your studio",
        body="A short note about backend work behind design builds.",
    ),
    Reader(),
    Suppression(),
    Writer(),
)
assert outcome.created, outcome
TRANSPORT = ("outreach.dispatch", "socket", "ssl", "http", "http.client", "urllib3",
             "requests", "httpx", "aiohttp", "smtplib", "email", "ftplib")
reached = [module for module in TRANSPORT if module in sys.modules]
if reached:
    print("reached:" + ",".join(reached))
    raise SystemExit(0)
print("clean")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO), capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "clean", completed.stdout + completed.stderr


def test_dispatch_module_has_no_transport_and_no_credentials():
    path = Path(dispatch_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"requests", "smtplib", "socket", "http", "urllib", "aiohttp", "httplib2", "os", "subprocess"}
    assert not (imported & forbidden), imported & forbidden

    source = path.read_text(encoding="utf-8").lower()
    for needle in ("api_key", "apikey", "token", "password", "base_url", "smtp_host"):
        assert needle not in source


# ---------------------------------------------------------------------------
# 12: provenance and evidence
# ---------------------------------------------------------------------------


def test_scorecard_without_any_evidence_is_unmeasured_not_deferred():
    dims = {key: {"score": 0.0} for key, _ in QUALIFICATION_DIMENSIONS}
    decision = evaluate(approved_record(dimension_evidence=dims, overall_score=0.0))
    assert RejectionCode.QUALIFICATION_UNMEASURED in decision.codes
    assert not decision.eligible


def test_evidence_without_finding_ids_is_incomplete_provenance():
    """Coverage exists (prose) but no finding id is cited: the draft has no trail."""
    dims = {key: evidence(text="documented in prose only") for key, _ in QUALIFICATION_DIMENSIONS}
    decision = evaluate(approved_record(dimension_evidence=dims))
    assert decision.code is RejectionCode.PROVENANCE_INCOMPLETE
    assert "finding ids" in decision.detail


def test_payload_refuses_to_build_from_a_rejected_decision():
    decision = evaluate(record=None)
    with pytest.raises(DraftRejected):
        build_draft_payload(valid_request(), decision, agent_identity=AGENT)


def test_non_uuid_evidence_refs_cannot_produce_an_empty_evidence_array():
    """personalization_evidence_ids is UUID[] and is the only structured
    provenance a row carries. If every cited ref is a fabricated non-UUID
    string, the array would be empty: that is prose-only provenance, refused."""
    dims = {key: evidence(text="screenshot") | {"finding_ids": ["finding-abc"]} for key, _ in QUALIFICATION_DIMENSIONS}
    record = approved_record(dimension_evidence=dims)
    with pytest.raises(DraftRejected) as raised:
        build_draft_payload(
            valid_request(),
            EligibilityDecision(qualification=record, evidence_refs=record.evidence_refs),
            agent_identity=AGENT,
        )
    assert RejectionCode.PROVENANCE_INCOMPLETE in raised.value.codes
    assert "UUID" in raised.value.detail


def test_evidence_array_carries_exactly_the_cited_finding_ids():
    record = approved_record()
    payload = build_draft_payload(
        valid_request(),
        EligibilityDecision(qualification=record, evidence_refs=record.evidence_refs),
        agent_identity=AGENT,
    )
    cited = {ref for dim in record.dimension_evidence.values() for ref in dim.get("finding_ids", ())}
    assert payload.personalization_evidence_ids  # non-empty
    assert set(payload.personalization_evidence_ids) == cited
    assert len(payload.personalization_evidence_ids) == len(cited)  # deduplicated


def test_a_forged_decision_cannot_carry_prohibited_text_past_the_payload_builder():
    """`EligibilityDecision` is a plain dataclass: `EligibilityDecision(qualification=...)`
    has no codes and therefore claims to be eligible. The payload builder must
    re-derive the content guards instead of trusting the flag."""
    record = approved_record()
    forged = EligibilityDecision(qualification=record)
    with pytest.raises(DraftRejected) as raised:
        build_draft_payload(
            valid_request(body="Happy to discuss a revenue share on the next project."),
            forged,
            agent_identity=AGENT,
        )
    assert RejectionCode.REVENUE_SHARING_PROHIBITED in raised.value.codes


def test_a_forged_decision_cannot_carry_an_unapproved_scorecard_past_the_builder():
    worst = approved_record(review_status="draft", reviewed_by_human_id=None, reviewed_at=None)
    with pytest.raises(DraftRejected) as raised:
        build_draft_payload(valid_request(), EligibilityDecision(qualification=worst), agent_identity=AGENT)
    assert RejectionCode.QUALIFICATION_PENDING_REVIEW in raised.value.codes


def test_a_forged_decision_cannot_borrow_another_companys_scorecard():
    borrowed = approved_record(company_id=COMPANY_B)
    with pytest.raises(DraftRejected) as raised:
        build_draft_payload(valid_request(), EligibilityDecision(qualification=borrowed), agent_identity=AGENT)
    assert RejectionCode.QUALIFICATION_COMPANY_MISMATCH in raised.value.codes


def test_provenance_never_states_a_prospect_fact_it_does_not_have():
    text = format_provenance(
        {
            "qualification_id": QUALIFICATION,
            "company_id": COMPANY,
            "overall_score": 72.0,
            "qualification_tier": "tier_2_prospective",
            "reviewed_by_human_id": REVIEWER,
            "reviewed_at": "2026-11-13T10:00:00+00:00",
            "rubric_version": "2.0.1",
            "evidence_coverage": 0.714,
            "evidence_refs": [],
        }
    )
    assert "revenue" not in text.lower()
    assert "team" not in text.lower()
    assert "clients" not in text.lower()


# ---------------------------------------------------------------------------
# Draft content guards
# ---------------------------------------------------------------------------


def test_revenue_sharing_commercial_model_is_rejected():
    decision = evaluate(approved_record(), request=valid_request(proposed_commercial_model="strategic_revenue_sharing"))
    assert decision.code is RejectionCode.REVENUE_SHARING_PROHIBITED


def test_revenue_sharing_in_the_body_is_rejected():
    decision = evaluate(
        approved_record(),
        request=valid_request(body="Happy to discuss a revenue share arrangement on future projects."),
    )
    assert decision.code is RejectionCode.REVENUE_SHARING_PROHIBITED


def test_monthly_reserved_capacity_is_rejected_as_premature():
    decision = evaluate(
        approved_record(), request=valid_request(proposed_commercial_model="monthly_reserved_capacity")
    )
    assert decision.code is RejectionCode.COMMERCIAL_MODEL_PREMATURE


@pytest.mark.parametrize(
    "body",
    [
        "Our team is ready to start whenever you are.",
        "We have delivered twenty Webflow migrations.",
        "We can offer a team of 6 developers.",
        "Dedicated capacity is available this month.",
    ],
)
def test_unsupported_delivery_claims_are_rejected(body):
    decision = evaluate(approved_record(), request=valid_request(body=body))
    assert RejectionCode.UNSUPPORTED_DELIVERY_CLAIM in decision.codes


def test_empty_body_is_rejected_before_any_lookup():
    qualification_reader = FakeQualificationReader(approved_record())
    suppression_reader = FakeSuppressionReader(clean())
    decision = evaluate_draft_eligibility(
        valid_request(body="   "), qualification_reader, suppression_reader
    )
    assert decision.code is RejectionCode.DRAFT_CONTENT_INVALID
    assert qualification_reader.calls == [] and suppression_reader.calls == []


def test_every_rejection_code_is_in_the_closed_set():
    assert set(REJECTION_CODES) == {code.value for code in RejectionCode}
    samples = [
        evaluate(record=None),
        evaluate(approved_record(review_status="pending_human_review")),
        evaluate(approved_record(qualification_tier="disqualified")),
        evaluate(approved_record(reviewed_by_human_id=None, needs_human_review=True)),
        evaluate(report=SuppressionReport(state="ambiguous")),
        evaluate(reader_error=RuntimeError("boom")),
        evaluate(approved_record(), request=valid_request(body="")),
    ]
    for decision in samples:
        assert decision.codes
        for code in decision.codes:
            assert code.value in REJECTION_CODES


def test_every_rejection_code_is_produced_by_at_least_one_scenario():
    """The closed set is only closed if each code is reachable; nothing is decorative."""
    scenarios = {
        RejectionCode.QUALIFICATION_MISSING: lambda: evaluate(record=None),
        RejectionCode.QUALIFICATION_LOOKUP_FAILED: lambda: evaluate(reader_error=RuntimeError("jwt")),
        RejectionCode.QUALIFICATION_UNMEASURED: lambda: evaluate(approved_record(insufficient_evidence=True)),
        RejectionCode.QUALIFICATION_DISQUALIFIED: lambda: evaluate(
            approved_record(qualification_tier="disqualified")
        ),
        RejectionCode.QUALIFICATION_PENDING_REVIEW: lambda: evaluate(
            approved_record(review_status="pending_human_review")
        ),
        RejectionCode.QUALIFICATION_NOT_APPROVED: lambda: evaluate(approved_record(review_status="rejected")),
        RejectionCode.HUMAN_REVIEWER_MISSING: lambda: evaluate(
            approved_record(reviewed_by_human_id=None, reviewed_at=None)
        ),
        RejectionCode.QUALIFICATION_COMPANY_MISMATCH: lambda: evaluate(approved_record(company_id=COMPANY_B)),
        RejectionCode.QUALIFICATION_TIER_INCONSISTENT: lambda: evaluate(
            approved_record(qualification_tier="tier_1_priority")
        ),
        RejectionCode.SUPPRESSED_CONTACT: lambda: evaluate(
            report=SuppressionReport(
                state="suppressed", findings=(SuppressionFinding("contact_email", "a@b.example", "registry"),)
            )
        ),
        RejectionCode.SUPPRESSED_DOMAIN: lambda: evaluate(
            report=SuppressionReport(
                state="suppressed", findings=(SuppressionFinding("domain", "b.example", "registry"),)
            )
        ),
        RejectionCode.SUPPRESSED_COMPANY: lambda: evaluate(
            report=SuppressionReport(
                state="suppressed", findings=(SuppressionFinding("company", COMPANY, "companies"),)
            )
        ),
        RejectionCode.SUPPRESSION_LOOKUP_FAILED: lambda: evaluate(suppression_error=TimeoutError("t")),
        RejectionCode.SUPPRESSION_AMBIGUOUS: lambda: evaluate(report=SuppressionReport(state="ambiguous")),
        RejectionCode.PROVENANCE_INCOMPLETE: lambda: evaluate(
            approved_record(dimension_evidence={k: evidence() for k, _ in QUALIFICATION_DIMENSIONS})
        ),
        RejectionCode.DRAFT_CONTENT_INVALID: lambda: evaluate(request=valid_request(body="")),
        RejectionCode.REVENUE_SHARING_PROHIBITED: lambda: evaluate(
            request=valid_request(proposed_commercial_model="strategic_revenue_sharing")
        ),
        RejectionCode.UNSUPPORTED_DELIVERY_CLAIM: lambda: evaluate(
            request=valid_request(body="Our team can start on Monday.")
        ),
        RejectionCode.COMMERCIAL_MODEL_PREMATURE: lambda: evaluate(
            request=valid_request(proposed_commercial_model="monthly_reserved_capacity")
        ),
    }
    assert set(scenarios) == set(RejectionCode), set(RejectionCode) - set(scenarios)
    for code, make in scenarios.items():
        assert code in make().codes, f"{code} not reachable"


# ---------------------------------------------------------------------------
# Structural requests and writer integrity
# ---------------------------------------------------------------------------


def test_structure_is_validated_at_construction_not_hidden_in_a_decision():
    with pytest.raises(DraftRequestError):
        valid_request(channel="carrier_pigeon")
    with pytest.raises(DraftRequestError):
        valid_request(company_id="")
    with pytest.raises(DraftRequestError):
        valid_request(proposed_commercial_model="something_new")
    with pytest.raises(DraftRequestError):
        valid_request(sequence_step=0)


def test_the_writer_identity_wins_over_any_caller_supplied_name():
    writer = FakeDraftWriter(agent_identity="drafter:verified")
    create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)
    assert writer.rows[0]["drafted_by_agent_identity"] == "drafter:verified"


def test_a_writer_that_returns_an_approved_or_sent_row_is_an_integrity_failure():
    writer = FakeDraftWriter(tamper={"sending_status": "sent", "sent_at": "2026-11-13T11:00:00+00:00"})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


def test_a_writer_that_returns_an_approved_row_is_an_integrity_failure():
    writer = FakeDraftWriter(tamper={"approval_status": "approved", "approved_at": "2026-11-13T11:00:00+00:00"})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


def test_a_writer_without_an_identity_cannot_be_used():
    writer = FakeDraftWriter(agent_identity="")
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


# ---------------------------------------------------------------------------
# Contract: the payload and the enums are the deployed schema's, not ours
# ---------------------------------------------------------------------------


def table_columns(table: str) -> set[str]:
    match = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", SCHEMA, re.S)
    assert match, f"{table} not found in crm/schema.sql"
    body = match.group(1)
    columns = set()
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("CONSTRAINT", "--", ")")):
            continue
        name = re.match(r"([a-z][a-z0-9_]*)\s", stripped)
        if name:
            columns.add(name.group(1))
    return columns


def enum_values(type_name: str) -> list[str]:
    match = re.search(rf"CREATE TYPE {type_name} AS ENUM \((.*?)\n\);", SCHEMA, re.S)
    assert match, f"{type_name} not found in crm/schema.sql"
    return re.findall(r"'([^']+)'", match.group(1))


def test_draft_payload_only_writes_real_outreach_columns():
    columns = table_columns("outreach_messages")
    payload = build_draft_payload(
        valid_request(),
        EligibilityDecision(qualification=approved_record(), evidence_refs=approved_record().evidence_refs),
        agent_identity=AGENT,
    )
    assert set(payload.to_row()) <= columns, set(payload.to_row()) - columns
    # The columns that only a later gate (or the database) may touch.
    assert {"sent_at", "approved_at", "approved_by_human_id", "sending_status"} <= columns


def test_outreach_enums_match_the_deployed_types():
    assert list(OUTREACH_CHANNELS) == enum_values("outreach_channel_type")
    assert list(COMMERCIAL_MODELS) == enum_values("commercial_model_type")


def test_qualification_tiers_and_statuses_match_the_deployed_types():
    tiers = enum_values("qualification_tier_type")
    statuses = enum_values("qualification_review_status_type")
    assert approved_record().qualification_tier in tiers
    assert approved_record().review_status in statuses


def test_qualification_record_reads_real_columns():
    columns = table_columns("lead_qualifications")
    used = {
        "id",
        "company_id",
        "review_status",
        "qualification_tier",
        "overall_score",
        "reviewed_by_human_id",
        "reviewed_at",
        "dimension_evidence",
        "disqualification_reasons",
        "recommended_next_action",
    }
    assert used <= columns, used - columns
    record = QualificationRecord.from_row(
        {
            "id": QUALIFICATION,
            "company_id": COMPANY,
            "review_status": "approved",
            "qualification_tier": "tier_1_priority",
            "overall_score": "88.50",
            "reviewed_by_human_id": REVIEWER,
            "reviewed_at": "2026-11-13T10:00:00+00:00",
            "dimension_evidence": {},
            "disqualification_reasons": [],
            "recommended_next_action": "Book a call",
        }
    )
    assert record.overall_score == 88.5
    assert record.review_status_known and record.tier_known
    assert record.has_human_reviewer


# ---------------------------------------------------------------------------
# Adversarial regressions (reports/eryndex/outreach-adversarial-review.md)
# ---------------------------------------------------------------------------


def test_qualification_read_for_another_company_is_rejected():
    """Attack: answer company B's request with company A's approved scorecard."""
    reader = FakeQualificationReader(approved_record(company_id=COMPANY_B))
    decision = evaluate_draft_eligibility(valid_request(company_id=COMPANY), reader, FakeSuppressionReader(clean()))
    assert RejectionCode.QUALIFICATION_COMPANY_MISMATCH in decision.codes
    assert not decision.eligible
    assert reader.calls == [COMPANY]


def test_a_tier_the_score_does_not_earn_is_rejected():
    """Attack: forge a high tier to look like a priority lead."""
    decision = evaluate(approved_record(qualification_tier="tier_1_priority", overall_score=45.0))
    assert RejectionCode.QUALIFICATION_TIER_INCONSISTENT in decision.codes
    assert not decision.eligible


def test_needs_human_review_is_not_overruled_by_a_reviewer_id():
    decision = evaluate(approved_record(needs_human_review=True))
    assert decision.code is RejectionCode.QUALIFICATION_PENDING_REVIEW
    assert not decision.eligible


def test_qualification_reader_returning_a_row_is_a_rejection_not_a_crash():
    class RowReader:
        def get_qualification(self, company_id):
            return {"id": QUALIFICATION, "company_id": COMPANY, "review_status": "approved"}

    decision = evaluate_draft_eligibility(valid_request(), RowReader(), FakeSuppressionReader(clean()))
    assert decision.code is RejectionCode.QUALIFICATION_LOOKUP_FAILED
    assert "dict" in decision.detail


def test_suppression_reader_returning_a_row_is_a_rejection_not_a_crash():
    class RowSuppression:
        def check(self, company_id, contact_id):
            return {"state": "clear"}

    decision = evaluate_draft_eligibility(valid_request(), FakeQualificationReader(approved_record()), RowSuppression())
    assert decision.code is RejectionCode.SUPPRESSION_LOOKUP_FAILED
    assert "dict" in decision.detail


def test_clear_without_naming_a_source_is_not_clean():
    """Attack: return an empty 'clear' so an unavailable service looks clean."""
    decision = evaluate(report=SuppressionReport(state="clear"))
    assert decision.code is RejectionCode.SUPPRESSION_AMBIGUOUS
    assert "source" in decision.detail
    assert not decision.eligible


def test_a_suppression_lookup_that_exits_the_process_does_not_become_clean():
    class Exiting:
        def check(self, company_id, contact_id):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        evaluate_draft_eligibility(valid_request(), FakeQualificationReader(approved_record()), Exiting())


def test_base_exception_is_not_swallowed_into_a_decision():
    class Exiting:
        def get_qualification(self, company_id):
            raise SystemExit(2)

    with pytest.raises(SystemExit):
        evaluate_draft_eligibility(valid_request(), Exiting(), FakeSuppressionReader(clean()))


@pytest.mark.parametrize(
    "body",
    [
        "Happy to discuss a revenue\u2011share arrangement.",  # U+2011 non-breaking hyphen
        "Happy to discuss a revenue\u200bsharing arrangement.",  # zero-width space
        "Happy to discuss a r\u0435venue sharing arrangement.",  # Cyrillic е
        "Happy to discuss a revenue\u2014share arrangement.",  # em dash
        "\uff52\uff45\uff56\uff45\uff4e\uff55\uff45 sharing is fine",  # full-width
        "We can do a revenue split on the next project.",
        "We take a share of revenue only when it ships.",
        "We take a percentage of the revenue.",
        "Happy to split the revenue 50/50.",
        "A profit share would work for us.",
    ],
)
def test_revenue_sharing_obfuscation_is_still_rejected(body):
    decision = evaluate(approved_record(), request=valid_request(body=body))
    assert RejectionCode.REVENUE_SHARING_PROHIBITED in decision.codes


@pytest.mark.parametrize(
    "body",
    [
        "Our client Acme loved the build.",
        "We built the storefront for Acme last year.",
        "We delivered three Webflow migrations.",
        "We can start immediately on your next brief.",
        "We have ten developers on the bench.",
        "Our engineering bench absorbs overflow.",
        "Delivery is guaranteed.",
    ],
)
def test_delivery_and_client_claims_it_cannot_support_are_rejected(body):
    decision = evaluate(approved_record(), request=valid_request(body=body))
    assert RejectionCode.UNSUPPORTED_DELIVERY_CLAIM in decision.codes


@pytest.mark.parametrize(
    "subject",
    [
        "Quick note\r\nBcc: victim@example.com",
        "Quick note\nBcc: victim@example.com",
        "Quick note\x00",
        "Quick note\x1b[31m",
    ],
)
def test_subject_cannot_carry_a_header_break_or_control_character(subject):
    decision = evaluate(approved_record(), request=valid_request(subject=subject))
    assert decision.code is RejectionCode.DRAFT_CONTENT_INVALID
    assert "header injection" in decision.detail or "control character" in decision.detail


def test_body_allows_newlines_but_not_other_control_characters():
    ok = evaluate(approved_record(), request=valid_request(body="Hello,\r\n\r\nA normal note.\nThanks."))
    assert ok.eligible

    bad = evaluate(approved_record(), request=valid_request(body="Hello\x00world"))
    assert bad.code is RejectionCode.DRAFT_CONTENT_INVALID


def test_a_writer_cannot_rewrite_the_body_after_the_guard():
    writer = FakeDraftWriter(tamper={"body": "I build backend layers. Revenue sharing available on request."})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


def test_a_writer_cannot_substitute_the_recipient_after_the_guard():
    writer = FakeDraftWriter(tamper={"contact_id": OTHER_CONTACT})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


def test_a_writer_cannot_drop_the_evidence_array():
    writer = FakeDraftWriter(tamper={"personalization_evidence_ids": []})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


def test_an_existing_row_from_another_company_is_an_integrity_failure():
    """Attack: make the gate report 'already_exists' from a foreign row, so no
    draft is ever written for the requested company."""
    writer = FakeDraftWriter(existing={"id": "55555555-5555-4555-8555-555555555555", "company_id": COMPANY_B,
                                       "contact_id": CONTACT, "channel": "email", "campaign_id": None,
                                       "sequence_step": 1})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)
    assert writer.writes == 0


def test_an_existing_row_that_does_not_hash_to_the_key_is_an_integrity_failure():
    writer = FakeDraftWriter(existing={"id": "55555555-5555-4555-8555-555555555555", "company_id": COMPANY,
                                       "contact_id": CONTACT, "channel": "email", "campaign_id": None,
                                       "sequence_step": 7})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


def test_an_existing_row_without_identity_columns_is_an_integrity_failure():
    writer = FakeDraftWriter(existing={"id": "55555555-5555-4555-8555-555555555555", "company_id": COMPANY})
    with pytest.raises(DraftPersistenceError):
        create_draft(valid_request(), FakeQualificationReader(approved_record()), FakeSuppressionReader(clean()), writer)


def test_duplicate_and_reordered_requests_converge_on_one_row():
    writer = FakeDraftWriter()
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())

    first = create_draft(valid_request(), qualified, suppression, writer)
    second = create_draft(valid_request(), qualified, suppression, writer)
    third = create_draft(valid_request(sequence_step=2), qualified, suppression, writer)
    fourth = create_draft(valid_request(sequence_step=2), qualified, suppression, writer)
    again = create_draft(valid_request(), qualified, suppression, writer)

    assert [o.status for o in (first, second, third, fourth, again)] == [
        "created",
        "already_exists",
        "created",
        "already_exists",
        "already_exists",
    ]
    assert writer.writes == 2
    assert all(o.draft_id for o in (first, second, third, fourth, again))


def test_a_rejection_never_echoes_the_draft_text():
    """The body may carry anything an attacker sends; a rejection detail is
    logged by callers, so it must describe the rule, not repeat the message."""
    body = "Secret customer list: Acme, Globex. Happy to discuss a revenue share."
    decision = evaluate(approved_record(), request=valid_request(body=body))
    assert decision.codes
    assert "Acme" not in decision.detail
    assert "Secret" not in decision.detail


def test_dispatch_cannot_be_turned_into_a_sender():
    """Every entry point raises, including on a subclass that skips __init__ and
    on a module whose flag has been flipped."""
    class SkipInit(dispatch_module.DispatchChannel):
        def __init__(self):  # deliberately does not call super()
            pass

    for call in (
        lambda: SkipInit().send("x"),
        lambda: SkipInit().queue("x"),
        lambda: SkipInit().schedule("x"),
        lambda: SkipInit().credentials(),
        lambda: dispatch_module.dispatch("x"),
        lambda: dispatch_module.queue_for_dispatch("x"),
    ):
        with pytest.raises(dispatch_module.DispatchDisabled):
            call()

    dispatch_module.DISPATCH_AVAILABLE = True  # the flag is not a switch
    try:
        with pytest.raises(dispatch_module.DispatchDisabled):
            dispatch_module.dispatch("x")
    finally:
        dispatch_module.DISPATCH_AVAILABLE = False

    public = {name for name in vars(dispatch_module) if not name.startswith("_")}
    assert not public & {"send", "sender", "client", "smtp", "transport", "session"}


def test_a_provider_error_is_reported_without_its_credential():
    """Attack: make the adapter's error message land in a logged decision detail."""
    key = "sk-" + "live" + "-" + "DEADBEEF"
    bearer = "eyJhbGciOiJIUzI1NiJ9" + "." + "payload" + "." + "signature"
    scheme = "Bea" + "rer"  # split so a secret scanner does not rewrite this file
    error = RuntimeError(
        "GET https://proj.supabase.co/rest/v1/suppression_registry?select=*&apikey=" + key
        + " Authorization: " + scheme + " " + bearer + " failed"
    )
    decision = evaluate(suppression_error=error)
    assert decision.code is RejectionCode.SUPPRESSION_LOOKUP_FAILED
    assert "RuntimeError" in decision.detail  # still diagnosable
    assert key not in decision.detail
    assert bearer not in decision.detail
    assert "apikey=***" in decision.detail


def test_a_payload_cannot_be_constructed_for_the_later_commercial_model():
    """Only the entry model is draftable; the payload class refuses the rest even
    when a caller builds it directly instead of going through the gate."""
    from outreach.ports import DraftPayload

    for model in ("monthly_reserved_capacity", "strategic_revenue_sharing"):
        with pytest.raises(ValueError):
            DraftPayload(
                company_id=COMPANY,
                contact_id=CONTACT,
                channel="email",
                subject="s",
                body="b",
                personalization_rationale="r",
                proposed_commercial_model=model,
            )

    with pytest.raises(ValueError):
        DraftPayload(company_id=COMPANY, contact_id=CONTACT, channel="email", subject="s",
                     body="  ", personalization_rationale="r")


def test_an_exception_whose_str_raises_is_still_a_rejection():
    class Hostile(Exception):
        def __str__(self):
            raise RuntimeError("no")

    decision = evaluate(reader_error=Hostile())
    assert decision.code is RejectionCode.QUALIFICATION_LOOKUP_FAILED
    assert "Hostile" in decision.detail


def test_importing_the_gate_does_not_import_dispatch_or_transport():
    script = """
import sys
import outreach.gate  # noqa: F401
reached = [m for m in ("outreach.dispatch", "socket", "ssl", "http.client", "requests",
                       "httpx", "urllib3", "smtplib") if m in sys.modules]
print("reached:" + ",".join(reached) if reached else "clean")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO), capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "clean", completed.stdout + completed.stderr


# ---------------------------------------------------------------------------
# Residual defects found by independent verification (security-closure-matrix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "\u041eur team can absorb the overflow.",        # uppercase Cyrillic O
        "\u0422en developers are on the bench.",         # uppercase Cyrillic T
        "\u0420rofit share would work for us.",          # uppercase Cyrillic R (looks like P)
        "We can st\u0430rt immediately.",                # lowercase Cyrillic a
    ],
)
def test_uppercase_and_lowercase_cyrillic_homoglyphs_do_not_defeat_the_guards(body):
    """``casefold`` maps uppercase Cyrillic onto lowercase Cyrillic, so the
    confusable table must run *after* case folding, not before: folding it first
    left ``Оur team`` and ``Тen developers`` unmatched by every pattern."""
    decision = evaluate(approved_record(), request=valid_request(body=body))
    assert decision.codes
    assert {
        RejectionCode.UNSUPPORTED_DELIVERY_CLAIM,
        RejectionCode.REVENUE_SHARING_PROHIBITED,
    } & set(decision.codes)


@pytest.mark.parametrize(
    "body",
    [
        "A revenue\u180eshare would work.",      # U+180E Mongolian vowel separator (Cf)
        "A revenue\u2061share would work.",      # U+2061 invisible function application (Cf)
        "A rev\u2063enue share would work.",     # U+2063 invisible separator (Cf)
        "A pro\u180efit share would work.",
    ],
)
def test_unknown_invisible_format_characters_do_not_defeat_the_guards(body):
    """The fixed invisible-character list is not the whole of Unicode category
    ``Cf``; normalization strips the category, not a hand-written subset."""
    decision = evaluate(approved_record(), request=valid_request(body=body))
    assert RejectionCode.REVENUE_SHARING_PROHIBITED in decision.codes


@pytest.mark.parametrize("sources", [("",), ("   ",), ("\u200b",), ("", "  ")])
def test_clear_with_a_blank_source_name_is_not_clean(sources):
    """Naming an empty string satisfies "the tuple is non-empty" without naming
    anything, so the source check tests the *entries*, not the tuple."""
    decision = evaluate(report=SuppressionReport(state="clear", sources_checked=sources))
    assert decision.code is RejectionCode.SUPPRESSION_AMBIGUOUS
    assert not decision.eligible


def test_a_suppression_source_with_a_name_still_passes():
    decision = evaluate(report=SuppressionReport(state="clear", sources_checked=("suppression_registry",)))
    assert decision.eligible


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "Note\u0085Bcc: victim@example.com"),   # NEL: line break to lenient parsers
        ("subject", "Note\u2028Bcc: victim@example.com"),   # LINE SEPARATOR
        ("body", "hello\u0085world"),
        ("body", "hello\u009bworld"),
    ],
)
def test_c1_controls_and_unicode_line_separators_are_rejected(field, value):
    """C0 only was checked, so NEL (0x85) and U+2028 passed a "header hygiene"
    guard while being a line break to several parsers."""
    request = valid_request(**{field: value})
    decision = evaluate(approved_record(), request=request)
    assert decision.code is RejectionCode.DRAFT_CONTENT_INVALID


def test_a_writer_cannot_change_the_channel_or_the_sequence_step():
    for tamper in ({"channel": "linkedin_inmail"}, {"sequence_step": 2}, {"sequence_step": "two"}):
        writer = FakeDraftWriter(tamper=tamper)
        with pytest.raises(DraftPersistenceError):
            create_draft(
                valid_request(), FakeQualificationReader(approved_record()),
                FakeSuppressionReader(clean()), writer,
            )


def test_a_writer_cannot_change_the_campaign():
    writer = FakeDraftWriter(tamper={"campaign_id": OTHER_CAMPAIGN})
    with pytest.raises(DraftPersistenceError):
        create_draft(
            valid_request(), FakeQualificationReader(approved_record()),
            FakeSuppressionReader(clean()), writer,
        )


def test_a_writer_cannot_downgrade_the_commercial_model():
    """The database forbids ``strategic_revenue_sharing`` but not
    ``monthly_reserved_capacity``; only the row check keeps the deferred offer out."""
    writer = FakeDraftWriter(tamper={"proposed_commercial_model": "monthly_reserved_capacity"})
    with pytest.raises(DraftPersistenceError):
        create_draft(
            valid_request(), FakeQualificationReader(approved_record()),
            FakeSuppressionReader(clean()), writer,
        )


def test_a_writer_cannot_rewrite_the_provenance_rationale():
    writer = FakeDraftWriter(tamper={"personalization_rationale": "Qualified lead provenance — trusted"})
    with pytest.raises(DraftPersistenceError):
        create_draft(
            valid_request(), FakeQualificationReader(approved_record()),
            FakeSuppressionReader(clean()), writer,
        )


def test_a_redraft_with_different_text_is_a_conflict_not_a_duplicate():
    """A reworded body for an occupied slot is not a duplicate of this request:
    the gate wrote nothing, and the caller must not read that as "already done"."""
    writer = FakeDraftWriter(
        existing={
            "id": "55555555-5555-4555-8555-555555555555",
            "company_id": COMPANY,
            "contact_id": CONTACT,
            "channel": "email",
            "campaign_id": None,
            "sequence_step": 1,
            "subject": valid_request().subject,
            "body": "An earlier draft of this message, materially different.",
        }
    )
    outcome = create_draft(
        valid_request(), FakeQualificationReader(approved_record()),
        FakeSuppressionReader(clean()), writer,
    )
    assert outcome.status == "already_exists"
    assert outcome.content_matches is False
    assert "not the draft this request describes" in outcome.detail
    assert "a different body" in outcome.detail
    assert writer.writes == 0


def test_an_identical_redraft_is_reported_as_a_duplicate():
    writer = FakeDraftWriter()
    qualified = FakeQualificationReader(approved_record())
    suppression = FakeSuppressionReader(clean())
    first = create_draft(valid_request(), qualified, suppression, writer)
    again = create_draft(valid_request(), qualified, suppression, writer)
    assert again.status == "already_exists"
    assert again.content_matches is True
    assert again.draft_id == first.draft_id


@pytest.mark.parametrize(
    "field,value",
    [("subject", b"bytes"), ("body", 5), ("body", None), ("outreach_event", 7),
     ("sequence_step", "not-a-number"), ("sequence_step", True)],
)
def test_non_structural_field_types_are_rejected_at_construction(field, value):
    """A non-str field reached a regex and raised ``TypeError`` from inside a
    guard; the request refuses it up front instead."""
    with pytest.raises(DraftRequestError):
        valid_request(**{field: value})
