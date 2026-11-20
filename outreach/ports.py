"""Ports for the outreach drafting stage (Workflow 4, gates G3 and G4).

Why ports and not a repository
------------------------------
Everything the gate needs to decide comes from outside this package, and every
one of those reads can fail. Naming them as Protocols keeps three failure modes
impossible to paper over:

* a qualification read that *fails* is not the same as a company that *has no*
  qualification, and neither one is permission to draft;
* a suppression lookup that errors or returns something ambiguous must never be
  mistaken for "clean" — that mistake is how an opted-out prospect gets an
  email;
* a draft write that comes back approved, queued or sent means the boundary is
  broken, not that the workflow finished.

The production implementations live in ``integrations/supabase`` (reads through
RLS, writes through the drafting agent's JWT). This module is importable with
the standard library only, so the gate and its tests never need a database.

Column names and enum values below are transcribed from ``crm/schema.sql``
(``lead_qualifications`` L514-561, ``suppression_registry`` L316-328,
``outreach_messages`` L596-646). Nothing here is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from contracts.errors import EryndexError
from intelligence.ipp.models import QUALIFICATION_REVIEW_STATUSES, QUALIFICATION_TIERS

# ---------------------------------------------------------------------------
# Enum value sets — transcribed verbatim from crm/schema.sql
# ---------------------------------------------------------------------------

# crm/schema.sql L221-226: outreach_channel_type
OUTREACH_CHANNELS: tuple[str, ...] = ("email", "linkedin_inmail", "contact_form", "other")

# crm/schema.sql L215-219: commercial_model_type. Revenue sharing is prohibited on
# this table by chk_outreach_no_revenue_sharing (L630-632) and by AGENTS.md §2;
# monthly reserved capacity is a later-stage offer (AGENTS.md §2.2), so neither
# is draftable today.
COMMERCIAL_MODELS: tuple[str, ...] = (
    "project_based_wholesale",
    "monthly_reserved_capacity",
    "strategic_revenue_sharing",
)

# The seven scored dimensions and their ``dimension_evidence`` keys, from
# crm/schema.sql L520-527 and fn_enforce_lead_qualification_rules (L1273+).
QUALIFICATION_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("customer_flow", "customer_flow_score"),
    ("development_demand", "development_demand_score"),
    ("repeatability_orders", "repeatability_orders_score"),
    ("technical_fit", "technical_fit_score"),
    ("business_size", "business_size_score"),
    ("decision_maker_accessibility", "decision_maker_accessibility_score"),
    ("commercial_fit", "commercial_fit_score"),
)

#: Every state a suppression check may legitimately report. ``ambiguous`` is a
#: first-class outcome: "we could not tell" is not "clear".
SUPPRESSION_STATES: tuple[str, ...] = ("clear", "suppressed", "ambiguous")

#: What a suppression finding points at. ``domain`` and ``company`` are kept
#: apart so an operator can tell a company-wide block from a single domain on
#: the company's domain list.
SUPPRESSION_TARGETS: tuple[str, ...] = ("contact_email", "contact_linkedin", "domain", "company")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OutreachError(EryndexError):
    """Base class for failures in this stage."""


class QualificationLookupError(OutreachError):
    """The persisted qualification could not be read.

    Raised for transport/permission failures. "There is no row" is *not* this
    error: readers return ``None`` for absence so the gate can distinguish an
    unqualified company from an unreadable one.
    """


class SuppressionLookupError(OutreachError):
    """The suppression check did not complete.

    Any raise here is treated by the gate as a rejection, never as a pass. A
    suppression service that is down must not look like a clean prospect.
    """


class DraftPersistenceError(OutreachError):
    """The writer did not persist (or did not return) a clean draft."""


# ---------------------------------------------------------------------------
# Read-side value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualificationRecord:
    """One row of ``lead_qualifications``, as the gate needs to see it.

    ``needs_human_review`` and ``insufficient_evidence`` have no columns in the
    deployed schema; readers pass them through when the scorecard object is
    still in memory (``intelligence.ipp.models.QualificationScorecard`` carries
    both) and leave them ``None`` when reading a persisted row. ``None`` means
    "not exposed by this reader", never "false".
    """

    qualification_id: str
    company_id: str
    review_status: str
    qualification_tier: str
    overall_score: float
    reviewed_by_human_id: str | None = None
    reviewed_at: str | None = None
    dimension_evidence: Mapping[str, Any] = field(default_factory=dict)
    disqualification_reasons: Sequence[str] = ()
    recommended_next_action: str = ""
    needs_human_review: bool | None = None
    insufficient_evidence: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "disqualification_reasons", tuple(self.disqualification_reasons))
        object.__setattr__(self, "dimension_evidence", dict(self.dimension_evidence or {}))

    # -- what the gate asks of a scorecard ---------------------------------

    @property
    def has_human_reviewer(self) -> bool:
        """A reviewer identity is required for approval (chk_lead_qual_review_human_gate)."""
        return bool(str(self.reviewed_by_human_id or "").strip())

    @property
    def review_status_known(self) -> bool:
        """False when the row carries a status outside ``qualification_review_status_type``."""
        return self.review_status in QUALIFICATION_REVIEW_STATUSES

    @property
    def tier_known(self) -> bool:
        """False when the row carries a tier outside ``qualification_tier_type``."""
        return self.qualification_tier in QUALIFICATION_TIERS

    @property
    def evidence_coverage(self) -> float:
        """Share of the seven dimensions that carry documented evidence (0-1).

        Computed over the fixed dimension list, not over whatever keys the row
        happens to carry, so a truncated or hand-built ``dimension_evidence``
        lowers coverage instead of inflating it.
        """
        if not QUALIFICATION_DIMENSIONS:
            return 0.0
        covered = sum(1 for key, _ in QUALIFICATION_DIMENSIONS if _has_evidence(self.dimension_evidence.get(key)))
        return round(covered / len(QUALIFICATION_DIMENSIONS), 3)

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        """Finding ids cited by the dimension evidence, deduplicated and sorted.

        These are the ``research_findings.id`` values the scorecard was built
        from; they are the only acceptable provenance for a draft.
        """
        refs: set[str] = set()
        for entry in self.dimension_evidence.values():
            if isinstance(entry, Mapping):
                for ref in entry.get("finding_ids") or ():
                    text = str(ref).strip()
                    if text:
                        refs.add(text)
        return tuple(sorted(refs))

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> QualificationRecord:
        """Build from a PostgREST row (real column names, L514-534)."""
        return cls(
            qualification_id=str(row.get("id") or ""),
            company_id=str(row.get("company_id") or ""),
            review_status=str(row.get("review_status") or ""),
            qualification_tier=str(row.get("qualification_tier") or ""),
            overall_score=float(row.get("overall_score") or 0),
            reviewed_by_human_id=(str(row["reviewed_by_human_id"]) if row.get("reviewed_by_human_id") else None),
            reviewed_at=(str(row["reviewed_at"]) if row.get("reviewed_at") else None),
            dimension_evidence=dict(row.get("dimension_evidence") or {}),
            disqualification_reasons=tuple(row.get("disqualification_reasons") or ()),
            recommended_next_action=str(row.get("recommended_next_action") or ""),
        )


def _has_evidence(entry: Any) -> bool:
    """True when a ``dimension_evidence[k]`` entry documents something.

    Mirrors the trigger's test (``->> 'evidence'`` non-empty) and accepts the
    richer shape the IPP engine writes.
    """
    if not isinstance(entry, Mapping):
        return False
    evidence = entry.get("evidence")
    if isinstance(evidence, str):
        return bool(evidence.strip())
    if evidence:
        return True
    return bool(entry.get("finding_ids"))


@dataclass(frozen=True)
class SuppressionFinding:
    """One reason the target is suppressed (or looks suppressed)."""

    target_type: str
    identifier: str
    source: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.target_type not in SUPPRESSION_TARGETS:
            raise ValueError(
                f"unknown suppression target {self.target_type!r}; allowed: {list(SUPPRESSION_TARGETS)}"
            )


@dataclass(frozen=True)
class SuppressionReport:
    """Outcome of a suppression check for one company/contact pair.

    ``ambiguous`` is reported, not raised, so the gate can record *why* it
    refused. It is a rejection either way, exactly like an exception.

    ``sources_checked`` names the sources the reader actually consulted. A
    ``clear`` verdict that names none is refused by the gate: "I looked at
    nothing and found nothing" is indistinguishable from a query that returned
    zero rows because RLS, a filter or an empty database hid them.
    """

    state: str
    findings: Sequence[SuppressionFinding] = ()
    detail: str = ""
    sources_checked: Sequence[str] = ()

    def __post_init__(self) -> None:
        if self.state not in SUPPRESSION_STATES:
            raise ValueError(
                f"unknown suppression state {self.state!r}; allowed: {list(SUPPRESSION_STATES)}"
            )
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "sources_checked", tuple(self.sources_checked))

    @property
    def is_clear(self) -> bool:
        return self.state == "clear"

    def targets(self) -> tuple[str, ...]:
        return tuple(sorted({f.target_type for f in self.findings}))


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


class QualificationReader(Protocol):
    """Read the persisted qualification for a company.

    Returns the single effective scorecard, or ``None`` when the company has
    never been scored. Raises :class:`QualificationLookupError` when the read
    itself fails — the gate must not read a failed read as "unqualified" or,
    worse, as "qualified".
    """

    def get_qualification(self, company_id: str) -> QualificationRecord | None: ...


class SuppressionReader(Protocol):
    """Check whether a company/contact pair may be contacted at all.

    The implementation owns every suppression source: ``contacts.is_opted_out``
    (L423), ``companies.is_suppressed`` (L358), ``company_domains`` (L390) and
    ``suppression_registry`` (L316). Implementations must raise
    :class:`SuppressionLookupError` on transport/permission failure and report
    ``state='ambiguous'`` when they cannot attribute a match to one target.

    Two obligations the gate cannot check for itself:

    * a ``clear`` report must name what it read in ``sources_checked`` (the gate
      refuses a clear verdict that names nothing), and
    * zero rows is not an answer. RLS narrows ``contacts`` to active task scope
      and hides opted-out rows, so a reader that maps "SELECT returned []" to
      ``clear`` reports every hidden row as clean. Read the contact row for the
      pair first and raise :class:`SuppressionLookupError` if it is invisible.

    The gate cannot do these reads itself: it has no client, and a
    caller-supplied domain would be a way around domain suppression.
    """

    def check(self, company_id: str, contact_id: str) -> SuppressionReport: ...


class DraftWriter(Protocol):
    """Persist an outreach draft (``outreach_messages``), never anything else.

    ``agent_identity`` is the verified drafting identity (from JWT claims at
    construction time, like ``QualificationRepository``); the gate copies it
    onto the payload instead of trusting a caller-supplied name.

    ``find_existing`` returns the row already stored under an idempotency key,
    or ``None``. The deployed schema has no key column, so a real writer
    recomputes :func:`outreach.gate.draft_idempotency_key` from the persisted
    row's own columns — the gap is documented in
    ``reports/eryndex/outreach-workflow.md``.
    """

    agent_identity: str

    def find_existing(self, idempotency_key: str) -> Mapping[str, Any] | None: ...

    def write(self, draft: DraftPayload) -> Mapping[str, Any]: ...


# ---------------------------------------------------------------------------
# Write-side payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftPayload:
    """Column payload for one ``outreach_messages`` row (L596-623).

    Only draft-legal values are reachable from here. There is no ``sent_at``,
    no ``sending_status`` other than ``draft``, and no approval field to fill,
    because G3 (approval) and G4 (dispatch) are human gates in
    ``orchestration/workflow.py`` and are not this package's to pass.
    """

    company_id: str
    contact_id: str
    channel: str
    subject: str
    body: str
    personalization_rationale: str
    personalization_evidence_ids: tuple[str, ...] = ()
    campaign_id: str | None = None
    sequence_step: int = 1
    proposed_commercial_model: str = "project_based_wholesale"
    approval_status: str = "draft"
    sending_status: str = "draft"
    drafted_by_agent_identity: str = ""
    outreach_event: str = ""
    idempotency_key: str = ""
    #: Structured copy of the provenance written into the rationale. The schema
    #: has no JSONB provenance column, so a real writer keeps this in memory /
    #: logs only; it exists so tests and future columns do not have to parse
    #: prose.
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.approval_status != "draft":
            raise ValueError("a draft payload cannot carry approval_status != 'draft'")
        if self.sending_status != "draft":
            raise ValueError("a draft payload cannot carry sending_status != 'draft'")
        # Only the entry model is draftable today (AGENTS.md §2). The type allows
        # the later offer, but no stage may reach it yet; refusing it here keeps
        # "only draft-legal values are reachable" true for direct constructors,
        # not just for the gate.
        if self.proposed_commercial_model != "project_based_wholesale":
            raise ValueError(
                f"{self.proposed_commercial_model} is not draftable in cold outreach; "
                "only project_based_wholesale is (strategic_revenue_sharing is prohibited)"
            )
        if not (self.body or "").strip():
            raise ValueError("a draft payload cannot carry an empty body (body is NOT NULL)")
        if not str(self.company_id or "").strip() or not str(self.contact_id or "").strip():
            raise ValueError("a draft payload needs both company_id and contact_id")
        object.__setattr__(self, "personalization_evidence_ids", tuple(self.personalization_evidence_ids))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))

    def to_row(self) -> dict[str, Any]:
        """The exact columns this stage is allowed to write."""
        return {
            "campaign_id": self.campaign_id,
            "company_id": self.company_id,
            "contact_id": self.contact_id,
            "drafted_by_agent_identity": self.drafted_by_agent_identity,
            "channel": self.channel,
            "subject": self.subject or None,
            "body": self.body,
            "personalization_evidence_ids": list(self.personalization_evidence_ids),
            "personalization_rationale": self.personalization_rationale,
            "proposed_commercial_model": self.proposed_commercial_model,
            "approval_status": self.approval_status,
            "sending_status": self.sending_status,
            "sequence_step": int(self.sequence_step),
        }
