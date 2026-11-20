"""Human Gate 1, first half: reviewing a staged discovery candidate.

The rules that matter are enforced by the database, and this module is written to respect
rather than duplicate them:

- ``chk_discovery_candidate_review_pair`` requires ``reviewed_by_human_id`` and
  ``reviewed_at`` to be set together with any status other than ``pending_review``, so a
  review is always one UPDATE carrying the reviewer.
- ``uq_discovery_candidates_pending_domain`` is partial on ``pending_review``, which means
  a candidate that stays pending shadows its domain for every later discovery run. Leaving
  ``pending_review`` is therefore the whole point of Gate 1, not bookkeeping.
- ``human_operator_all_discovery_candidates`` lets a human review any row; the agent policy
  never allows an agent to update one.

A transition is idempotent: repeating it returns the row as it already stands, and a
transition that is not legal from the current status fails closed with the reason.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

CANDIDATE_TABLE = "discovery_candidates"
EVIDENCE_TABLE = "discovery_candidate_evidence"

PENDING = "pending_review"
APPROVED = "approved"
REJECTED = "rejected"
DUPLICATE = "duplicate"
CONFLICT = "conflict"

STATUSES = (PENDING, APPROVED, REJECTED, DUPLICATE, CONFLICT)

CANDIDATE_SELECT = (
    "id,name,normalized_name,domain,source_name,source_url,discovered_at,signals,status,"
    "batch_key,discovered_by_agent_identity,reviewed_by_human_id,reviewed_at,review_notes,"
    "converted_company_id,created_at,updated_at"
)


class Gate1Error(RuntimeError):
    """A review action is not legal, or its result cannot be established."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _single(rows: Sequence[Mapping[str, Any]], what: str) -> Mapping[str, Any]:
    if not rows:
        raise Gate1Error(f"{what} not found (or not visible to this operator)")
    if len(rows) != 1:
        raise Gate1Error(f"{what} resolved to {len(rows)} rows; refusing to act on an ambiguous row")
    return rows[0]


def get_candidate(client: Any, candidate_id: str) -> Mapping[str, Any]:
    rows = client.get(CANDIDATE_TABLE, {"select": CANDIDATE_SELECT, "id": f"eq.{candidate_id}"})
    return _single(rows, f"candidate {candidate_id!r}")


def list_candidates(
    client: Any,
    *,
    status: str = PENDING,
    limit: int = 50,
) -> list[Mapping[str, Any]]:
    """Pending candidates are the operator's queue; anything else is history."""
    if status != "any" and status not in STATUSES:
        raise Gate1Error(f"unknown status {status!r}; expected one of {sorted(STATUSES)} or 'any'")
    params: dict[str, Any] = {
        "select": CANDIDATE_SELECT,
        "order": "discovered_at.asc",
        "limit": str(max(1, min(int(limit), 500))),
    }
    if status != "any":
        params["status"] = f"eq.{status}"
    return list(client.get(CANDIDATE_TABLE, params))


def list_evidence(client: Any, candidate_id: str) -> list[Mapping[str, Any]]:
    return list(
        client.get(
            EVIDENCE_TABLE,
            {
                "select": "candidate_id,url,excerpt,retrieved_at,provider",
                "candidate_id": f"eq.{candidate_id}",
                "order": "retrieved_at.asc",
            },
        )
    )


def _review(
    client: Any,
    candidate_id: str,
    *,
    operator_id: str,
    status: str,
    review_notes: str | None,
    allowed_from: Sequence[str],
) -> Mapping[str, Any]:
    candidate = get_candidate(client, candidate_id)
    current = str(candidate.get("status"))

    if current == status:
        # Idempotent: the decision was already recorded. Nothing is rewritten, so the
        # original reviewer and timestamp stay the ones of record.
        return candidate
    if current not in allowed_from:
        raise Gate1Error(
            f"candidate {candidate_id!r} is {current!r}; it can only move to {status!r} from "
            f"{list(allowed_from)}. Run `candidates show {candidate_id}` to see its history."
        )

    patch: dict[str, Any] = {
        "status": status,
        "reviewed_by_human_id": operator_id,
        "reviewed_at": _now(),
    }
    if review_notes:
        patch["review_notes"] = review_notes

    updated = client.patch(
        CANDIDATE_TABLE,
        patch,
        params={"id": f"eq.{candidate_id}", "status": f"eq.{current}"},
    )
    if not updated:
        # The filter included the old status, so an empty result means the row moved
        # underneath us. Re-read rather than assuming the write happened.
        after = get_candidate(client, candidate_id)
        if str(after.get("status")) == status:
            return after
        raise Gate1Error(
            f"candidate {candidate_id!r} changed while being reviewed (now "
            f"{after.get('status')!r}); no review was recorded"
        )
    row = _single(updated, f"candidate {candidate_id!r}")
    if str(row.get("status")) != status or not row.get("reviewed_by_human_id"):
        raise Gate1Error(
            f"the review of {candidate_id!r} did not persist as requested "
            f"(status={row.get('status')!r}, reviewer={row.get('reviewed_by_human_id')!r})"
        )
    return row


def approve_candidate(
    client: Any,
    candidate_id: str,
    *,
    operator_id: str,
    review_notes: str | None = None,
) -> Mapping[str, Any]:
    """Record Gate 1 approval. Registration is a separate, explicit action."""
    return _review(
        client,
        candidate_id,
        operator_id=operator_id,
        status=APPROVED,
        review_notes=review_notes,
        allowed_from=(PENDING,),
    )


def reject_candidate(
    client: Any,
    candidate_id: str,
    *,
    operator_id: str,
    reason: str,
) -> Mapping[str, Any]:
    """Reject a candidate, with the reason on the row. A bare rejection is not accepted."""
    if not reason or not str(reason).strip():
        raise Gate1Error("a rejection must state a reason; it becomes the candidate's review note")
    return _review(
        client,
        candidate_id,
        operator_id=operator_id,
        status=REJECTED,
        review_notes=str(reason).strip(),
        allowed_from=(PENDING,),
    )


def mark_duplicate(
    client: Any,
    candidate_id: str,
    *,
    operator_id: str,
    company_id: str,
    note: str | None = None,
) -> Mapping[str, Any]:
    """Record that an approved candidate already exists in the CRM as ``company_id``."""
    candidate = get_candidate(client, candidate_id)
    if str(candidate.get("status")) == DUPLICATE and candidate.get("converted_company_id") == company_id:
        return candidate
    if str(candidate.get("status")) not in (PENDING, APPROVED):
        raise Gate1Error(
            f"candidate {candidate_id!r} is {candidate.get('status')!r}; only a pending or "
            "approved candidate can be marked duplicate"
        )
    updated = client.patch(
        CANDIDATE_TABLE,
        {
            "status": DUPLICATE,
            "reviewed_by_human_id": operator_id,
            "reviewed_at": _now(),
            "review_notes": note or f"matched existing company {company_id}",
            "converted_company_id": company_id,
        },
        params={"id": f"eq.{candidate_id}"},
    )
    row = _single(updated, f"candidate {candidate_id!r}")
    if str(row.get("status")) != DUPLICATE:
        raise Gate1Error(f"marking {candidate_id!r} duplicate did not persist")
    return row


def link_company(client: Any, candidate_id: str, *, company_id: str) -> Mapping[str, Any]:
    """Attach the created company to its candidate, closing the Gate 1 trail."""
    updated = client.patch(
        CANDIDATE_TABLE,
        {"converted_company_id": company_id},
        params={"id": f"eq.{candidate_id}"},
    )
    row = _single(updated, f"candidate {candidate_id!r}")
    if str(row.get("converted_company_id") or "") != company_id:
        raise Gate1Error(
            f"candidate {candidate_id!r} does not point at company {company_id!r} after linking"
        )
    return row
