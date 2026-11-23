"""Human Gate 2: persist an agent-authored scorecard, then let a human decide it.

Two identities are involved, and the split is the point of the gate:

- the **draft** is written by the qualifier agent (``QUALIFIER_EMAIL`` /
  ``QUALIFIER_PASSWORD``), because ``lead_qualifications.evaluator_identity`` has to name
  the agent that produced the scorecard and ``agent_insert_lead_qualifications`` only lets
  a qualifier insert ``review_status='draft'``;
- the **decision** is written by a human operator, because the database refuses an approval
  whose ``reviewed_by_human_id`` is not ``auth.uid()``.

Nothing here trusts the caller for attribution. ``reviewed_by_human_id`` is always the
signed-in operator's subject, and after every write the row is read back to prove the
database recorded what was asked. Rejection is the one place the schema cannot keep the
reviewer on the row: ``chk_lead_qual_review_human_gate`` and
``fn_enforce_lead_qualification_rules`` force the reviewer pair to NULL for every status
other than ``approved``. A rejection is therefore attributed through the immutable
``audit_logs`` ledger, which is verified here as well.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from contracts.environment import (
    DatabaseConfig,
    assert_database_environment,
    load_env_file,
)
from integrations.postgres.auth import PostgresAuthClient
from integrations.postgres.client import DatabaseError, PostgresClient
from integrations.postgres.qualification_repository import QualificationPersistenceError
from qualification.loader import QualificationInputError  # re-exported for the CLI
from qualification.service import qualify_company

TABLE = "lead_qualifications"
AUDIT_TABLE = "audit_logs"

DRAFT = "draft"
PENDING = "pending_human_review"
APPROVED = "approved"
REJECTED = "rejected"

STATUSES = (DRAFT, PENDING, APPROVED, REJECTED)

#: Only a scorecard that has not been decided yet may be decided.
DECIDABLE = (DRAFT, PENDING)

REQUIRED_ENV = (
    "ERYNDEX_DB_URL",
    "ERYNDEX_JWT_SECRET",
    "QUALIFIER_EMAIL",
    "QUALIFIER_PASSWORD",
)

QUALIFICATION_SELECT = (
    "id,company_id,evaluator_type,evaluator_identity,overall_score,"
    "customer_flow_score,development_demand_score,repeatability_orders_score,"
    "technical_fit_score,business_size_score,decision_maker_accessibility_score,"
    "commercial_fit_score,dimension_evidence,qualification_tier,"
    "disqualification_reasons,disqualification_notes,recommended_next_action,"
    "review_status,reviewed_by_human_id,reviewed_at,created_at,updated_at"
)

AUDIT_SELECT = (
    "id,table_name,record_id,action,actor_type,actor_identity,new_values,created_at"
)


class QualificationGateError(RuntimeError):
    """A Gate 2 action is not legal, or its result cannot be established."""


class QualifierIdentityError(QualificationGateError):
    """The identity used for persistence is not the qualifier agent."""


class QualificationNotMeasured(QualificationGateError):
    """The evidence cannot support a scorecard, so nothing was written (ADR-0006)."""


class QualifierWriteDenied(QualificationGateError):
    """The database refused the qualifier's insert (SQLSTATE 42501)."""

    def __init__(self, message: str, *, missing_claim: str | None = None) -> None:
        super().__init__(message)
        self.missing_claim = missing_claim


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _environment(environ: Mapping[str, str] | None) -> dict[str, str]:
    if environ is not None:
        return dict(environ)
    return {**load_env_file(), **os.environ}


def _single(rows: Any, what: str) -> Mapping[str, Any]:
    rows = list(rows or [])
    if not rows:
        raise QualificationGateError(f"{what} not found (or not visible to this identity)")
    if len(rows) != 1:
        raise QualificationGateError(
            f"{what} resolved to {len(rows)} rows; refusing to act on an ambiguous row"
        )
    return rows[0]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def list_qualifications(
    client: Any,
    *,
    status: str | None = None,
    company_id: str | None = None,
    limit: int = 50,
) -> list[Mapping[str, Any]]:
    if status is not None and status not in STATUSES:
        raise QualificationGateError(
            f"unknown review status {status!r}; expected one of {list(STATUSES)}"
        )
    params: dict[str, Any] = {
        "select": QUALIFICATION_SELECT,
        "order": "created_at.desc",
        "limit": str(max(1, min(int(limit), 500))),
    }
    if status is not None:
        params["review_status"] = f"eq.{status}"
    if company_id is not None:
        params["company_id"] = f"eq.{company_id}"
    return list(client.get(TABLE, params))


def show(client: Any, qualification_id: str) -> Mapping[str, Any]:
    """One scorecard, including the evidence behind every dimension."""
    rows = client.get(
        TABLE,
        {"select": QUALIFICATION_SELECT, "id": f"eq.{qualification_id}", "limit": "1"},
    )
    return _single(rows, f"qualification {qualification_id!r}")


# ---------------------------------------------------------------------------
# Persistence — the qualifier agent writes the draft
# ---------------------------------------------------------------------------


def _qualifier_identity(claims: Mapping[str, Any], email: str) -> str:
    """Refuse locally when the identity cannot satisfy ``agent_insert_lead_qualifications``.

    The database would refuse it anyway; failing here turns a 42501 into a sentence that
    names the claim to fix, before a connection is even opened for the write.
    """
    app = dict(claims.get("app_metadata") or {})
    role = str(app.get("user_role") or "")
    subagent = str(app.get("subagent_type") or "")
    agent_id = str(app.get("agent_id") or "")

    if role != "agent":
        raise QualifierIdentityError(
            f"QUALIFIER_EMAIL ({email}) signs in with app_metadata.user_role="
            f"{role or 'absent'!r}; persisting a scorecard requires 'agent'. A human "
            "operator may review a scorecard, never author one — use `qualifications "
            "approve` for the human half of Gate 2."
        )
    if subagent != "qualifier":
        raise QualifierIdentityError(
            f"QUALIFIER_EMAIL ({email}) signs in with app_metadata.subagent_type="
            f"{subagent or 'absent'!r}; persisting a scorecard requires 'qualifier' "
            "(the policy is is_agent_type('qualifier'))."
        )
    if not agent_id:
        raise QualifierIdentityError(
            f"QUALIFIER_EMAIL ({email}) has no app_metadata.agent_id; a scorecard must name "
            "its evaluator and the policy requires evaluator_identity = current_agent_identity()."
        )
    return agent_id


def _explain_policy_denial(claims: Mapping[str, Any]) -> tuple[str, str | None]:
    """Name the claim or scope a 42501 was missing, outermost requirement first."""
    app = dict(claims.get("app_metadata") or {})
    role = str(app.get("user_role") or "")
    if not claims:
        return (
            "no JWT claims were supplied, so every row-level policy denies the insert",
            "app_metadata.user_role",
        )
    if role != "agent":
        return (
            (
                f"app_metadata.user_role is {role or 'absent'!r}; a qualification draft "
                "requires 'agent'"
            ),
            "app_metadata.user_role",
        )
    if str(app.get("subagent_type") or "") != "qualifier":
        return (
            (
                f"app_metadata.subagent_type is {app.get('subagent_type') or 'absent'!r}; "
                "the insert policy is is_agent_type('qualifier')"
            ),
            "app_metadata.subagent_type",
        )
    if not app.get("agent_id"):
        return (
            (
                "app_metadata.agent_id is missing; evaluator_identity must equal "
                "current_agent_identity()"
            ),
            "app_metadata.agent_id",
        )
    return (
        (
            "the qualifier identity is correct, so the remaining requirement is task scope: "
            "agent_insert_lead_qualifications requires a task assigned to "
            f"{app.get('agent_id')!r} for this company with status pending or in_progress"
        ),
        "tasks.status (an assigned task in pending/in_progress)",
    )


def persist_scorecard(
    company_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    """Score the company from its own research and persist the draft as the qualifier.

    Refuses in three distinct ways, each with a different next action:

    - the identity is not a qualifier agent → nothing is attempted;
    - the evidence is inconclusive → nothing is written (ADR-0006: not measured is not
      disqualified);
    - the database refuses the insert (42501) → the message names the missing claim/scope.
    """
    if not str(company_id or "").strip():
        raise QualificationGateError("company_id is required")

    env = _environment(environ)
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise QualificationGateError(
            "missing qualifier configuration: " + ", ".join(sorted(set(missing)))
        )

    config = DatabaseConfig.load(env)
    assert_database_environment(config)

    auth = PostgresAuthClient(config.dsn, config.jwt_secret)
    signed_in = auth.sign_in_with_password(env["QUALIFIER_EMAIL"], env["QUALIFIER_PASSWORD"])
    claims = dict(signed_in.get("claims") or {})
    agent_id = _qualifier_identity(claims, str(env["QUALIFIER_EMAIL"]))

    client = PostgresClient(config.dsn, role="authenticated", claims=claims)
    try:
        try:
            result = qualify_company(client, company_id, agent_identity=agent_id, persist=True)
        except QualificationPersistenceError as exc:
            message = str(exc)
            if "inconclusive" in message.lower():
                raise QualificationNotMeasured(
                    f"company {company_id} was not persisted: its evidence is inconclusive, so "
                    "the IPP scorecard is refused rather than written as a score. Missing "
                    "evidence means the company has not been measured — that is not the same as "
                    "disqualification (ADR-0006). Nothing was written. Close the research gaps "
                    "and persist again; `qualify` shows which dimensions are unearned. "
                    f"Engine detail: {message}"
                ) from exc
            raise QualificationGateError(
                f"the scorecard for company {company_id} was refused before it was written: "
                f"{message}"
            ) from exc
        except QualificationInputError as exc:
            if "not found (or not visible)" in str(exc):
                # A zero-row SELECT looks identical to a missing company; for an agent it is
                # almost always scope. agent_select_companies is task-scoped, so without an
                # assigned task the qualifier cannot read the company it is meant to score.
                raise QualificationGateError(
                    f"company {company_id} is not visible to the qualifier {agent_id!r}: {exc}. "
                    "Agent reads are task-scoped (agent_select_companies), so the qualifier "
                    "needs a task assigned to it for this company with status pending or "
                    "in_progress. Assign the qualification task first."
                ) from exc
            raise

        except DatabaseError as exc:
            if exc.code == "42501" or "row-level security" in str(exc).lower() or (
                "permission denied" in str(exc).lower()
            ):
                explanation, claim = _explain_policy_denial(claims)
                raise QualifierWriteDenied(
                    f"the qualifier insert into {TABLE} was refused for {agent_id!r} "
                    f"[42501]: {explanation}",
                    missing_claim=claim,
                ) from exc
            raise QualificationGateError(
                f"the database refused the scorecard for company {company_id}"
                f" ({exc.code or 'no SQLSTATE'}): {exc.message}"
            ) from exc
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()

    row = result.persisted if isinstance(result.persisted, dict) else None
    if not row or not row.get("id"):
        raise QualificationGateError(
            f"persisting the scorecard for company {company_id} returned no row"
        )
    if str(row.get("review_status")) != DRAFT or row.get("reviewed_by_human_id"):
        raise QualificationGateError(
            "the persisted scorecard is not a clean draft "
            f"(review_status={row.get('review_status')!r}, "
            f"reviewed_by_human_id={row.get('reviewed_by_human_id')!r}); an agent may only "
            "write an unreviewed draft"
        )
    if str(row.get("evaluator_identity") or "") != agent_id:
        raise QualificationGateError(
            f"the persisted scorecard names evaluator {row.get('evaluator_identity')!r}, "
            f"not the signing qualifier {agent_id!r}"
        )
    return row


# ---------------------------------------------------------------------------
# Human decisions — approve / reject
# ---------------------------------------------------------------------------


def _merge_note(existing: Any, entry: str) -> str:
    """Keep the agent's notes and append the human's, so neither is lost."""
    current = str(existing or "").strip()
    return f"{current}\n{entry}" if current else entry


def _refuse_wrong_state(qualification_id: str, current: str, target: str) -> None:
    raise QualificationGateError(
        f"qualification {qualification_id!r} is {current!r}; it can only move to {target!r} "
        f"from {list(DECIDABLE)}. Run `qualifications show {qualification_id}` to see the "
        "scorecard and its history."
    )


def _audit_rejection(
    client: Any, qualification_id: str, *, operator_id: str
) -> Mapping[str, Any]:
    """Prove the immutable ledger attributes the rejection to the signing operator.

    ``reviewed_by_human_id`` is forced NULL on a rejected row, so the audit row is the
    reviewer record. It is written by a SECURITY DEFINER trigger in the same transaction.
    """
    rows = client.get(
        AUDIT_TABLE,
        {
            "select": AUDIT_SELECT,
            "table_name": f"eq.{TABLE}",
            "record_id": f"eq.{qualification_id}",
            "order": "id.desc",
            "limit": "10",
        },
    )
    for row in rows or []:
        new_values = row.get("new_values") or {}
        if str(new_values.get("review_status")) != REJECTED:
            continue
        actor = str(row.get("actor_identity") or "")
        if actor != str(operator_id):
            raise QualificationGateError(
                f"the rejection of {qualification_id!r} was recorded against actor "
                f"{actor!r}, not the operator who signed in ({operator_id!r}); refusing to "
                "treat that as this operator's decision"
            )
        return row
    raise QualificationGateError(
        f"the rejection of {qualification_id!r} was written, but the audit ledger contains no "
        "row attributing it to the signing operator; the decision is unattributed — investigate "
        "the audit trigger before relying on it"
    )


def approve(
    client: Any,
    qualification_id: str,
    *,
    operator_id: str,
    note: str | None = None,
) -> Mapping[str, Any]:
    """Record Human Gate 2 approval. One UPDATE carries the status and the reviewer."""
    if not str(operator_id or "").strip():
        raise QualificationGateError(
            "operator_id is required: the database binds reviewed_by_human_id to auth.uid()"
        )

    row = show(client, qualification_id)
    current = str(row.get("review_status"))
    if current == APPROVED:
        # Idempotent: the decision is already of record. Nothing is rewritten, so the
        # original reviewer and timestamp stay the ones the database recorded.
        if not row.get("reviewed_by_human_id"):
            raise QualificationGateError(
                f"qualification {qualification_id!r} is approved but carries no reviewer; "
                "chk_lead_qual_review_human_gate says that row is inconsistent — investigate "
                "before relying on it"
            )
        return row
    if current not in DECIDABLE:
        _refuse_wrong_state(qualification_id, current, APPROVED)

    patch: dict[str, Any] = {
        "review_status": APPROVED,
        "reviewed_by_human_id": operator_id,
        # fn_enforce_lead_qualification_rules overwrites this with clock_timestamp(); it is
        # sent anyway so the row satisfies chk_lead_qual_review_human_gate on its own terms.
        "reviewed_at": _now(),
    }
    if note and str(note).strip():
        # There is no reviewer-note column on lead_qualifications. The note is kept with the
        # scorecard's notes (the only free-text column) under an explicit marker, and the
        # audit trigger captures the whole row image either way.
        patch["disqualification_notes"] = _merge_note(
            row.get("disqualification_notes"), f"Review note: {str(note).strip()}"
        )

    try:
        updated = client.patch(
            TABLE,
            patch,
            params={"id": f"eq.{qualification_id}", "review_status": f"eq.{current}"},
        )
    except DatabaseError as exc:
        if exc.code == "23505":
            raise QualificationGateError(
                f"company {row.get('company_id')} already has an approved scorecard "
                "(uq_lead_qualifications_approved). Return that one to draft before approving "
                "a replacement — two approved scorecards would make the company's tier depend "
                "on insertion order."
            ) from exc
        raise QualificationGateError(
            f"the database refused the approval of {qualification_id!r} "
            f"({exc.code or 'no SQLSTATE'}): {exc.message}"
        ) from exc

    if not updated:
        after = show(client, qualification_id)
        if str(after.get("review_status")) == APPROVED:
            return after
        if str(after.get("review_status")) == current:
            raise QualificationGateError(
                f"the approval of {qualification_id!r} was refused: the row is still "
                f"{current!r}, so no row was visible for update. This identity is not "
                "permitted to review scorecards (the policy requires a human operator)."
            )
        raise QualificationGateError(
            f"qualification {qualification_id!r} changed while being approved (now "
            f"{after.get('review_status')!r}); no approval was recorded"
        )

    result = _single(updated, f"qualification {qualification_id!r}")
    if str(result.get("review_status")) != APPROVED:
        raise QualificationGateError(
            f"the approval of {qualification_id!r} did not persist as requested "
            f"(review_status={result.get('review_status')!r})"
        )
    recorded_reviewer = str(result.get("reviewed_by_human_id") or "")
    if recorded_reviewer != str(operator_id):
        raise QualificationGateError(
            f"the approval of {qualification_id!r} was recorded against "
            f"{recorded_reviewer or 'no reviewer'!r}, not the operator who signed in "
            f"({operator_id!r}); refusing to treat that as this operator's decision"
        )
    if not result.get("reviewed_at"):
        raise QualificationGateError(
            f"the approval of {qualification_id!r} has no reviewed_at timestamp; "
            "chk_lead_qual_review_human_gate requires the reviewer pair together"
        )
    return result


def reject(
    client: Any,
    qualification_id: str,
    *,
    operator_id: str,
    reason: str,
) -> Mapping[str, Any]:
    """Overrule a scorecard, recording the reason the schema demands.

    Reviewer attribution moves to ``audit_logs`` here: the schema forces the reviewer pair
    to NULL on any status other than ``approved``, and requires ``disqualification_notes``
    (or reasons) on a rejection.
    """
    if not str(operator_id or "").strip():
        raise QualificationGateError(
            "operator_id is required: the database binds the reviewer to auth.uid()"
        )
    if not reason or not str(reason).strip():
        raise QualificationGateError(
            "a rejection must state a reason; it becomes the scorecard's disqualification notes"
        )
    reason = str(reason).strip()

    row = show(client, qualification_id)
    current = str(row.get("review_status"))
    if current == REJECTED:
        return row
    if current not in DECIDABLE:
        _refuse_wrong_state(qualification_id, current, REJECTED)

    patch = {
        "review_status": REJECTED,
        "disqualification_notes": _merge_note(
            row.get("disqualification_notes"), f"Rejection reason: {reason}"
        ),
    }
    try:
        updated = client.patch(
            TABLE,
            patch,
            params={"id": f"eq.{qualification_id}", "review_status": f"eq.{current}"},
        )
    except DatabaseError as exc:
        raise QualificationGateError(
            f"the database refused the rejection of {qualification_id!r} "
            f"({exc.code or 'no SQLSTATE'}): {exc.message}"
        ) from exc

    if not updated:
        after = show(client, qualification_id)
        if str(after.get("review_status")) == REJECTED:
            return after
        if str(after.get("review_status")) == current:
            raise QualificationGateError(
                f"the rejection of {qualification_id!r} was refused: the row is still "
                f"{current!r}, so no row was visible for update. This identity is not "
                "permitted to review scorecards (the policy requires a human operator)."
            )
        raise QualificationGateError(
            f"qualification {qualification_id!r} changed while being rejected (now "
            f"{after.get('review_status')!r}); no rejection was recorded"
        )

    result = _single(updated, f"qualification {qualification_id!r}")
    if str(result.get("review_status")) != REJECTED:
        raise QualificationGateError(
            f"the rejection of {qualification_id!r} did not persist as requested "
            f"(review_status={result.get('review_status')!r})"
        )
    if result.get("reviewed_by_human_id") is not None or result.get("reviewed_at") is not None:
        raise QualificationGateError(
            f"the rejected scorecard {qualification_id!r} carries reviewer fields "
            f"(reviewer={result.get('reviewed_by_human_id')!r}, "
            f"reviewed_at={result.get('reviewed_at')!r}); chk_lead_qual_review_human_gate "
            "forbids them on any status other than 'approved'"
        )

    audit_row = _audit_rejection(client, qualification_id, operator_id=operator_id)
    return {**result, "audit_rejection": audit_row}


__all__ = [
    "APPROVED",
    "DECIDABLE",
    "DRAFT",
    "PENDING",
    "REJECTED",
    "STATUSES",
    "QualificationGateError",
    "QualificationInputError",
    "QualificationNotMeasured",
    "QualifierIdentityError",
    "QualifierWriteDenied",
    "approve",
    "list_qualifications",
    "persist_scorecard",
    "reject",
    "show",
]
