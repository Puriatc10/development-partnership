"""Persistence for qualification drafts (Workflow 3, Human Gate 2).

The IPP engine is pure; it never touches the database. This repository is the
only path from a scorecard to ``lead_qualifications``, and it enforces the same
rules the database does — locally and first, so a violation is a clear error
instead of a Postgres exception:

- an agent may only ever write ``review_status='draft'`` (it cannot approve);
- every dimension with points > 0 must carry evidence (``fn_enforce_lead_qualification_rules``);
- tier must match the score bands the trigger enforces;
- a scorecard that is inconclusive because evidence was missing is **not**
  persisted at all (ADR-0006): an unmeasured company is not a disqualified one.

Identity comes from the caller's verified JWT claims; ``evaluator_identity`` is
assigned here, never trusted from the payload.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from contracts.keys import content_key

logger = logging.getLogger("eryndex.supabase.qualification")

DIMENSION_COLUMNS = (
    "customer_flow_score",
    "development_demand_score",
    "repeatability_orders_score",
    "technical_fit_score",
    "business_size_score",
    "decision_maker_accessibility_score",
    "commercial_fit_score",
)
# dimension_evidence keys as used by the trigger.
EVIDENCE_KEYS = (
    "customer_flow",
    "development_demand",
    "repeatability_orders",
    "technical_fit",
    "business_size",
    "decision_maker_accessibility",
    "commercial_fit",
)
VALID_TIERS = frozenset({"tier_1_priority", "tier_2_prospective", "tier_3_deferred", "disqualified"})


class QualificationPersistenceError(ValueError):
    """The scorecard may not be written to the CRM as-is."""


def tier_for_score(overall_score: float, has_disqualification: bool) -> str:
    """Mirror of ``fn_enforce_lead_qualification_rules`` (crm/schema.sql L1301-1319)."""
    if has_disqualification or overall_score < 40:
        return "disqualified"
    if overall_score >= 80:
        return "tier_1_priority"
    if overall_score >= 60:
        return "tier_2_prospective"
    return "tier_3_deferred"


def validate_record(record: Mapping[str, Any]) -> None:
    """Fail locally exactly where the database trigger would fail."""
    if record.get("review_status") != "draft":
        raise QualificationPersistenceError(
            "agents may only write review_status='draft'; approval belongs to a human operator"
        )
    if record.get("evaluator_type") != "agent":
        raise QualificationPersistenceError("evaluator_type must be 'agent' for an agent-written draft")
    if record.get("reviewed_by_human_id") or record.get("reviewed_at"):
        raise QualificationPersistenceError("reviewer fields must be NULL on an agent draft")

    score = float(record.get("overall_score", 0))
    if score < 0 or score > 100:
        raise QualificationPersistenceError(f"overall_score out of range: {score}")

    # chk_lead_qual_score_sum
    dimension_sum = sum(float(record.get(col, 0)) for col in DIMENSION_COLUMNS)
    if abs(dimension_sum - score) > 0.001:
        raise QualificationPersistenceError(
            f"overall_score {score} does not equal the dimension sum {dimension_sum}"
        )

    reasons = record.get("disqualification_reasons") or []
    expected_tier = tier_for_score(score, bool(reasons))
    if record.get("qualification_tier") != expected_tier:
        raise QualificationPersistenceError(
            f"tier '{record.get('qualification_tier')}' violates the score bands; expected '{expected_tier}'"
        )

    evidence = record.get("dimension_evidence") or {}
    for column, key in zip(DIMENSION_COLUMNS, EVIDENCE_KEYS):
        if float(record.get(column, 0)) > 0:
            entry = evidence.get(key)
            if not isinstance(entry, Mapping) or entry.get("evidence") in (None, "", [], {}):
                raise QualificationPersistenceError(
                    f"{column} > 0 requires documented evidence in dimension_evidence['{key}']['evidence']"
                )


def _fingerprint(record: Mapping[str, Any], company_id: str) -> str:
    return content_key(
        {
            "company_id": company_id,
            "overall_score": round(float(record.get("overall_score", 0)), 2),
            "qualification_tier": record.get("qualification_tier"),
            "dimension_evidence": record.get("dimension_evidence") or {},
            "recommended_next_action": record.get("recommended_next_action"),
        }
    )


class QualificationRepository:
    """Writes drafts under an agent JWT; every read/write goes through RLS."""

    def __init__(self, client: Any, agent_identity: str) -> None:
        if not agent_identity:
            raise ValueError("agent_identity is required (from verified JWT claims)")
        self.client = client
        self.agent_identity = agent_identity

    def existing_draft(self, company_id: str) -> Optional[dict]:
        rows = self.client.get(
            "lead_qualifications",
            {
                "company_id": f"eq.{company_id}",
                "evaluator_identity": f"eq.{self.agent_identity}",
                "review_status": "eq.draft",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def persist_draft(self, company_id: str, scorecard: Any) -> dict:
        """Persist one draft; idempotent on identical content (ADR-0004)."""
        if not company_id:
            raise ValueError("company_id is required")
        if getattr(scorecard, "insufficient_evidence", False):
            raise QualificationPersistenceError(
                "refusing to persist an inconclusive scorecard: missing evidence means the company "
                "has not been measured, which is not the same as disqualification (ADR-0006)"
            )
        if not getattr(scorecard, "needs_human_review", False):
            raise QualificationPersistenceError(
                "a scorecard must be marked needs_human_review; the engine may never emit a final decision"
            )

        record = dict(scorecard.to_db_record())
        record.update(
            {
                "company_id": company_id,
                "evaluator_type": "agent",
                "evaluator_identity": self.agent_identity,
            }
        )
        validate_record(record)

        fingerprint = _fingerprint(record, company_id)
        existing = self.existing_draft(company_id)
        if existing and _fingerprint(existing, company_id) == fingerprint:
            logger.info("qualification draft already persisted for company %s", company_id)
            return {"status": "already_executed", "record": existing, "idempotency_key": fingerprint}

        rows = self.client.post("lead_qualifications", record)
        if not rows or not isinstance(rows, list) or not rows[0].get("id"):
            raise RuntimeError("Failed to persist qualification draft")
        persisted = rows[0]
        if persisted.get("review_status") != "draft" or persisted.get("reviewed_by_human_id"):
            raise RuntimeError(
                "Persisted qualification is not a clean draft: review_status="
                f"{persisted.get('review_status')}, reviewed_by_human_id={persisted.get('reviewed_by_human_id')}"
            )
        logger.info(
            "Persisted qualification draft %s for company %s (score %.1f, tier %s)",
            persisted["id"],
            company_id,
            float(persisted.get("overall_score", 0)),
            persisted.get("qualification_tier"),
        )
        return {"status": "created", "record": persisted, "idempotency_key": fingerprint}
