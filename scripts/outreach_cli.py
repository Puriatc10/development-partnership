"""Draft, list, show, approve, authorise, hand off and record-a-manual-send for outreach.

Seven subcommands, four of them read-only:

* ``draft``     — run the eligibility gate, render a deterministic draft, persist it;
* ``list``      — the drafts filed under one company;
* ``show``      — one message, in full;
* ``approve``   — Gate G3: an operator marks a draft approved (a human action);
* ``authorize`` — Gate G4: record the dispatch authorisation decision;
* ``export``    — render an approved message for a human to send by hand (records nothing);
* ``mark-sent`` — record that a human did send it, with the mailbox's message id.

``authorize`` records a decision and stops there. Dispatch does not exist in this
build: ``outreach.dispatch`` raises unconditionally and this script does not
import it — not to ask whether dispatch is available, not to print its state.
The command says so in its output because the absence is the point.

``export`` and ``mark-sent`` close the loop the other way round, without adding a
transport: a human pastes an approved message into Gmail, and then tells the
platform what they did. ``export`` writes an .eml or prints a copy-ready block and
records nothing at all; ``mark-sent`` calls ``fn_record_manual_send``, which
requires a human operator, an approved message and a non-blank external id, and
which writes the state change and its activity row in one transaction. Neither
path can transmit: there is no SMTP import anywhere in this repository, and
``tests/test_outreach_handoff.py`` asserts that the renderer is pure.

Who is recorded as the drafter
------------------------------
``drafted_by_agent_identity`` is stamped with the signed-in operator's id. The
text is template-generated (``outreach.drafts``, no model, no network) and the row
is inserted under ``human_operator_all_outreach_messages``, so the operator is the
actor that caused it. The agent-side insert policy additionally requires
``is_agent_type('drafter')`` (crm/schema.sql L1977-1999, S8 in
reports/eryndex/outreach-workflow.md) and this build provisions no drafter
identity; writing an agent's name onto a row an operator created would be a lie,
so it is not done.

Every command runs as a signed-in human operator (``operations.session``). That
is deliberate rather than convenient: the only policy that admits a read of
``suppression_registry`` is ``human_operator_all_suppression``, and the draft-time
suppression check has to be a real read (see ``outreach.adapters``). An agent
session would fail closed with ``suppression_lookup_failed``, which is the correct
answer for an identity that cannot see the ledger.

Refusals from the gate are printed with the code that caused them
(``qualification_missing``, ``qualification_pending_review``, ``suppressed_domain``,
...) and exit 1. Configuration and session failures exit 2. Nothing here retries,
queues, schedules or sends.

    python scripts/outreach_cli.py draft --company-id <uuid> --contact-id <uuid> \\
        --campaign-id <uuid> --step 1
    python scripts/outreach_cli.py list --company-id <uuid>
    python scripts/outreach_cli.py show <message-id>
    python scripts/outreach_cli.py approve <message-id> --note "read it twice, tone is right"
    python scripts/outreach_cli.py authorize <message-id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from contracts.environment import load_env_file  # noqa: E402
from contracts.errors import EnvironmentGuardError, GateViolation  # noqa: E402
from operations.session import (  # noqa: E402
    AgentSessionError,
    OperatorSessionError,
    open_agent_session,
    open_session,
)
from outreach.adapters import (  # noqa: E402
    PostgresDraftWriter,
    PostgresQualificationReader,
    PostgresSuppressionReader,
)
from outreach.drafts import DraftGenerationError, render_draft, template_for  # noqa: E402
from outreach.handoff import (  # noqa: E402
    SENDER_MAILBOX,
    HandoffRefused,
    render_email_handoff,
    to_eml,
)
from outreach.gate import (  # noqa: E402
    DraftOutcome,
    DraftRejected,
    DraftRequest,
    DraftRequestError,
    create_draft,
    draft_idempotency_key,
    evaluate_draft_eligibility,
)
from outreach.ports import OUTREACH_CHANNELS, OutreachError  # noqa: E402
from orchestration.workflow import assert_gate  # noqa: E402

#: Stated wherever an approval or authorisation is printed. It is a fact about the
#: build, not a feature flag: no environment variable changes it.
DISPATCH_STATE = (
    "dispatch: disabled — this build has no sending path; outreach.dispatch raises "
    "DispatchDisabled and is not imported here"
)

MESSAGE_TABLE = "outreach_messages"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _rejection_payload(decision: Any, *, company_id: str, contact_id: str) -> dict[str, Any]:
    code = decision.code.value if decision.code is not None else ""
    return {
        "status": "rejected",
        "rejection_code": code,
        "rejection_codes": [c.value for c in decision.codes],
        "detail": decision.detail,
        "company_id": company_id,
        "contact_id": contact_id,
    }


def _print_rejection(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        _emit_json(payload)
        return
    print(f"refused: {payload['rejection_code'] or 'unknown'}", file=sys.stderr)
    if len(payload["rejection_codes"]) > 1:
        print(f"codes  : {', '.join(payload['rejection_codes'])}", file=sys.stderr)
    print(f"detail : {payload['detail']}", file=sys.stderr)
    print("nothing was written", file=sys.stderr)


def _one(client: Any, table: str, row_id: str, columns: str, what: str) -> dict[str, Any]:
    """Read exactly one row by id; absence and ambiguity are both refusals."""
    rows = client.get(table, {"id": f"eq.{row_id}", "select": columns, "limit": "2"})
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise OutreachError(f"reading {table} for {what} returned an unusable shape")
    if not rows:
        raise LookupError(f"{what} {row_id} does not exist or is not visible to this identity")
    if len(rows) > 1:
        raise OutreachError(f"{what} {row_id} matched {len(rows)} rows; refusing to guess")
    return dict(rows[0])


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------

DRAFT_TASK_TYPE = "outreach_drafting"


def _ensure_drafting_task(operator: Any, args: argparse.Namespace, agent_id: str) -> str:
    """Give the drafter the assignment its policies require, and reuse it on retry.

    Every agent read here is task-scoped: without an active task for the company, the drafter sees
    no company, no contact, no scorecard and no messages. The operator creates the assignment
    because `human_operator_all_tasks` is the only insert policy on `tasks`.
    """
    rows = operator.client.get(
        "tasks",
        {
            "company_id": f"eq.{args.company_id}",
            "assigned_actor_type": "eq.agent",
            "assigned_actor_identity": f"eq.{agent_id}",
            "status": "in.(pending,in_progress)",
            "select": "id",
            "limit": "5",
        },
    )
    if rows:
        return str(rows[0]["id"])

    created = operator.client.post(
        "tasks",
        {
            "title": f"Draft outreach for company {args.company_id}",
            "task_type": DRAFT_TASK_TYPE,
            "priority": "medium",
            "status": "pending",
            "assigned_actor_type": "agent",
            "assigned_actor_identity": agent_id,
            "company_id": args.company_id,
            "contact_id": args.contact_id,
            "campaign_id": args.campaign_id or None,
        },
    )
    if not created:
        raise OutreachError(f"the operator could not create a drafting task for {agent_id}")
    return str(created[0]["id"])


def _draft_identity(operator: Any, args: argparse.Namespace) -> Any:
    """Return the session that will author the draft.

    Drafting and approving must not be the same principal, so the draft is written by the
    ``drafter`` agent whenever its credentials exist. If they are absent the operator writes it and
    the row says so — a fallback that is visible in `drafted_by_agent_identity`, never disguised.
    """
    env = {**load_env_file(), **os.environ}
    email, password = env.get("DRAFTER_EMAIL"), env.get("DRAFTER_PASSWORD")
    if not (email and password):
        print(
            "warning: no DRAFTER_EMAIL/DRAFTER_PASSWORD, so the operator is drafting. "
            "The message will record the operator as its author and the approval will not be "
            "a second pair of eyes.",
            file=sys.stderr,
        )
        return operator

    try:
        drafter = open_agent_session(
            "drafter",
            env,
            email=email,
            password=password,
            expected_agent_id=env.get("DRAFTER_AGENT_ID") or None,
        )
    except AgentSessionError as exc:
        raise OutreachError(
            f"cannot open the drafter session: {exc}. Nothing was drafted."
        ) from exc

    task_id = _ensure_drafting_task(operator, args, drafter.agent_id)
    print(f"drafting as {drafter.agent_id} (task {task_id})", file=sys.stderr)
    args._signer_email = operator.email
    return drafter


def _cmd_draft(session: Any, args: argparse.Namespace) -> int:
    client = session.client
    reader = PostgresQualificationReader(client)
    suppression = PostgresSuppressionReader(client)
    writer = PostgresDraftWriter(client, session.user_id)

    step = int(args.step)
    template = template_for(step)
    campaign_id = args.campaign_id or None

    # Phase 1 — ask the gate before composing anything. The probe request is
    # structurally valid so the gate evaluates the *company*, not the text: a
    # rejection here is the real reason this company may not be written to, and it
    # is reported with its code instead of being hidden behind a render failure.
    probe = DraftRequest(
        company_id=args.company_id,
        contact_id=args.contact_id,
        channel=args.channel,
        outreach_event=template.outreach_event,
        subject="eligibility probe",
        body="eligibility probe",
        sequence_step=step,
        campaign_id=campaign_id,
    )
    decision = evaluate_draft_eligibility(probe, reader, suppression)
    if not decision.eligible:
        payload = _rejection_payload(
            decision, company_id=args.company_id, contact_id=args.contact_id
        )
        payload["idempotency_key"] = draft_idempotency_key(probe)
        _print_rejection(payload, as_json=args.json)
        return 1
    if decision.qualification is None:
        # The gate cannot return "eligible" without the record it evaluated; if it
        # ever does, that is a broken decision, not permission to compose.
        raise OutreachError(
            "the gate reported the company eligible but carried no scorecard; refusing to draft"
        )

    # Phase 2 — compose from the scorecard the gate just approved, and from real
    # rows only: the company's name and the contact's first name come from the
    # database, never from the command line.
    company = _one(
        client, "companies", args.company_id, "id,name,primary_domain", "company"
    )
    contact = _one(client, "contacts", args.contact_id, "id,company_id,first_name", "contact")
    if str(contact.get("company_id") or "") != str(args.company_id):
        raise OutreachError(
            f"contact {args.contact_id} belongs to company {contact.get('company_id')}, not "
            f"{args.company_id}; nothing was written"
        )

    payload = render_draft(
        company=company,
        qualification=decision.qualification,
        contact=contact,
        agent_identity=session.user_id,
        campaign_id=campaign_id,
        step=step,
        channel=args.channel,
        signer=args.signer
        if args.signer is not None
        else _default_signer(getattr(args, "_signer_email", None) or session.email),
    )

    request = DraftRequest(
        company_id=payload.company_id,
        contact_id=payload.contact_id,
        channel=payload.channel,
        outreach_event=payload.outreach_event,
        subject=payload.subject,
        body=payload.body,
        sequence_step=payload.sequence_step,
        campaign_id=payload.campaign_id,
        proposed_commercial_model=payload.proposed_commercial_model,
    )

    # Phase 3 — create_draft re-evaluates everything (content, qualification,
    # suppression) and is the only writer. Passing the pre-flight decision would
    # be trusting a stale answer.
    outcome: DraftOutcome = create_draft(request, reader, suppression, writer)
    if outcome.status == "rejected":
        _print_rejection(
            _rejection_payload(
                outcome.decision, company_id=args.company_id, contact_id=args.contact_id
            )
            | {"detail": outcome.detail},
            as_json=args.json,
        )
        return 1

    result = {
        "status": outcome.status,
        "message_id": outcome.draft_id,
        "company_id": payload.company_id,
        "contact_id": payload.contact_id,
        "campaign_id": payload.campaign_id,
        "sequence_step": payload.sequence_step,
        "channel": payload.channel,
        "subject": payload.subject,
        "allowed_because": outcome.decision.detail,
        "evidence_ids": list(outcome.decision.evidence_refs),
        "drafted_by_agent_identity": payload.drafted_by_agent_identity,
        "idempotency_key": outcome.idempotency_key,
        "content_matches": outcome.content_matches,
        "detail": outcome.detail,
        "dispatch": "disabled",
    }
    if args.json:
        _emit_json(result)
    else:
        if outcome.status == "already_exists":
            print(
                f"no draft written: a message already occupies this slot "
                f"({result['message_id']}); content_matches={outcome.content_matches}"
            )
            if outcome.detail:
                print(f"  {outcome.detail}")
            print("  run `show " + str(result["message_id"]) + "` to read what is stored")
        else:
            print(f"created message {result['message_id']}")
            print(
                f"  company {result['company_id']} / contact {result['contact_id']} / "
                f"{result['channel']} / step {result['sequence_step']}"
            )
            print(f"  subject: {result['subject']}")
            print(f"allowed because: {result['allowed_because']}")
            print(f"evidence: {', '.join(result['evidence_ids'])}")
        print(DISPATCH_STATE)
    return 0


def _default_signer(email: str) -> str:
    """The operator's own name, derived from the address they sign in with."""
    local = str(email or "").split("@", 1)[0].strip()
    return local.replace(".", " ").replace("_", " ").strip()


# ---------------------------------------------------------------------------
# list / show
# ---------------------------------------------------------------------------


def _cmd_list(session: Any, args: argparse.Namespace) -> int:
    rows = session.client.get(
        MESSAGE_TABLE,
        {
            "company_id": f"eq.{args.company_id}",
            "select": "id,contact_id,campaign_id,channel,sequence_step,approval_status,"
            "sending_status,subject,created_at",
            "order": "created_at.desc",
            "limit": str(args.limit),
        },
    )
    if not isinstance(rows, list):
        raise OutreachError("listing outreach_messages returned an unusable shape")
    payload = {"status": "ok", "company_id": args.company_id, "count": len(rows), "messages": rows}
    if args.json:
        _emit_json(payload)
        return 0
    if not rows:
        print(f"no outreach messages for company {args.company_id}")
        return 0
    print(f"{len(rows)} message(s) for company {args.company_id}")
    for row in rows:
        print(
            f"  {row.get('id')}  step {row.get('sequence_step')}  {row.get('channel')}  "
            f"{row.get('approval_status')}/{row.get('sending_status')}  "
            f"{str(row.get('subject') or '')[:70]}"
        )
    print(DISPATCH_STATE)
    return 0


def _cmd_show(session: Any, args: argparse.Namespace) -> int:
    row = _one(
        session.client,
        MESSAGE_TABLE,
        args.message_id,
        "id,company_id,contact_id,campaign_id,channel,subject,body,personalization_evidence_ids,"
        "personalization_rationale,proposed_commercial_model,approval_status,approved_by_human_id,"
        "approved_at,sending_status,sent_at,sequence_step,drafted_by_agent_identity,created_at",
        "message",
    )
    if args.json:
        _emit_json({"status": "ok", "message": row, "dispatch": "disabled"})
        return 0
    print(f"message {row.get('id')}")
    for key in (
        "company_id",
        "contact_id",
        "campaign_id",
        "channel",
        "sequence_step",
        "approval_status",
        "sending_status",
        "approved_by_human_id",
        "approved_at",
        "sent_at",
        "proposed_commercial_model",
        "drafted_by_agent_identity",
        "created_at",
    ):
        print(f"  {key:28}: {row.get(key)}")
    print(f"  personalization_evidence_ids: {row.get('personalization_evidence_ids')}")
    print(f"\nsubject: {row.get('subject')}\n")
    print(str(row.get("body") or "").rstrip())
    print("\n" + str(row.get("personalization_rationale") or ""))
    print(DISPATCH_STATE)
    return 0


# ---------------------------------------------------------------------------
# approve (G3) / authorize (G4)
# ---------------------------------------------------------------------------


def _require_draft_state(row: Mapping[str, Any]) -> None:
    status = str(row.get("approval_status") or "")
    sending = str(row.get("sending_status") or "")
    if sending != "draft":
        raise OutreachError(
            f"message {row.get('id')} has sending_status {sending!r}; only a draft may be "
            "approved or authorised here"
        )
    if status not in {"draft", "pending_human_approval"}:
        raise OutreachError(
            f"message {row.get('id')} has approval_status {status!r}; only a draft or a "
            "pending approval can be approved (rejected messages return to draft first)"
        )


def _record_activity(
    client: Any,
    *,
    company_id: str,
    contact_id: str,
    subject: str,
    description: str,
    operator_id: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Write the decision into ``activities`` — the only append-only trail we may use.

    ``outreach_messages`` has no column for "authorised"; ``audit_logs`` is written
    only by triggers and is not insertable. An activity row is the honest record:
    it names the actor, the message and the decision, and it changes nothing about
    the message itself.
    """
    rows = client.post(
        "activities",
        {
            "company_id": company_id,
            "contact_id": contact_id or None,
            "activity_type": "note",
            "subject": subject,
            "description": description,
            "performed_by_actor_type": "human",
            "performed_by_actor_identity": operator_id,
            "metadata": dict(metadata),
        },
    )
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
        raise OutreachError("the activity was not recorded; the decision has no audit trail")
    return dict(rows[0])


def _cmd_approve(session: Any, args: argparse.Namespace) -> int:
    # Gate G3 is a human decision. The session already proved the role; the gate
    # contract is asserted too, so a future caller cannot pass it with the wrong
    # claims by reusing this function.
    assert_gate(
        "G3",
        session.claims,
        evidence={"message_id": args.message_id, "reviewer": session.user_id, "decision": "approve"},
    )

    row = _one(
        session.client,
        MESSAGE_TABLE,
        args.message_id,
        "id,company_id,contact_id,approval_status,sending_status,subject",
        "message",
    )
    _require_draft_state(row)

    updated = session.client.patch(
        MESSAGE_TABLE,
        {"approval_status": "approved", "approved_by_human_id": session.user_id},
        {"id": f"eq.{args.message_id}"},
    )
    if not isinstance(updated, list) or len(updated) != 1:
        raise OutreachError(
            f"approving message {args.message_id} touched {len(updated) if isinstance(updated, list) else '?'} "
            "rows; exactly one was expected"
        )
    stored = dict(updated[0])
    if stored.get("approval_status") != "approved":
        raise OutreachError(
            f"the database did not record the approval: approval_status="
            f"{stored.get('approval_status')!r}"
        )
    if str(stored.get("approved_by_human_id") or "") != session.user_id:
        raise OutreachError("the stored approver is not the signed-in operator")
    if stored.get("sending_status") != "draft":
        raise OutreachError(
            f"approval changed the sending status to {stored.get('sending_status')!r}; "
            "approval must not queue anything"
        )

    activity = None
    if args.note:
        activity = _record_activity(
            session.client,
            company_id=str(stored.get("company_id") or ""),
            contact_id=str(stored.get("contact_id") or ""),
            subject=f"Gate G3 approval of outreach message {args.message_id}",
            description=(
                f"Approved by operator {session.user_id}. Note: {args.note.strip()}"
            ),
            operator_id=session.user_id,
            metadata={"gate": "G3", "message_id": args.message_id, "decision": "approved"},
        )

    result = {
        "status": "approved",
        "message_id": stored.get("id"),
        "approval_status": stored.get("approval_status"),
        "approved_by_human_id": stored.get("approved_by_human_id"),
        "approved_at": stored.get("approved_at"),
        "sending_status": stored.get("sending_status"),
        "note_activity_id": (activity or {}).get("id"),
        "dispatch": "disabled",
    }
    if args.json:
        _emit_json(result)
        return 0
    print(f"approved message {result['message_id']} (gate G3) by {result['approved_by_human_id']}")
    print(f"  approval_status={result['approval_status']}  sending_status={result['sending_status']}")
    if result["note_activity_id"]:
        print(f"  note recorded as activity {result['note_activity_id']}")
    print(f"  next: `authorize {result['message_id']}` records the gate G4 decision")
    print(DISPATCH_STATE)
    return 0


def _cmd_authorize(session: Any, args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc).isoformat()
    assert_gate(
        "G4",
        session.claims,
        evidence={
            "message_id": args.message_id,
            "reviewer": session.user_id,
            "authorized_at": now,
        },
    )

    row = _one(
        session.client,
        MESSAGE_TABLE,
        args.message_id,
        "id,company_id,contact_id,approval_status,approved_by_human_id,approved_at,"
        "sending_status,sent_at",
        "message",
    )
    if row.get("approval_status") != "approved":
        raise OutreachError(
            f"message {args.message_id} is {row.get('approval_status')!r}; gate G4 applies to an "
            "approved message only (approve it at G3 first)"
        )
    if not row.get("approved_by_human_id") or not row.get("approved_at"):
        raise OutreachError(
            f"message {args.message_id} carries no human approver metadata; the approval is not "
            "provable, so authorisation would be recorded against nothing"
        )
    if row.get("sending_status") != "draft":
        raise OutreachError(
            f"message {args.message_id} has sending_status {row.get('sending_status')!r}; "
            "authorisation is recorded for a draft, never for something already in flight"
        )
    if row.get("sent_at"):
        raise OutreachError(f"message {args.message_id} carries sent_at; refusing to touch it")

    activity = _record_activity(
        session.client,
        company_id=str(row.get("company_id") or ""),
        contact_id=str(row.get("contact_id") or ""),
        subject=f"Gate G4 dispatch authorisation for outreach message {args.message_id}",
        description=(
            f"Dispatch authorisation recorded by operator {session.user_id}. No message was "
            "transmitted: this build has no sending path, and authorise is a record of the "
            "decision only."
        ),
        operator_id=session.user_id,
        metadata={
            "gate": "G4",
            "message_id": args.message_id,
            "decision": "authorised",
            "authorized_at": now,
            "dispatch": "disabled",
        },
    )

    result = {
        "status": "authorised",
        "message_id": args.message_id,
        "authorisation_activity_id": activity.get("id"),
        "authorized_at": now,
        "authorised_by": session.user_id,
        "sending_status": row.get("sending_status"),
        "dispatch": "disabled",
    }
    if args.json:
        _emit_json(result)
        return 0
    print(f"dispatch authorisation recorded for message {args.message_id} (gate G4) by {session.user_id}")
    print(f"  activity {activity.get('id')}  authorised_at {now}")
    print(f"  sending_status is still {row.get('sending_status')!r}; nothing was transmitted")
    print(DISPATCH_STATE)
    return 0


# ---------------------------------------------------------------------------
# export (hand off to a human) / mark-sent (record what the human did)
# ---------------------------------------------------------------------------

EXPORT_COLUMNS = (
    "id,company_id,contact_id,channel,approval_status,sending_status,sent_at,subject,body,"
    "sent_via,external_message_id"
)

MARK_SENT_COLUMNS = (
    "id,approval_status,sending_status,sent_at,sent_via,external_message_id,sent_recorded_by"
)


def _handoff_row(session: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Load the message and its recipient address, as the operator sees them.

    The address lives on ``contacts``, not on the message, and it is read here rather than accepted
    from the command line: a handoff addressed to whatever the caller typed is not a handoff of the
    message the database approved.
    """
    row = _one(session.client, MESSAGE_TABLE, args.message_id, EXPORT_COLUMNS, "message")
    contact_id = str(row.get("contact_id") or "")
    contact = _one(
        session.client, "contacts", contact_id, "id,email,full_name,first_name,last_name", "contact"
    )
    return {**row, "to": contact.get("email"), "contact_name": contact.get("full_name")}


def _cmd_export(session: Any, args: argparse.Namespace) -> int:
    """Render an approved message for a human to send. Reads only; records nothing.

    Argument refusals come before the database read, because a bad ``--out`` has nothing to do with
    the message. The approval check is *not* here — it lives in ``outreach.handoff`` so it applies to
    every caller, not just to this command.
    """
    path = Path(args.out) if args.out else None
    if path is not None:
        if path.suffix.lower() != ".eml":
            raise OutreachError(
                f"--out {str(path)!r} does not end in .eml; this command writes an RFC 822 file "
                "that a mail client can open. Use a name like out/message.eml, or drop --out to "
                "print the copy-ready block instead."
            )
        if path.exists() and not args.force:
            raise OutreachError(
                f"{path} already exists; refusing to overwrite a file this command did not create "
                "in this run. Pass --force to replace it."
            )

    row = _handoff_row(session, args)
    handoff: dict[str, Any] = {
        "message_id": row.get("id"),
        "to": row.get("to"),
        "subject": row.get("subject"),
        "sender_mailbox": SENDER_MAILBOX,
        "recorded": "nothing",
        "dispatch": "disabled",
    }

    if path is not None:
        payload = to_eml(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        handoff |= {"status": "exported", "path": str(path), "bytes": len(payload)}
    else:
        handoff |= {"status": "rendered", "block": render_email_handoff(row)}

    if args.json:
        _emit_json(handoff)
        return 0
    if path is not None:
        print(f"wrote {handoff['path']} ({handoff['bytes']} bytes)")
        print(f"  to      : {handoff['to']}")
        print(f"  subject : {handoff['subject']}")
        print(f"  from    : {SENDER_MAILBOX} (you send it, in Gmail)")
    else:
        print(handoff["block"], end="")
    print("recorded: nothing — export only reads; nothing was stored and nothing was transmitted")
    print(DISPATCH_STATE)
    return 0


def _cmd_mark_sent(session: Any, args: argparse.Namespace) -> int:
    """Record a send the operator performed by hand, then read the row back and report it.

    The write is a function call, not a table update, because the five conditions and the activity
    row have to hold together in one transaction. What is confirmed here is the database's answer,
    not the request: if the columns did not land, this raises instead of printing success.
    """
    external = (args.external_id or "").strip()
    if not external:
        raise OutreachError(
            "--external-id is blank; paste the Message-ID (or thread id) Gmail shows for the "
            "message you actually sent, so the record can be checked against the mailbox. "
            "Nothing was recorded."
        )
    note = (args.note or "").strip() or None

    try:
        rows = session.client.rpc(
            "fn_record_manual_send",
            {"p_message": args.message_id, "p_external_id": external, "p_note": note},
        )
    except Exception as exc:  # noqa: BLE001 - see below
        # This script must not import the data-access layer (it never builds a client — another
        # test asserts that), so a database refusal is recognised by the SQLSTATE it carries rather
        # than by its class. Anything without a `code` is a bug and is re-raised untouched.
        code = getattr(exc, "code", None)
        if code is None:
            raise
        detail = getattr(exc, "message", None) or str(exc)
        raise OutreachError(f"{detail} Nothing was recorded. [{code}]") from exc
    if not isinstance(rows, list) or len(rows) != 1:
        count = len(rows) if isinstance(rows, list) else "an unusable number of"
        raise OutreachError(
            f"fn_record_manual_send returned {count} rows for message {args.message_id}; "
            "exactly one was expected, so the send was not confirmed. Re-run with --json "
            "to inspect."
        )

    stored = _one(session.client, MESSAGE_TABLE, args.message_id, MARK_SENT_COLUMNS, "message")
    if stored.get("sending_status") != "sent" or stored.get("sent_via") != "manual_gmail":
        raise OutreachError(
            f"the database does not show the manual send: sending_status="
            f"{stored.get('sending_status')!r}, sent_via={stored.get('sent_via')!r}. The record is "
            "not trustworthy; check the activity trail before retrying."
        )
    if str(stored.get("sent_recorded_by") or "") != session.user_id:
        raise OutreachError(
            f"the recorded sender is {stored.get('sent_recorded_by')!r}, not the signed-in "
            f"operator {session.user_id}; refusing to report this as your send."
        )
    if str(stored.get("external_message_id") or "") != external:
        raise OutreachError(
            f"the stored external id is {stored.get('external_message_id')!r}, not {external!r}; "
            "the record does not match what was sent."
        )

    result = {
        "status": "recorded",
        "message_id": stored.get("id"),
        "approval_status": stored.get("approval_status"),
        "sending_status": stored.get("sending_status"),
        "sent_at": stored.get("sent_at"),
        "sent_via": stored.get("sent_via"),
        "external_message_id": stored.get("external_message_id"),
        "sent_recorded_by": stored.get("sent_recorded_by"),
        "note_recorded": note is not None,
        "recorded": "manual send (activity trail, same transaction)",
        "dispatch": "disabled",
    }
    if args.json:
        _emit_json(result)
        return 0
    print(f"recorded manual send for message {result['message_id']}")
    print(f"  recorded by     : {result['sent_recorded_by']}")
    print(f"  sending_status  : {result['sending_status']}")
    print(f"  sent_via        : {result['sent_via']}")
    print(f"  external id     : {result['external_message_id']}")
    print(f"  sent_at         : {result['sent_at']}")
    print(f"  from            : {SENDER_MAILBOX} (sent by hand, not by the platform)")
    trail = "written in the same transaction"
    print(f"  activity        : {trail}{' (with your note)' if note else ''}")
    print(DISPATCH_STATE)
    return 0


# ---------------------------------------------------------------------------
# Parser / entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/outreach_cli.py",
        description=(
            "Outreach drafting surface: drafts are created as drafts, approval is a human "
            "action (G3), authorisation is recorded (G4). Nothing sends."
        ),
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="run the gate and persist one draft")
    draft.add_argument("--company-id", required=True, help="companies.id")
    draft.add_argument("--contact-id", required=True, help="contacts.id (NOT NULL on the row)")
    draft.add_argument("--campaign-id", default=None, help="outreach_campaigns.id (optional)")
    draft.add_argument("--step", type=int, default=1, help="sequence_step (>= 1)")
    draft.add_argument("--channel", default="email", choices=list(OUTREACH_CHANNELS))
    draft.add_argument(
        "--signer",
        default=None,
        help="name on the closing line (defaults to the operator's address local part)",
    )
    draft.set_defaults(func=_cmd_draft)

    listing = sub.add_parser("list", help="list outreach messages for one company")
    listing.add_argument("--company-id", required=True, help="companies.id")
    listing.add_argument("--limit", type=int, default=50)
    listing.set_defaults(func=_cmd_list)

    show = sub.add_parser("show", help="show one message in full")
    show.add_argument("message_id")
    show.set_defaults(func=_cmd_show)

    approve = sub.add_parser("approve", help="Gate G3 — operator approval of one draft")
    approve.add_argument("message_id")
    approve.add_argument("--note", default=None, help="recorded on the activity trail")
    approve.set_defaults(func=_cmd_approve)

    authorize = sub.add_parser(
        "authorize", help="Gate G4 — record the dispatch decision (nothing is sent)"
    )
    authorize.add_argument("message_id")
    authorize.set_defaults(func=_cmd_authorize)

    export = sub.add_parser(
        "export",
        help="render an approved message for a human to send by hand (records nothing)",
    )
    export.add_argument("message_id")
    export.add_argument(
        "--out",
        default=None,
        help="write an .eml here instead of printing the copy-ready block",
    )
    export.add_argument(
        "--force", action="store_true", help="replace an --out file that already exists"
    )
    export.set_defaults(func=_cmd_export)

    mark_sent = sub.add_parser(
        "mark-sent",
        help="record that a human sent an approved message by hand, with the mailbox's id",
    )
    mark_sent.add_argument("message_id")
    mark_sent.add_argument(
        "--external-id",
        required=True,
        help="Message-ID or thread id Gmail shows for the message that was sent",
    )
    mark_sent.add_argument("--note", default=None, help="recorded on the activity trail")
    mark_sent.set_defaults(func=_cmd_mark_sent)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        session = open_session()
    except (OperatorSessionError, EnvironmentGuardError) as exc:
        print(f"cannot open an operator session: {exc}", file=sys.stderr)
        return 2

    if args.func is _cmd_draft:
        try:
            session = _draft_identity(session, args)
        except (AgentSessionError, OutreachError) as exc:
            print(f"cannot draft: {exc}", file=sys.stderr)
            return 2

    try:
        return int(args.func(session, args))
    except DraftRejected as exc:
        print(
            f"refused: {', '.join(code.value for code in exc.codes)} ({exc.detail})",
            file=sys.stderr,
        )
        return 1
    except DraftGenerationError as exc:
        print(f"cannot render a draft: {exc}", file=sys.stderr)
        return 1
    except DraftRequestError as exc:
        print(f"invalid request: {exc}", file=sys.stderr)
        return 2
    except GateViolation as exc:
        print(f"gate refused: {exc}", file=sys.stderr)
        return 2
    except LookupError as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return 1
    except HandoffRefused as exc:
        print(f"cannot hand off: {exc}", file=sys.stderr)
        return 1
    except OutreachError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a CLI reports; it does not traceback at the operator
        code = getattr(exc, "code", None)
        detail = f"[{code}] " if code else ""
        print(f"error: {detail}{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
