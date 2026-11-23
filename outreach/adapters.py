"""Production implementations of the three outreach ports, against the real database.

What this module is for
-----------------------
``outreach.ports`` names three reads/writes and refuses to fake any of them. This
module is the only place those promises are kept against PostgreSQL, and it is
deliberately boring: every method either returns a value the gate can evaluate or
raises the port's typed error. There is no third outcome, because "no answer" is
indistinguishable from "clean" once it reaches the gate.

The rules implemented here are read out of the deployed SQL, not invented:

* ``lead_qualifications`` (``crm/schema.sql`` L514-561) plus the partial unique
  index ``uq_lead_qualifications_approved`` (migration ``20261114194622``) — at
  most one approved scorecard per company, which is what makes "the approved row"
  a well-defined answer.
* ``suppression_registry`` (L316-328) with ``identifier`` unique and always
  ``LOWER(TRIM(...))``; the sync triggers (L861-914, repaired by the same
  migration) write contact opt-outs as ``email``/``linkedin`` and company
  suppression as the company's ``domain``; ``companies.is_suppressed`` is the
  company-level flag itself.
* ``outreach_messages`` (L596-646), its insert policy
  ``agent_insert_outreach_messages`` as replaced by H-03 (approved,
  non-disqualified scorecard + ``lifecycle_stage IN ('qualified',
  'outreach_queued','in_outreach')``), ``chk_outreach_no_revenue_sharing`` and
  ``chk_outreach_mandatory_human_gate``.

Two consequences of the RLS policies are load-bearing and stated here because a
reader that ignored them would silently report "clear":

1. ``suppression_registry`` has no agent policy at all (L1835-1836: broad agent
   SELECT was removed to stop enumeration; agents are meant to call
   ``is_identifier_suppressed``). ``PostgresClient`` exposes tables, not RPCs, so
   an agent identity cannot check the ledger through this port. Rather than let a
   zero-row read look like a clean prospect, :class:`PostgresSuppressionReader`
   raises :class:`SuppressionLookupError` for any identity the ledger policy does
   not admit. The supported caller is the operator session (see
   ``scripts/outreach_cli.py``), which is also the only identity that can see a
   company whose ``is_suppressed`` flag is set.
2. ``agent_select_outreach_messages`` and ``agent_select_contacts`` are
   task-scoped. A row that RLS hides is indistinguishable from a row that does
   not exist, so every "single row" read below treats *invisible* as a failure to
   answer (``...LookupError``) instead of as a negative answer.

Driver types are normalised at this boundary (ADR-0003): psycopg hands back
``uuid.UUID`` and ``datetime`` objects, while the gate compares the row it gets
back against the payload's strings. ``_normalise`` converts those at the edge so
the gate's read-back checks compare like with like.
"""

from __future__ import annotations

from decimal import Decimal

from typing import Any, Mapping, Sequence
from uuid import UUID

from .gate import row_idempotency_key
from .ports import (
    DraftPayload,
    DraftPersistenceError,
    OutreachError,
    QualificationLookupError,
    QualificationRecord,
    SuppressionFinding,
    SuppressionLookupError,
    SuppressionReport,
)

#: ``is_human_operator()`` (crm/schema.sql L63-71): the roles the ledger policy admits.
HUMAN_ROLE_CLAIMS: frozenset[str] = frozenset({"operator", "admin", "founder"})

#: H-03 insert policy: a draft is legal only in these stages.
DRAFTABLE_LIFECYCLE_STAGES: tuple[str, ...] = ("qualified", "outreach_queued", "in_outreach")

#: Named in ``SuppressionReport.sources_checked`` when all four were consulted.
SUPPRESSION_SOURCES: tuple[str, ...] = (
    "contacts.is_opted_out",
    "companies.is_suppressed",
    "company_domains",
    "suppression_registry",
)

#: ``suppression_target_type`` (schema L299-306) -> the port's target vocabulary.
_REGISTRY_TARGETS: dict[str, str] = {
    "email": "contact_email",
    "linkedin": "contact_linkedin",
    "domain": "domain",
}

#: How many ``outreach_messages`` rows the writer will scan to prove a slot is free.
#: See :meth:`PostgresDraftWriter.find_existing` — the scan is bounded and its
#: incompleteness raises rather than guessing.
DEFAULT_SLOT_SCAN_LIMIT = 500


# ---------------------------------------------------------------------------
# Small, shared plumbing
# ---------------------------------------------------------------------------


def _describe(exc: BaseException, limit: int = 300) -> str:
    """``TypeName[sqlstate]: message``, truncated and stripped of control characters.

    Rejection details end up in logs and in the CLI's JSON, so provider text is
    never passed through verbatim. No credential is ever interpolated into these
    messages by this module; the scrub is here because a driver message may echo
    the statement it was given.
    """
    code = getattr(exc, "code", None) or getattr(exc, "sqlstate", None)
    label = f"{type(exc).__name__}"
    if code:
        label += f"[{code}]"
    try:
        message = str(exc)
    except Exception:  # noqa: BLE001 - an exception whose __str__ raises
        message = "<unprintable>"
    cleaned = "".join(ch for ch in message if ch >= " " or ch in "\t\n\r").strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "..."
    return f"{label}: {cleaned}" if cleaned else label


def _read(
    client: Any,
    table: str,
    params: Mapping[str, Any],
    error: type[OutreachError],
) -> list[Mapping[str, Any]]:
    """Run one ``client.get`` and normalise its failure modes into ``error``.

    Three things are refused: a raise from the client (transport, RLS, permission),
    a non-list answer, and a non-mapping row. Each is an unreadable state, and an
    unreadable state must never reach the gate looking like an empty result.
    """
    try:
        rows = client.get(table, dict(params))
    except Exception as exc:  # noqa: BLE001 - every client failure is a lookup failure
        raise error(f"reading {table} failed: {_describe(exc)}") from exc
    if not isinstance(rows, list):
        raise error(f"reading {table} returned {type(rows).__name__}, expected a list of rows")
    for row in rows:
        if not isinstance(row, Mapping):
            raise error(
                f"reading {table} returned a {type(row).__name__} row, expected a mapping"
            )
    return rows


def _single(
    client: Any,
    table: str,
    params: Mapping[str, Any],
    error: type[OutreachError],
    what: str,
) -> Mapping[str, Any] | None:
    """Read at most one row; more than one is an unreadable answer, not a choice."""
    merged = dict(params)
    merged.setdefault("limit", "2")
    rows = _read(client, table, merged, error)
    if len(rows) > 1:
        raise error(f"{what}: {table} returned {len(rows)} rows where at most one is possible")
    return rows[0] if rows else None


def _normalise(value: Any) -> Any:
    """Driver values -> the JSON-ish shape the gate and the CLI compare against."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value


def _normalise_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: _normalise(value) for key, value in row.items()}


def _required_text(value: Any, field: str, error: type[OutreachError]) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise error(f"{field} is required")
    return text


def _claims_allow_registry(claims: Any) -> bool:
    """True only for the identities ``human_operator_all_suppression`` admits."""
    if not isinstance(claims, Mapping):
        return False
    app_metadata = claims.get("app_metadata")
    if not isinstance(app_metadata, Mapping):
        return False
    return str(app_metadata.get("user_role") or "") in HUMAN_ROLE_CLAIMS


# ---------------------------------------------------------------------------
# QualificationReader
# ---------------------------------------------------------------------------


class PostgresQualificationReader:
    """The approved scorecard for a company, read under the caller's own claims.

    Absence is ``None``; anything else that is not a usable scorecard raises
    :class:`QualificationLookupError`. A pending row is deliberately *returned*,
    not filtered out: the gate has distinct codes for "not approved" and "still
    in review", and reporting those as ``qualification_missing`` would hide a
    lead that is halfway through gate G2. A disqualified row is returned for the
    same reason — ``qualification_disqualified`` is the honest answer.
    """

    def __init__(self, client: Any, *, table: str = "lead_qualifications") -> None:
        self.client = client
        self.table = table

    # -- port --------------------------------------------------------------
    def get_qualification(self, company_id: str) -> QualificationRecord | None:
        company = _required_text(company_id, "company_id", QualificationLookupError)

        # At most one approved row can exist (uq_lead_qualifications_approved, and
        # chk_lead_qual_review_human_gate guarantees a reviewer on it). Reading two
        # is a broken database or a mis-keyed query, never a tie to break here.
        approved = _read(
            self.client,
            self.table,
            {
                "company_id": f"eq.{company}",
                "review_status": "eq.approved",
                "order": "created_at.desc",
                "limit": "2",
            },
            QualificationLookupError,
        )
        if len(approved) > 1:
            raise QualificationLookupError(
                f"company {company} has {len(approved)} approved scorecards; "
                "uq_lead_qualifications_approved says at most one may exist, so this read "
                "cannot be trusted"
            )
        if approved:
            return _to_record(approved[0], company)

        # No approval: report whatever the company does have so the gate can say
        # "pending review" or "rejected" instead of "missing".
        latest = _single(
            self.client,
            self.table,
            {
                "company_id": f"eq.{company}",
                "order": "created_at.desc",
                "limit": "2",
            },
            QualificationLookupError,
            f"scorecard for company {company}",
        )
        if latest is None:
            return None
        return _to_record(latest, company)


def _to_record(row: Mapping[str, Any], company_id: str) -> QualificationRecord:
    """Validate one row and map it to the port's value object.

    The gate re-checks every field it branches on; this function's job is to make
    sure a malformed row becomes a typed lookup failure rather than an
    ``AttributeError`` inside the gate or, worse, a record whose empty
    ``dimension_evidence`` reads as "no evidence" instead of "unreadable".
    """
    row_company = str(row.get("company_id") or "").strip()
    if row_company != company_id:
        raise QualificationLookupError(
            f"scorecard for company {company_id!r} carries company_id {row_company!r}; "
            "the answer does not belong to this request"
        )

    evidence = row.get("dimension_evidence")
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, Mapping):
        raise QualificationLookupError(
            f"dimension_evidence is {type(evidence).__name__}, expected a JSON object"
        )

    reasons = row.get("disqualification_reasons")
    if reasons is None:
        reasons = ()
    if isinstance(reasons, str) or not isinstance(reasons, Sequence):
        raise QualificationLookupError(
            f"disqualification_reasons is {type(reasons).__name__}, expected an array"
        )

    score = row.get("overall_score")
    # psycopg returns NUMERIC as Decimal, so a real scorecard arrives as Decimal(60.0) rather than
    # float. Rejecting it made the first live draft fail with `qualification_lookup_failed` even
    # though the row was perfectly valid — the adapter was testing its own type assumption.
    if isinstance(score, bool) or not isinstance(score, (int, float, str, Decimal)):
        raise QualificationLookupError(
            f"overall_score is {type(score).__name__}, expected a number"
        )
    try:
        score_value = float(score)
    except (TypeError, ValueError) as exc:
        raise QualificationLookupError(f"overall_score {score!r} is not a number") from exc

    try:
        return QualificationRecord.from_row(
            {
                **dict(row),
                "overall_score": score_value,
                "dimension_evidence": dict(evidence),
                "disqualification_reasons": [str(reason) for reason in reasons],
            }
        )
    except (TypeError, ValueError) as exc:
        raise QualificationLookupError(f"scorecard row cannot be read: {_describe(exc)}") from exc


# ---------------------------------------------------------------------------
# SuppressionReader
# ---------------------------------------------------------------------------


class PostgresSuppressionReader:
    """Answer the gate's suppression question from the four deployed sources.

    ``registry_readable`` defaults to what the client's claims say
    (``is_human_operator()``); pass it explicitly only when the caller already
    knows the answer — tests, or a session whose claims were asserted out of band.

    The company-level flag is checked before the ledger because it is the
    company-wide decision; a company that is suppressed is also *hidden* from a
    task-scoped agent read, so its absence would otherwise be indistinguishable
    from a missing company.
    """

    def __init__(self, client: Any, *, registry_readable: bool | None = None) -> None:
        self.client = client
        self.registry_readable = (
            _claims_allow_registry(getattr(client, "claims", None))
            if registry_readable is None
            else bool(registry_readable)
        )

    # -- port --------------------------------------------------------------
    def check(self, company_id: str, contact_id: str) -> SuppressionReport:
        company = _required_text(company_id, "company_id", SuppressionLookupError)
        contact = _required_text(contact_id, "contact_id", SuppressionLookupError)

        if not self.registry_readable:
            # Saying "clear" here would mean "I could not look, so you are fine".
            raise SuppressionLookupError(
                "this identity may not read suppression_registry and PostgresClient exposes no "
                "call path to is_identifier_suppressed(), so a negative answer cannot be "
                "distinguished from an unreadable one; run the draft check from an operator "
                "session (see scripts/outreach_cli.py)"
            )

        contact_row = self._contact(contact)
        contact_company = str(contact_row.get("company_id") or "").strip()
        if contact_company != company:
            raise SuppressionLookupError(
                f"contact {contact} belongs to company {contact_company!r}, not {company!r}; "
                "the suppression check cannot be attributed"
            )

        company_row = self._company(company)
        findings: list[SuppressionFinding] = []

        if _is_true(contact_row.get("is_opted_out")):
            findings.append(
                SuppressionFinding(
                    target_type="contact_email",
                    identifier=str(contact_row.get("email") or contact),
                    source="contacts.is_opted_out",
                    detail="contact has opted out (contacts.is_opted_out = true)",
                )
            )

        if _is_true(company_row.get("is_suppressed")):
            # Company suppression is company-wide: the sync trigger mirrors it onto
            # the domain, so also reporting "domain suppressed" would bury the real
            # reason behind a narrower code.
            findings.append(
                SuppressionFinding(
                    target_type="company",
                    identifier=company,
                    source="companies.is_suppressed",
                    detail=(
                        "company is flagged is_suppressed"
                        + (f": {company_row.get('suppression_reason')}" if company_row.get("suppression_reason") else "")
                    ),
                )
            )
            return SuppressionReport(
                state="suppressed",
                findings=tuple(findings),
                detail="company-level suppression: " + "; ".join(f.detail for f in findings),
                sources_checked=("contacts.is_opted_out", "companies.is_suppressed"),
            )

        domains = self._domains(company, primary_domain=str(company_row.get("primary_domain") or ""))
        if not domains:
            raise SuppressionLookupError(
                f"company {company} exposes no domain at all; domain suppression cannot be "
                "checked and an empty check is not a clear answer"
            )

        identifiers: list[tuple[str, str]] = []
        email = str(contact_row.get("email") or "").strip()
        linkedin = str(contact_row.get("linkedin_url") or "").strip()
        if email:
            identifiers.append(("contact_email", email))
        if linkedin:
            identifiers.append(("contact_linkedin", linkedin))
        identifiers.extend(("domain", domain) for domain in domains)

        for expected_target, identifier in identifiers:
            for row in self._registry_rows(identifier):
                finding = _registry_finding(row, identifier, expected_target)
                findings.append(finding)

        if findings:
            return SuppressionReport(
                state="suppressed",
                findings=tuple(findings),
                detail="; ".join(f"{f.target_type} {f.identifier}: {f.detail}" for f in findings),
                sources_checked=SUPPRESSION_SOURCES,
            )

        return SuppressionReport(state="clear", sources_checked=SUPPRESSION_SOURCES)

    # -- reads -------------------------------------------------------------
    def _contact(self, contact_id: str) -> Mapping[str, Any]:
        row = _single(
            self.client,
            "contacts",
            {
                "id": f"eq.{contact_id}",
                "select": "id,company_id,email,linkedin_url,is_opted_out,opt_out_at",
                "limit": "2",
            },
            SuppressionLookupError,
            f"contact {contact_id}",
        )
        if row is None:
            # RLS hides opted-out contacts (agent_select_contacts, L1838-1851) and
            # contacts outside the caller's task scope. An invisible contact is not
            # a clear one.
            raise SuppressionLookupError(
                f"contact {contact_id} is not visible to this identity; the row is either "
                "absent, opted out, or outside the caller's task scope, and none of those "
                "is permission to draft"
            )
        return row

    def _company(self, company_id: str) -> Mapping[str, Any]:
        row = _single(
            self.client,
            "companies",
            {
                "id": f"eq.{company_id}",
                "select": "id,primary_domain,is_suppressed,suppression_reason",
                "limit": "2",
            },
            SuppressionLookupError,
            f"company {company_id}",
        )
        if row is None:
            raise SuppressionLookupError(
                f"company {company_id} is not visible to this identity (a suppressed company "
                "is hidden from task-scoped agent reads); refusing to report a clear prospect"
            )
        return row

    def _domains(self, company_id: str, *, primary_domain: str) -> list[str]:
        rows = _read(
            self.client,
            "company_domains",
            {"company_id": f"eq.{company_id}", "select": "domain,is_primary", "limit": "50"},
            SuppressionLookupError,
        )
        domains: list[str] = []
        for candidate in [primary_domain, *(str(row.get("domain") or "") for row in rows)]:
            cleaned = candidate.strip().lower()
            if cleaned and cleaned not in domains:
                domains.append(cleaned)
        return domains

    def _registry_rows(self, identifier: str) -> list[Mapping[str, Any]]:
        cleaned = identifier.strip().lower()
        if not cleaned:
            return []
        rows = _read(
            self.client,
            "suppression_registry",
            {
                "identifier": f"eq.{cleaned}",
                "select": "target_type,identifier,reason,source",
                "limit": "2",
            },
            SuppressionLookupError,
        )
        if len(rows) > 1:
            # identifier is UNIQUE (L319); two rows mean the answer cannot be trusted.
            raise SuppressionLookupError(
                f"suppression_registry returned {len(rows)} rows for one identifier; the ledger "
                "is not readable as a single answer"
            )
        return rows

# -- mapping ----------------------------------------------------------------


def _is_true(value: Any) -> bool:
    if value is None:
        return False
    if value is True:
        return True
    return str(value).strip().lower() in {"t", "true", "1"}


def _registry_finding(
    row: Mapping[str, Any], identifier: str, expected_target: str
) -> SuppressionFinding:
    """Map one registry row to a port finding; an unattributable row is a failure.

    ``suppression_registry.target_type`` is ``email|domain|linkedin|phone|other``;
    the port speaks ``contact_email|contact_linkedin|domain|company``. A row whose
    type does not match the identifier we looked up (or is ``phone``/``other``)
    cannot be attributed, and guessing would put the wrong code on the rejection.
    """
    target_type = str(row.get("target_type") or "").strip()
    mapped = _REGISTRY_TARGETS.get(target_type)
    if mapped is None or mapped != expected_target:
        raise SuppressionLookupError(
            f"suppression_registry holds identifier {identifier!r} under target_type "
            f"{target_type!r}, which does not match the {expected_target!r} identifier that was "
            "looked up; the answer cannot be attributed"
        )
    detail = f"on the suppression ledger as {target_type}"
    if row.get("reason"):
        detail += f" ({row.get('reason')})"
    if row.get("source"):
        detail += f", source={row.get('source')}"
    return SuppressionFinding(
        target_type=mapped,
        identifier=str(row.get("identifier") or identifier),
        source="suppression_registry",
        detail=detail,
    )


# ---------------------------------------------------------------------------
# DraftWriter
# ---------------------------------------------------------------------------


class PostgresDraftWriter:
    """Insert one ``outreach_messages`` row as a draft, and nothing else.

    ``agent_identity`` is the verified identity of the session that constructed
    the client (from JWT claims, never from the payload). The writer stamps it on
    the row and refuses a payload that carries a different one, because
    ``drafted_by_agent_identity`` is what the insert policy compares against
    ``current_agent_identity()``.

    Before writing, the row's ownership is validated against the database:
    company exists and is in a draftable lifecycle stage with no suppression flag,
    contact exists, belongs to that company, and has not opted out, and a named
    campaign exists and is active. The composite FK and H-03 policy would refuse
    most of these anyway; doing it here turns a 42501/23503 into a sentence that
    names the fact.
    """

    def __init__(
        self,
        client: Any,
        agent_identity: str,
        *,
        slot_scan_limit: int = DEFAULT_SLOT_SCAN_LIMIT,
    ) -> None:
        identity = str(agent_identity or "").strip()
        if not identity:
            raise DraftPersistenceError(
                "a draft writer needs the verified drafting identity from its session claims"
            )
        if slot_scan_limit < 1:
            raise DraftPersistenceError("slot_scan_limit must be >= 1")
        self.client = client
        self.agent_identity = identity
        self.slot_scan_limit = int(slot_scan_limit)
        self._seen: dict[str, dict[str, Any]] = {}

    # -- port --------------------------------------------------------------
    def find_existing(self, idempotency_key: str) -> Mapping[str, Any] | None:
        """Return the row occupying this slot, or ``None``.

        There is no idempotency column (schema L596-646), so the key is recomputed
        from each candidate row's own identity columns with
        :func:`outreach.gate.row_idempotency_key` — the single definition of a slot.
        The scan is bounded: if it fills the window, absence cannot be proven and
        this raises instead of answering "free" and risking a duplicate.
        """
        key = str(idempotency_key or "").strip()
        if not key:
            raise DraftPersistenceError("find_existing needs the idempotency key the gate computed")
        if key in self._seen:
            return _normalise_row(self._seen[key])

        rows = _read(
            self.client,
            "outreach_messages",
            {
                "select": "id,company_id,contact_id,campaign_id,channel,sequence_step,subject,body",
                "order": "created_at.desc",
                "limit": str(self.slot_scan_limit),
            },
            DraftPersistenceError,
        )
        for row in rows:
            try:
                recomputed = row_idempotency_key(row)
            except (TypeError, ValueError) as exc:
                raise DraftPersistenceError(
                    "an outreach_messages row cannot be keyed from its own columns "
                    f"(company_id, campaign_id, channel, sequence_step): {_describe(exc)}"
                ) from exc
            if recomputed == key:
                normalised = _normalise_row(row) or {}
                self._seen[key] = normalised
                return normalised
        if len(rows) >= self.slot_scan_limit:
            raise DraftPersistenceError(
                f"cannot prove the slot is free: the scan window of {self.slot_scan_limit} "
                "outreach_messages rows is full. Raise slot_scan_limit rather than risking a "
                "duplicate draft."
            )
        return None

    def write(self, draft: DraftPayload) -> Mapping[str, Any]:
        """Insert the draft row and return it as read back from the database."""
        if not isinstance(draft, DraftPayload):
            raise DraftPersistenceError(
                f"write() needs a DraftPayload, got {type(draft).__name__}"
            )
        # Last line before the database: the payload type already refuses these,
        # but a hand-built instance could have been created with object.__setattr__.
        if draft.approval_status != "draft" or draft.sending_status != "draft":
            raise DraftPersistenceError(
                "refusing to write a row that is not a draft: "
                f"approval_status={draft.approval_status!r}, sending_status={draft.sending_status!r}"
            )
        if str(draft.drafted_by_agent_identity) != self.agent_identity:
            raise DraftPersistenceError(
                "payload identity "
                f"{draft.drafted_by_agent_identity!r} is not this writer's verified identity "
                f"{self.agent_identity!r}; the insert policy compares both"
            )
        if not draft.personalization_evidence_ids:
            raise DraftPersistenceError(
                "refusing to write a draft with no personalization_evidence_ids: the row would "
                "carry no machine-checkable provenance"
            )

        self._require_company(draft.company_id)
        self._require_contact(draft.contact_id, draft.company_id)
        if draft.campaign_id:
            self._require_campaign(draft.campaign_id)

        row = self._insert(draft.to_row())
        persisted = self._verify(row, draft)
        if draft.idempotency_key:
            self._seen[str(draft.idempotency_key)] = persisted
        return persisted

    # -- ownership checks --------------------------------------------------
    def _require_company(self, company_id: str) -> None:
        row = _single(
            self.client,
            "companies",
            {
                "id": f"eq.{company_id}",
                "select": "id,lifecycle_stage,is_suppressed,primary_domain",
                "limit": "2",
            },
            DraftPersistenceError,
            f"company {company_id}",
        )
        if row is None:
            raise DraftPersistenceError(
                f"company {company_id} is not visible to this identity; a draft cannot be owned "
                "by a company the writer cannot read"
            )
        if _is_true(row.get("is_suppressed")):
            raise DraftPersistenceError(
                f"company {company_id} is suppressed; no draft may be created for it"
            )
        stage = str(row.get("lifecycle_stage") or "").strip()
        if stage not in DRAFTABLE_LIFECYCLE_STAGES:
            raise DraftPersistenceError(
                f"company {company_id} is at lifecycle_stage {stage!r}; a draft requires one of "
                f"{list(DRAFTABLE_LIFECYCLE_STAGES)} (H-03 insert policy, "
                "db/migrations/20261114194622_hardening_batch1.sql)"
            )

    def _require_contact(self, contact_id: str, company_id: str) -> None:
        row = _single(
            self.client,
            "contacts",
            {
                "id": f"eq.{contact_id}",
                "select": "id,company_id,is_opted_out,email",
                "limit": "2",
            },
            DraftPersistenceError,
            f"contact {contact_id}",
        )
        if row is None:
            raise DraftPersistenceError(
                f"contact {contact_id} is not visible to this identity; outreach_messages.contact_id "
                "is NOT NULL and the recipient must exist"
            )
        owner = str(row.get("company_id") or "").strip()
        if owner != str(company_id):
            raise DraftPersistenceError(
                f"contact {contact_id} belongs to company {owner!r}, not {company_id!r}; "
                "fk_outreach_contact_company would refuse this row"
            )
        if _is_true(row.get("is_opted_out")):
            raise DraftPersistenceError(
                f"contact {contact_id} has opted out; a draft addressed to them must not exist"
            )

    def _require_campaign(self, campaign_id: str) -> None:
        row = _single(
            self.client,
            "outreach_campaigns",
            {"id": f"eq.{campaign_id}", "select": "id,name,is_active", "limit": "2"},
            DraftPersistenceError,
            f"campaign {campaign_id}",
        )
        if row is None:
            raise DraftPersistenceError(
                f"campaign {campaign_id} does not exist or is not visible; pass --campaign-id "
                "only for a real campaign"
            )
        if not _is_true(row.get("is_active")):
            raise DraftPersistenceError(
                f"campaign {campaign_id} is inactive; drafting into a closed campaign would file "
                "the message where nobody is working it"
            )

    # -- write / read-back -------------------------------------------------
    def _insert(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            rows = self.client.post("outreach_messages", dict(row))
        except Exception as exc:  # noqa: BLE001 - every write failure is a persistence failure
            raise DraftPersistenceError(
                "inserting the outreach draft failed: " + _describe(exc)
            ) from exc
        if not isinstance(rows, list) or not rows:
            raise DraftPersistenceError(
                "the insert returned no row; the draft is not proven to exist"
            )
        if not isinstance(rows[0], Mapping):
            raise DraftPersistenceError(
                f"the insert returned a {type(rows[0]).__name__}, expected a mapping"
            )
        return rows[0]

    def _verify(self, row: Mapping[str, Any], draft: DraftPayload) -> dict[str, Any]:
        """Read back what the database stored; a non-draft row is an integrity failure."""
        persisted = _normalise_row(row) or {}
        if not str(persisted.get("id") or "").strip():
            raise DraftPersistenceError("the inserted row carries no id")
        if persisted.get("approval_status") != "draft":
            raise DraftPersistenceError(
                f"the stored row is not a draft: approval_status={persisted.get('approval_status')!r}"
            )
        if persisted.get("sending_status") != "draft":
            raise DraftPersistenceError(
                f"the stored row is not a draft: sending_status={persisted.get('sending_status')!r}"
            )
        if persisted.get("approved_by_human_id") or persisted.get("approved_at") or persisted.get("sent_at"):
            raise DraftPersistenceError("the stored row carries approval or delivery metadata")
        if str(persisted.get("company_id") or "") != draft.company_id:
            raise DraftPersistenceError("the stored row belongs to a different company")
        if str(persisted.get("contact_id") or "") != draft.contact_id:
            raise DraftPersistenceError("the stored row is addressed to a different contact")
        if str(persisted.get("campaign_id") or "") != str(draft.campaign_id or ""):
            raise DraftPersistenceError("the stored row carries a different campaign_id")
        if (persisted.get("channel") or "") != draft.channel:
            raise DraftPersistenceError("the stored row carries a different channel")
        try:
            step = int(persisted.get("sequence_step") or 0)
        except (TypeError, ValueError) as exc:
            raise DraftPersistenceError(
                f"the stored row carries a non-integer sequence_step: {persisted.get('sequence_step')!r}"
            ) from exc
        if step != int(draft.sequence_step):
            raise DraftPersistenceError("the stored row carries a different sequence_step")
        if str(persisted.get("body") or "") != draft.body:
            raise DraftPersistenceError("the stored body differs from the text that passed the gate")
        if str(persisted.get("subject") or "") != str(draft.subject or ""):
            raise DraftPersistenceError("the stored subject differs from the text that passed the gate")
        if [str(item) for item in (persisted.get("personalization_evidence_ids") or [])] != list(
            draft.personalization_evidence_ids
        ):
            raise DraftPersistenceError("the stored personalization_evidence_ids differ from the cited evidence")
        if str(persisted.get("personalization_rationale") or "") != draft.personalization_rationale:
            raise DraftPersistenceError("the stored personalization_rationale differs from the gate's provenance")
        if persisted.get("proposed_commercial_model") != draft.proposed_commercial_model:
            raise DraftPersistenceError("the stored row carries a different proposed_commercial_model")
        if str(persisted.get("drafted_by_agent_identity") or "") != self.agent_identity:
            raise DraftPersistenceError("the stored row was drafted by a different identity")
        return persisted
