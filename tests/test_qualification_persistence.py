"""Qualification persistence rules (ADR-0006) tested without a database.

The repository mirrors the database trigger locally; these tests assert the
mirror is faithful, so a violation fails before any network call.
"""

from __future__ import annotations

import pytest

from integrations.supabase.qualification_repository import (
    QualificationPersistenceError,
    QualificationRepository,
    tier_for_score,
    validate_record,
)


class FakeClient:
    def __init__(self, existing=None, post_rows=None):
        self.existing = existing or []
        self.post_rows = post_rows
        self.gets = []
        self.posts = []

    def get(self, table, params=None):
        self.gets.append((table, params))
        return self.existing

    def post(self, table, data, prefer="return=representation"):
        self.posts.append((table, data))
        if self.post_rows is not None:
            return self.post_rows
        row = dict(data)
        row["id"] = "lq-new"
        return [row]


class StubCard:
    def __init__(self, record, insufficient=False):
        self._record = record
        self.insufficient_evidence = insufficient
        self.needs_human_review = True

    def to_db_record(self):
        return dict(self._record)


EVIDENCE = {
    key: {"evidence": f"finding for {key}", "finder": []}
    for key in (
        "customer_flow",
        "development_demand",
        "repeatability_orders",
        "technical_fit",
        "business_size",
        "decision_maker_accessibility",
        "commercial_fit",
    )
}


def record(**over):
    base = {
        "overall_score": 62.0,
        "customer_flow_score": 15.0,
        "development_demand_score": 10.0,
        "repeatability_orders_score": 9.0,
        "technical_fit_score": 10.0,
        "business_size_score": 6.0,
        "decision_maker_accessibility_score": 6.0,
        "commercial_fit_score": 6.0,
        "qualification_tier": "tier_2_prospective",
        "disqualification_reasons": [],
        "recommended_next_action": "Approach with wholesale backend capacity.",
        "review_status": "draft",
        "dimension_evidence": dict(EVIDENCE),
    }
    base.update(over)
    return base


# --- local mirror of the trigger -----------------------------------------


def test_valid_record_passes():
    validate_record(
        record(
            company_id="c1",
            evaluator_type="agent",
            evaluator_identity="hermes-qualifier-v1",
        )
    )


def test_agent_cannot_write_approved_status():
    with pytest.raises(QualificationPersistenceError):
        validate_record(record(review_status="approved", evaluator_type="agent", evaluator_identity="x"))


def test_reviewer_fields_must_be_null():
    with pytest.raises(QualificationPersistenceError):
        validate_record(
            record(
                evaluator_type="agent",
                evaluator_identity="x",
                reviewed_by_human_id="human-1",
            )
        )


def test_score_sum_must_match_dimensions():
    with pytest.raises(QualificationPersistenceError):
        validate_record(record(overall_score=63.0, evaluator_type="agent", evaluator_identity="x"))


def test_tier_must_match_score_band():
    with pytest.raises(QualificationPersistenceError):
        validate_record(
            record(overall_score=62.0, qualification_tier="tier_1_priority", evaluator_type="agent", evaluator_identity="x")
        )


def test_positive_score_without_evidence_is_rejected():
    broken = record(evaluator_type="agent", evaluator_identity="x")
    broken["dimension_evidence"] = dict(EVIDENCE)
    broken["dimension_evidence"]["technical_fit"] = {"evidence": None}
    with pytest.raises(QualificationPersistenceError):
        validate_record(broken)


@pytest.mark.parametrize(
    "score,expected",
    [(0, "disqualified"), (39.99, "disqualified"), (40, "tier_3_deferred"), (59.9, "tier_3_deferred"),
     (60, "tier_2_prospective"), (79.9, "tier_2_prospective"), (80, "tier_1_priority"), (100, "tier_1_priority")],
)
def test_tier_bands_mirror_the_trigger(score, expected):
    assert tier_for_score(score, False) == expected


def test_any_disqualification_forces_disqualified_tier():
    assert tier_for_score(95, True) == "disqualified"


# --- repository behaviour -------------------------------------------------


def test_inconclusive_scorecard_is_never_persisted():
    repo = QualificationRepository(FakeClient(), "hermes-qualifier-v1")
    with pytest.raises(QualificationPersistenceError) as exc:
        repo.persist_draft("c1", StubCard(record(), insufficient=True))
    assert "inconclusive" in str(exc.value)


def test_identity_is_added_by_the_repository_not_the_payload():
    client = FakeClient()
    repo = QualificationRepository(client, "hermes-qualifier-v1")
    repo.persist_draft("c1", StubCard(record()))
    sent = client.posts[0][1]
    assert sent["company_id"] == "c1"
    assert sent["evaluator_type"] == "agent"
    assert sent["evaluator_identity"] == "hermes-qualifier-v1"
    assert sent["review_status"] == "draft"


def test_identical_draft_is_idempotent():
    card = StubCard(record())
    first_client = FakeClient()
    QualificationRepository(first_client, "hermes-qualifier-v1").persist_draft("c1", card)
    persisted = first_client.posts[0][1]
    persisted["id"] = "lq-1"

    second_client = FakeClient(existing=[persisted])
    result = QualificationRepository(second_client, "hermes-qualifier-v1").persist_draft("c1", card)
    assert result["status"] == "already_executed"
    assert second_client.posts == []


def test_changed_score_produces_a_new_draft():
    card = StubCard(record())
    client = FakeClient()
    QualificationRepository(client, "hermes-qualifier-v1").persist_draft("c1", card)
    stored = dict(client.posts[0][1], id="lq-1")

    changed = record(customer_flow_score=16.0, overall_score=63.0)
    client2 = FakeClient(existing=[stored])
    result = QualificationRepository(client2, "hermes-qualifier-v1").persist_draft("c1", StubCard(changed))
    assert result["status"] == "created"
    assert len(client2.posts) == 1


def test_persisted_row_is_read_back_and_verified():
    client = FakeClient(post_rows=[{"id": "lq-1", "review_status": "approved", "reviewed_by_human_id": "h"}])
    repo = QualificationRepository(client, "hermes-qualifier-v1")
    with pytest.raises(RuntimeError):
        repo.persist_draft("c1", StubCard(record()))


def test_missing_identity_is_rejected():
    with pytest.raises(ValueError):
        QualificationRepository(FakeClient(), "")
