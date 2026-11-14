"""The outreach drafting gate: qualified lead -> human approval -> suppression -> draft.

Why this module exists
----------------------
The deployed schema can already refuse to *queue* an unapproved message
(``fn_enforce_outreach_human_approval``, ``crm/schema.sql`` L1231-1254) and,
once ``20261113_hardening_batch1.sql`` is applied, refuse to store a draft for a
company without an approved scorecard (H-03). Neither check can see the things
that make a draft wrong *before* it exists:

* an "approved" scorecard that was never measured (coverage 0, or the
  ``insufficient_evidence`` flag the persistence layer is supposed to refuse);
* a suppression lookup that errored, so nobody knows whether the prospect opted
  out — the database trigger only re-checks suppression at dispatch time, using
  the registry, and an error there is a 500 rather than a silent draft;
* a body that offers revenue sharing or claims delivery capacity the business
  does not have (AGENTS.md §1.2, §11).

So the decision is made here, in a pure function over injected ports, and every
uncertainty resolves to a rejection. The gate has no client, no credentials and
no sender: ``outreach.dispatch`` is a raising stub and is never imported here.

Order of evaluation (deliberate)
--------------------------------
1. draft content the agent controls (it is pointless to read the CRM for a body
   that may not be sent at all);
2. the persisted qualification;
3. suppression — only if 1 and 2 passed. Skipping the lookup after an earlier
   rejection is safe (the outcome is a rejection either way) and keeps the
   decision independent of a flaky third party.

Rejections are reported as a *tuple* of codes: an auditor wants every reason,
not just the first one. ``EligibilityDecision.code`` is the primary reason for
logging and tests.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from contracts.errors import EryndexError
from contracts.keys import content_key
from intelligence.ipp.rubric import RUBRIC_VERSION, tier_for_score

from .ports import (
    COMMERCIAL_MODELS,
    OUTREACH_CHANNELS,
    DraftPayload,
    DraftPersistenceError,
    DraftWriter,
    QualificationReader,
    QualificationRecord,
    SuppressionReader,
    SuppressionReport,
)

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class DraftRequestError(ValueError):
    """The request is not evaluable at all (as opposed to not approvable).

    Structural problems raise; content problems are *decisions*, because a
    rejection with a code is auditable and a traceback is not.
    """


@dataclass(frozen=True)
class DraftRequest:
    """One intended outreach event for one contact.

    ``outreach_event`` names the intent (``initial_partnership_intro``,
    ``follow_up_after_silence``...). Together with ``company_id`` and
    ``channel`` it is the identity of the draft: re-running the drafter for the
    same event must not produce a second message.
    """

    company_id: str
    contact_id: str
    channel: str
    outreach_event: str
    subject: str = ""
    body: str = ""
    sequence_step: int = 1
    campaign_id: str | None = None
    proposed_commercial_model: str = "project_based_wholesale"

    def __post_init__(self) -> None:
        for name in ("company_id", "contact_id", "outreach_event"):
            if not str(getattr(self, name) or "").strip():
                raise DraftRequestError(f"{name} is required")
        # Every text field below is fed to a regular expression and to the row
        # payload. A non-str (bytes from a mis-decoded body, an int from a form
        # field) would raise a TypeError deep inside a guard instead of a
        # structural rejection here, so the type is checked at construction.
        for name in ("company_id", "contact_id", "outreach_event", "subject", "body"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise DraftRequestError(f"{name} must be a string, not {type(value).__name__}")
        if self.campaign_id is not None and not isinstance(self.campaign_id, str):
            raise DraftRequestError(
                f"campaign_id must be a string or None, not {type(self.campaign_id).__name__}"
            )
        if self.channel not in OUTREACH_CHANNELS:
            raise DraftRequestError(
                f"unknown channel {self.channel!r}; allowed: {list(OUTREACH_CHANNELS)} "
                "(outreach_channel_type, crm/schema.sql L221-226)"
            )
        if self.proposed_commercial_model not in COMMERCIAL_MODELS:
            raise DraftRequestError(
                f"unknown commercial model {self.proposed_commercial_model!r}; "
                f"allowed: {list(COMMERCIAL_MODELS)}"
            )
        if isinstance(self.sequence_step, bool):
            raise DraftRequestError("sequence_step must be an integer, not a boolean")
        try:
            step = int(self.sequence_step)
        except (TypeError, ValueError) as exc:
            raise DraftRequestError(
                f"sequence_step must be an integer, not {type(self.sequence_step).__name__}"
            ) from exc
        if step < 1:
            raise DraftRequestError("sequence_step must be >= 1 (chk_outreach_sequence_step)")


# ---------------------------------------------------------------------------
# Rejection codes — a closed set
# ---------------------------------------------------------------------------


class RejectionCode(str, Enum):
    """Every reason this gate refuses to create a draft.

    Closed on purpose: callers branch on these, dashboards count them, and a new
    reason needs a new code rather than a new string in a log line.
    """

    # -- qualification ----------------------------------------------------
    QUALIFICATION_MISSING = "qualification_missing"
    QUALIFICATION_LOOKUP_FAILED = "qualification_lookup_failed"
    QUALIFICATION_UNMEASURED = "qualification_unmeasured"
    QUALIFICATION_DISQUALIFIED = "qualification_disqualified"
    QUALIFICATION_PENDING_REVIEW = "qualification_pending_review"
    QUALIFICATION_NOT_APPROVED = "qualification_not_approved"
    QUALIFICATION_COMPANY_MISMATCH = "qualification_company_mismatch"
    QUALIFICATION_TIER_INCONSISTENT = "qualification_tier_inconsistent"
    HUMAN_REVIEWER_MISSING = "human_reviewer_missing"
    # -- suppression ------------------------------------------------------
    SUPPRESSED_CONTACT = "suppressed_contact"
    SUPPRESSED_DOMAIN = "suppressed_domain"
    SUPPRESSED_COMPANY = "suppressed_company"
    SUPPRESSION_LOOKUP_FAILED = "suppression_lookup_failed"
    SUPPRESSION_AMBIGUOUS = "suppression_ambiguous"
    # -- provenance -------------------------------------------------------
    PROVENANCE_INCOMPLETE = "provenance_incomplete"
    # -- draft content ----------------------------------------------------
    DRAFT_CONTENT_INVALID = "draft_content_invalid"
    REVENUE_SHARING_PROHIBITED = "revenue_sharing_prohibited"
    UNSUPPORTED_DELIVERY_CLAIM = "unsupported_delivery_claim"
    COMMERCIAL_MODEL_PREMATURE = "commercial_model_premature"


REJECTION_CODES: frozenset[str] = frozenset(code.value for code in RejectionCode)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityDecision:
    """Whether a draft may exist, and why not."""

    codes: tuple[RejectionCode, ...] = ()
    detail: str = ""
    qualification: QualificationRecord | None = None
    evidence_refs: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return not self.codes

    @property
    def code(self) -> RejectionCode | None:
        """Primary reason (the first one found); ``None`` when eligible."""
        return self.codes[0] if self.codes else None

    def raise_if_rejected(self) -> None:
        """For callers that treat a rejection as an error rather than an outcome."""
        if self.codes:
            raise DraftRejected(self.codes, self.detail)


class DraftRejected(EryndexError):
    """Raised by :meth:`EligibilityDecision.raise_if_rejected`."""

    def __init__(self, codes: Sequence[RejectionCode], detail: str = "") -> None:
        self.codes = tuple(codes)
        self.detail = detail
        super().__init__(f"draft rejected: {', '.join(c.value for c in self.codes)} ({detail})")


@dataclass(frozen=True)
class DraftOutcome:
    """Result of :func:`create_draft`.

    ``created`` is the only status that implies a row was written. ``rejected``
    and ``already_exists`` both wrote nothing.
    """

    status: str
    decision: EligibilityDecision
    idempotency_key: str = ""
    draft_id: str | None = None
    payload: DraftPayload | None = None
    detail: str = ""
    #: ``already_exists`` means the *slot* is taken, not that the stored draft is
    #: the one this request asked for. A re-draft with different body/subject or
    #: another recipient is a conflict, not a duplicate, and is reported as such.
    content_matches: bool = True

    @property
    def created(self) -> bool:
        return self.status == "created"


# ---------------------------------------------------------------------------
# Content guards
# ---------------------------------------------------------------------------

#: Claims this business cannot make yet: it is pre-operational and the delivery
#: team is not onboarded (AGENTS.md §1.2, §11). Lexical and deliberately
#: conservative — a false positive costs a rephrase, a false negative costs
#: credibility. Human review at G3 remains the authority on everything else.
_UNSUPPORTED_DELIVERY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bour\s+(?:engineering\s+)?(?:team|bench|capacity)\b", "claims a team"),
    (r"\bteam\s+of\s+\d+", "claims a team size"),
    (r"\bteam\s+of\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", "claims a team size"),
    (r"\b\d+\+?\s+(?:senior\s+)?(?:developers|engineers|designers|specialists)\b", "claims headcount"),
    (
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(?:senior\s+)?(?:developers|engineers|designers|specialists)\b",
        "claims headcount",
    ),
    (r"\bwe\s+have\s+(?:delivered|shipped|built\s+for|worked\s+with)\b", "claims a track record"),
    (r"\bwe\s+(?:built|delivered|shipped|launched)\b", "claims a track record"),
    (r"\b(?:our|previous)\s+(?:clients|portfolio|case\s+studies)\b", "claims a client list"),
    (r"\bour\s+(?:client|customer)\b", "claims a client list"),
    (r"\btrack\s+record\b", "claims a track record"),
    (r"\bdedicated\s+(?:capacity|team|resources)\b", "claims reserved capacity"),
    (r"\bcapacity\s+(?:available|to\s+take\s+on)\b", "claims available capacity"),
    (r"\b(?:immediate|guaranteed)\s+(?:availability|delivery|start)\b", "claims delivery availability"),
    (r"\b(?:start|begin|deliver)\s+immediately\b", "claims delivery availability"),
    (r"\bguaranteed?\b", "guarantees delivery"),
)

_REVENUE_SHARING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brevenue[\s-]?shar(?:e|ing)\b", "mentions revenue sharing"),
    (r"\brev[\s-]?share\b", "mentions revenue sharing"),
    (r"\brevenue[\s-]?(?:split|percentage|cut)\b", "mentions revenue splitting"),
    (r"\b(?:split|share|percentage|cut)\s+(?:of\s+)?(?:the\s+)?revenue\b", "mentions revenue splitting"),
    (r"\bprofit[\s-]?(?:split|shar(?:e|ing))\b", "mentions profit splitting"),
    (r"\bequity\b", "mentions equity"),
)

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: Characters that must never reach a stored draft: CR/LF in a subject is SMTP
#: header injection, and the rest are terminal/log injection. A body may carry
#: ``\n``, ``\r`` and ``\t``; nothing else below 0x20, and no C1 control
#: (0x7f-0x9f) either -- NEL (0x85) is treated as a line break by several
#: lenient header parsers and is never legitimate in a plain-text message.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
#: A subject is one line. Besides CR/LF this covers the Unicode line and
#: paragraph separators, which a naive renderer turns into a new line.
_LINE_BREAK_RE = re.compile(r"[\r\n\u2028\u2029]")

#: Credential-shaped fragments in a provider exception message. Adapters are
#: supposed to normalise failures (ADR-0003), but an unwrapped PostgREST or
#: httpx error carries the request URL, and the URL carries the key. A rejection
#: detail is logged by every caller, so it is redacted here rather than trusted.
_CREDENTIAL_RE = re.compile(
    r"(?i)\b(apikey|api_key|access_token|refresh_token|token|password|secret|authorization|bearer)\b"
    r"\s*[=:]\s*[^\s,;&\"']+"
)
#: ``Authorization`` headers are usually rendered as ``Bearer <jwt>`` with no
#: ``=``/``:`` in front of the token, which the pattern above cannot see.
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")

#: Invisible characters an agent can use to break a keyword without changing
#: what a human reads: zero-width spaces/joiners, word joiner, BOM, soft hyphen.
#: This is the documented subset only -- :func:`normalize_for_matching` also
#: strips every remaining Unicode ``Cf`` (format) character, so a separator this
#: table has never heard of (U+180E, U+2061-U+2064, ...) cannot break a keyword.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"), None)

#: Hyphen/dash lookalikes, folded to ASCII so ``revenue‑share`` matches.
_DASHES = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2043": "-",
        "\u02d7": "-",
        "\ufe63": "-",
        "\uff0d": "-",
    }
)

#: Cyrillic/Greek letters that render as Latin ones. NFKC does not fold scripts,
#: so a homoglyph would otherwise evade every lexical guard while reading
#: identically to a human. Only the letters our patterns need are mapped.
_CONFUSABLES = str.maketrans(
    {
        "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
        "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0455": "s", "\u0501": "d",
        "\u043a": "k", "\u043c": "m", "\u043d": "h", "\u0442": "t", "\u0432": "b",
        "\u0433": "r", "\u0438": "u", "\u0458": "j", "\u04bb": "h", "\u044c": "b",
        "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k", "\u03bd": "v",
        "\u03bf": "o", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u", "\u03c7": "x",
    }
)


def normalize_for_matching(text: str) -> str:
    """Fold text to a comparison skeleton before any lexical guard.

    Unicode-normalises, removes every format (``Cf``) character -- not only the
    ones named in ``_INVISIBLE`` -- unifies dash/hyphen lookalikes and folds the
    Cyrillic/Greek letters that render as Latin. The guards then see what a human
    reader sees, which is the only thing that matters for a claim that must never
    be made.

    Case folding happens *before* the confusable table, not after: ``casefold``
    maps an uppercase Cyrillic letter (``О`` U+041E) onto the lowercase form
    (``о`` U+043E), and the table only carries the lowercase mapping. Folding the
    other way round left ``Оur team`` and ``Тen developers`` looking like plain
    Cyrillic text to every pattern.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    folded = folded.translate(_INVISIBLE).translate(_DASHES).casefold()
    return folded.translate(_CONFUSABLES)


def _matches(patterns: Sequence[tuple[str, str]], text: str) -> list[str]:
    normalized = normalize_for_matching(text)
    return [label for pattern, label in patterns if re.search(pattern, normalized)]


def _describe_exception(exc: BaseException, limit: int = 300) -> str:
    """``TypeName: message`` with credentials and control characters removed.

    The type name is what a caller branches on; the message is kept because it
    is the only diagnosis available, but truncated and scrubbed. ``str`` itself
    may raise on a hostile object, so that is contained too.
    """
    try:
        message = str(exc)
    except Exception:  # noqa: BLE001 - an exception whose __str__ raises
        return f"{type(exc).__name__}: <unprintable>"
    redacted = _BEARER_RE.sub("bearer=***", message or "")
    redacted = _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}=***", redacted)
    redacted = _CONTROL_RE.sub(" ", redacted).strip()
    if len(redacted) > limit:
        redacted = redacted[:limit] + "..."
    return f"{type(exc).__name__}: {redacted}"


def _content_codes(request: DraftRequest) -> list[tuple[RejectionCode, str]]:
    codes: list[tuple[RejectionCode, str]] = []
    if request.proposed_commercial_model == "strategic_revenue_sharing":
        codes.append(
            (RejectionCode.REVENUE_SHARING_PROHIBITED, "proposed_commercial_model is strategic_revenue_sharing")
        )
    elif request.proposed_commercial_model != "project_based_wholesale":
        codes.append(
            (
                RejectionCode.COMMERCIAL_MODEL_PREMATURE,
                f"{request.proposed_commercial_model} is a later-stage offer (AGENTS.md §2); "
                "cold outreach opens with project-based wholesale development",
            )
        )

    if not (request.body or "").strip():
        codes.append((RejectionCode.DRAFT_CONTENT_INVALID, "body is empty"))
    if request.channel != "contact_form" and not (request.subject or "").strip():
        codes.append((RejectionCode.DRAFT_CONTENT_INVALID, "subject is empty for a direct channel"))

    # Structural text hygiene, checked on the raw text: normalization would
    # happily fold a CRLF away, and the stored row must not carry one.
    subject = request.subject or ""
    if _LINE_BREAK_RE.search(subject) or _CONTROL_RE.search(subject):
        codes.append(
            (
                RejectionCode.DRAFT_CONTENT_INVALID,
                "subject contains a line break or control character (header injection)",
            )
        )
    if _CONTROL_RE.search(request.body or ""):
        codes.append(
            (RejectionCode.DRAFT_CONTENT_INVALID, "body contains a control character")
        )

    text = f"{subject}\n{request.body}"
    for label in _matches(_REVENUE_SHARING_PATTERNS, text):
        codes.append((RejectionCode.REVENUE_SHARING_PROHIBITED, label))
    for label in _matches(_UNSUPPORTED_DELIVERY_PATTERNS, text):
        codes.append((RejectionCode.UNSUPPORTED_DELIVERY_CLAIM, label))
    return codes


# ---------------------------------------------------------------------------
# Qualification evaluation
# ---------------------------------------------------------------------------


def _qualification_codes(record: QualificationRecord, company_id: str) -> list[tuple[RejectionCode, str]]:
    codes: list[tuple[RejectionCode, str]] = []

    # The reader is asked for one company's scorecard; a record for another
    # company means the answer does not belong to this request at all (a
    # mis-keyed query, a shared cache, or a caller passing a hand-built record).
    if str(record.company_id or "") != str(company_id or ""):
        codes.append(
            (
                RejectionCode.QUALIFICATION_COMPANY_MISMATCH,
                f"qualification belongs to company {record.company_id!r}, not {company_id!r}",
            )
        )

    if not record.review_status_known:
        codes.append(
            (RejectionCode.QUALIFICATION_NOT_APPROVED, f"unrecognised review_status {record.review_status!r}")
        )
    elif record.review_status in ("draft", "pending_human_review"):
        codes.append(
            (
                RejectionCode.QUALIFICATION_PENDING_REVIEW,
                f"review_status is {record.review_status!r}; only a human operator approves (gate G2)",
            )
        )
    elif record.review_status == "rejected":
        codes.append((RejectionCode.QUALIFICATION_NOT_APPROVED, "review_status is 'rejected'"))

    # "Approved" without a reviewer is not approval. The table constraint
    # chk_lead_qual_review_human_gate enforces this in the database; the gate
    # cannot assume the row it was handed came from that constraint.
    if record.review_status == "approved" and not record.has_human_reviewer:
        codes.append(
            (
                RejectionCode.HUMAN_REVIEWER_MISSING,
                "review_status is 'approved' but reviewed_by_human_id is empty",
            )
        )
    # needs_human_review has no column: it is the in-memory scorecard's own
    # verdict on itself. If it says a human must still look, a reviewer id on
    # the row does not overrule that -- the flag is never downgraded here.
    if record.needs_human_review:
        codes.append(
            (
                RejectionCode.QUALIFICATION_PENDING_REVIEW,
                "scorecard still reports needs_human_review; it is not settled",
            )
        )
    if record.review_status == "approved" and record.has_human_reviewer and not record.reviewed_at:
        codes.append(
            (
                RejectionCode.PROVENANCE_INCOMPLETE,
                "approved scorecard has a reviewer but no reviewed_at (chk_lead_qual_review_human_gate)",
            )
        )

    if record.qualification_tier == "disqualified" or record.disqualification_reasons:
        codes.append(
            (
                RejectionCode.QUALIFICATION_DISQUALIFIED,
                f"tier {record.qualification_tier!r} with reasons {list(record.disqualification_reasons)}",
            )
        )
    elif not record.tier_known:
        codes.append(
            (RejectionCode.QUALIFICATION_UNMEASURED, f"unrecognised tier {record.qualification_tier!r}")
        )
    else:
        # chk_lead_qual_score_sum / fn_enforce_lead_qualification_rules force
        # tier and score to agree; a row (or a hand-built record) that claims a
        # better tier than its score earns is either fabricated or corrupted.
        expected_tier = tier_for_score(float(record.overall_score), bool(record.disqualification_reasons))
        if expected_tier != record.qualification_tier:
            codes.append(
                (
                    RejectionCode.QUALIFICATION_TIER_INCONSISTENT,
                    f"tier {record.qualification_tier!r} does not match score "
                    f"{record.overall_score} (expected {expected_tier!r})",
                )
            )

    if record.insufficient_evidence:
        codes.append(
            (
                RejectionCode.QUALIFICATION_UNMEASURED,
                "scorecard is flagged insufficient_evidence: the company was not measured (ADR-0006)",
            )
        )
    if record.evidence_coverage <= 0:
        codes.append(
            (RejectionCode.QUALIFICATION_UNMEASURED, "no dimension carries documented evidence")
        )

    if not (0.0 <= float(record.overall_score) <= 100.0):
        codes.append(
            (RejectionCode.PROVENANCE_INCOMPLETE, f"overall_score {record.overall_score} is out of range")
        )
    if not record.evidence_refs:
        codes.append(
            (
                RejectionCode.PROVENANCE_INCOMPLETE,
                "no finding ids cited in dimension_evidence; the draft would have no provenance",
            )
        )
    if not str(record.qualification_id or "").strip():
        codes.append((RejectionCode.PROVENANCE_INCOMPLETE, "qualification_id is missing"))
    return codes


# ---------------------------------------------------------------------------
# Suppression evaluation
# ---------------------------------------------------------------------------


def _suppression_codes(
    report: SuppressionReport | None, failure: BaseException | None
) -> list[tuple[RejectionCode, str]]:
    # Why ``None`` is a rejection: an implementation that returns nothing has
    # not told us the prospect is clean, and "did not answer" must never be
    # treated as "no match".
    if failure is not None:
        return [
            (
                RejectionCode.SUPPRESSION_LOOKUP_FAILED,
                f"suppression lookup raised {_describe_exception(failure)}",
            )
        ]
    if report is None:
        return [(RejectionCode.SUPPRESSION_LOOKUP_FAILED, "suppression reader returned no report")]
    if not isinstance(report, SuppressionReport):
        return [
            (
                RejectionCode.SUPPRESSION_LOOKUP_FAILED,
                f"suppression reader returned {type(report).__name__}, not a SuppressionReport",
            )
        ]

    if report.state == "suppressed":
        codes: list[tuple[RejectionCode, str]] = []
        targets = report.targets()
        if "contact_email" in targets or "contact_linkedin" in targets:
            codes.append(
                (RejectionCode.SUPPRESSED_CONTACT, f"contact on the suppression ledger: {report.detail}")
            )
        if "domain" in targets:
            codes.append((RejectionCode.SUPPRESSED_DOMAIN, f"domain suppressed: {report.detail}"))
        if "company" in targets:
            codes.append((RejectionCode.SUPPRESSED_COMPANY, f"company suppressed: {report.detail}"))
        if not codes:
            codes.append((RejectionCode.SUPPRESSED_COMPANY, f"suppressed: {report.detail}"))
        return codes

    if report.state == "ambiguous":
        return [
            (
                RejectionCode.SUPPRESSION_AMBIGUOUS,
                f"suppression state could not be determined: {report.detail or 'no detail'}",
            )
        ]

    if report.state == "clear" and report.findings:
        # A "clear" verdict that still carries findings is contradictory; the
        # registry also refuses whitespace-mangled identifiers, so we cannot
        # assume the lookup was meaningful.
        return [
            (
                RejectionCode.SUPPRESSION_AMBIGUOUS,
                f"reader reported clear but supplied findings: {[f.identifier for f in report.findings]}",
            )
        ]

    if report.state == "clear" and not _names_a_source(report.sources_checked):
        # An empty answer is not a clean answer. Without a statement of what was
        # consulted, a reader that queried nothing (wrong table, RLS-filtered,
        # short-circuited) is indistinguishable from one that found nothing.
        # A tuple of blank strings is not a statement: it satisfies "named
        # something" without naming anything.
        return [
            (
                RejectionCode.SUPPRESSION_AMBIGUOUS,
                "reader reported clear without naming any source it checked",
            )
        ]
    return []


def _names_a_source(sources_checked: Sequence[str]) -> bool:
    """True when at least one entry names something (not ``""``/``"   "``/``"•"``)."""
    return any(any(ch.isalnum() for ch in str(source)) for source in sources_checked)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_draft_eligibility(
    request: DraftRequest,
    qualification_reader: QualificationReader,
    suppression_reader: SuppressionReader,
) -> EligibilityDecision:
    """Decide whether a draft may exist. Pure: reads through ports, writes nothing.

    Fails closed on every uncertainty, including reader exceptions this module
    does not recognise (an unwrapped PostgREST error is a rejection, not a
    crash and not a pass).
    """
    rejections: list[tuple[RejectionCode, str]] = list(_content_codes(request))
    if rejections:
        return _decision(rejections)

    try:
        record = qualification_reader.get_qualification(request.company_id)
    except Exception as exc:  # noqa: BLE001 - any read failure must not read as "qualified"
        return _decision(
            [
                (
                    RejectionCode.QUALIFICATION_LOOKUP_FAILED,
                    f"qualification read raised {_describe_exception(exc)}",
                )
            ]
        )

    if record is None:
        return _decision(
            [
                (
                    RejectionCode.QUALIFICATION_MISSING,
                    f"no persisted qualification for company {request.company_id}; "
                    "the lead must pass gate G2 first",
                )
            ]
        )

    # The port promises a QualificationRecord. Anything else (a raw PostgREST
    # row, a dict, a mock that answers with a different shape) is an unreadable
    # answer, not a qualified lead: refuse it instead of crashing on it.
    if not isinstance(record, QualificationRecord):
        return _decision(
            [
                (
                    RejectionCode.QUALIFICATION_LOOKUP_FAILED,
                    f"qualification reader returned {type(record).__name__}, not a QualificationRecord",
                )
            ]
        )

    qualification_rejections = _qualification_codes(record, request.company_id)
    if qualification_rejections:
        return _decision(qualification_rejections, qualification=record)

    report: SuppressionReport | None = None
    failure: BaseException | None = None
    try:
        report = suppression_reader.check(request.company_id, request.contact_id)
    except Exception as exc:  # noqa: BLE001 - fail closed, never "clean"
        failure = exc

    suppression_rejections = _suppression_codes(report, failure)
    if suppression_rejections:
        return _decision(suppression_rejections, qualification=record)

    # Unreachable: _suppression_codes rejects a missing report. Kept as a real
    # branch (not an assert) so the fail-closed behaviour survives `python -O`.
    if report is None:
        return _decision(
            [(RejectionCode.SUPPRESSION_LOOKUP_FAILED, "suppression reader returned no report")],
            qualification=record,
        )

    return EligibilityDecision(
        codes=(),
        detail=(
            "qualified, human-approved, evidence-backed, not suppressed "
            f"(reader checked: {', '.join(report.sources_checked)})"
        ),
        qualification=record,
        evidence_refs=record.evidence_refs,
    )


def _decision(
    rejections: Sequence[tuple[RejectionCode, str]],
    *,
    qualification: QualificationRecord | None = None,
) -> EligibilityDecision:
    """Deduplicate codes in discovery order and keep every reason in ``detail``."""
    codes: list[RejectionCode] = []
    for code, _ in rejections:
        if code not in codes:
            codes.append(code)
    detail = "; ".join(dict.fromkeys(text for _, text in rejections))
    return EligibilityDecision(codes=tuple(codes), detail=detail, qualification=qualification)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def row_idempotency_key(row: Mapping[str, Any]) -> str:
    """Identity of one draft *slot*, computed from columns that exist.

    ``outreach_messages`` has no idempotency-key column and no ``outreach_event``
    column (crm/schema.sql L596-646), so the only key a writer can recompute from
    a persisted row is a hash of the columns the row actually carries. That is
    what this function is: the single place the platform defines the slot, so a
    writer never invents its own.

    The slot is ``(company_id, campaign_id, channel, sequence_step)``. The
    outreach event is *not* part of it: it is not persisted, so including it
    would produce a key no reader could ever recompute — dedupe that only works
    while the object is still in memory. Two distinct events aimed at the same
    step of the same campaign are therefore the same slot, and the second one is
    refused rather than duplicated. ``sequence_step`` *is* included, so a
    follow-up (step 2) is a different draft from the intro (step 1).
    """
    campaign = row.get("campaign_id")
    return content_key(
        {
            "company_id": str(row.get("company_id") or ""),
            "campaign_id": str(campaign) if campaign else None,
            "channel": str(row.get("channel") or ""),
            "sequence_step": int(row.get("sequence_step") or 0),
        }
    )


def draft_idempotency_key(request: DraftRequest) -> str:
    """Content-addressed key for one intended outreach slot (ADR-0004).

    Identical ``(company_id, campaign_id, channel, sequence_step)`` always hash
    to the same key, so a retried drafting run finds the row it already wrote.
    Body text is deliberately *not* part of the key: a reworded body for the
    same slot is a new draft only after a human rejects the old one, and that
    path goes through G3, not through this function.
    """
    return row_idempotency_key(
        {
            "company_id": request.company_id,
            "campaign_id": request.campaign_id,
            "channel": request.channel,
            "sequence_step": request.sequence_step,
        }
    )


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def build_provenance(record: QualificationRecord, rubric_version: str) -> dict[str, Any]:
    """Machine-readable provenance carried by every draft."""
    return {
        "company_id": record.company_id,
        "qualification_id": record.qualification_id,
        "overall_score": round(float(record.overall_score), 2),
        "qualification_tier": record.qualification_tier,
        "reviewed_by_human_id": record.reviewed_by_human_id,
        "reviewed_at": record.reviewed_at,
        "rubric_version": rubric_version,
        "evidence_coverage": record.evidence_coverage,
        "evidence_refs": list(record.evidence_refs),
        "source": "lead_qualifications",
    }


def format_provenance(provenance: Mapping[str, Any]) -> str:
    """Human-readable provenance for ``personalization_rationale`` (NOT NULL).

    The schema has no provenance column (see the workflow report), so this text
    *is* the audit trail next to the message. It states only what the scorecard
    said; it invents no prospect fact. The outreach event and sequence step are
    appended because neither has a column of its own, and the idempotency slot
    is derived from the step: without them in the text there would be no record
    of which event a row was drafted for.
    """
    refs = ", ".join(provenance.get("evidence_refs") or ()) or "none"
    text = (
        "Qualified lead provenance — "
        f"qualification_id={provenance.get('qualification_id')}; "
        f"company_id={provenance.get('company_id')}; "
        f"score={provenance.get('overall_score')}/100; "
        f"tier={provenance.get('qualification_tier')}; "
        f"reviewed_by_human_id={provenance.get('reviewed_by_human_id')}; "
        f"reviewed_at={provenance.get('reviewed_at')}; "
        f"rubric_version={provenance.get('rubric_version')}; "
        f"evidence_coverage={provenance.get('evidence_coverage')}; "
        f"evidence_refs=[{refs}]"
    )
    if provenance.get("outreach_event"):
        text += f"; outreach_event={provenance.get('outreach_event')}"
    if provenance.get("sequence_step") is not None:
        text += f"; sequence_step={provenance.get('sequence_step')}"
    return text


def build_draft_payload(
    request: DraftRequest,
    decision: EligibilityDecision,
    *,
    agent_identity: str,
    rubric_version: str = RUBRIC_VERSION,
) -> DraftPayload:
    """Assemble the draft row. Refuses to build from a non-eligible decision.

    The decision is *re-derived* from the request and the record it carries
    rather than trusted: ``EligibilityDecision`` is a plain dataclass any caller
    can construct with no codes at all, so "eligible" alone would let a forged
    decision mint a payload carrying prohibited text or an unapproved scorecard.
    Both guards are pure, so this costs nothing and closes the gap between
    ``evaluate_draft_eligibility`` and the only function that can write.
    """
    if not decision.eligible or decision.qualification is None:
        raise DraftRejected(decision.codes or (RejectionCode.PROVENANCE_INCOMPLETE,), decision.detail)

    record = decision.qualification
    content = _content_codes(request)
    if content:
        raise DraftRejected(tuple(dict.fromkeys(code for code, _ in content)), "; ".join(text for _, text in content))

    requalified = _qualification_codes(record, request.company_id)
    if requalified:
        raise DraftRejected(
            tuple(dict.fromkeys(code for code, _ in requalified)),
            "; ".join(text for _, text in requalified),
        )

    if not str(agent_identity or "").strip():
        raise DraftPersistenceError("a draft must carry the verified drafting agent identity")

    # personalization_evidence_ids is UUID[] (L605) and is the only structured
    # provenance a row can carry. A draft whose array would be empty has
    # provenance in prose only, which nothing can join on: refuse it rather
    # than write a row that cannot be traced back to research_findings.
    refs = tuple(str(r) for r in record.evidence_refs)
    uuid_refs = tuple(ref for ref in refs if _UUID_RE.match(ref))
    if not uuid_refs:
        raise DraftRejected(
            (RejectionCode.PROVENANCE_INCOMPLETE,),
            "no UUID-shaped research_findings id cited in dimension_evidence; "
            "personalization_evidence_ids would be empty and the draft would have no "
            "machine-checkable provenance",
        )

    provenance = {
        **build_provenance(record, rubric_version),
        "outreach_event": request.outreach_event,
        "sequence_step": int(request.sequence_step),
    }
    return DraftPayload(
        company_id=request.company_id,
        contact_id=request.contact_id,
        campaign_id=request.campaign_id,
        channel=request.channel,
        subject=request.subject,
        body=request.body,
        personalization_evidence_ids=uuid_refs,
        personalization_rationale=format_provenance(provenance),
        sequence_step=int(request.sequence_step),
        proposed_commercial_model=request.proposed_commercial_model,
        drafted_by_agent_identity=agent_identity,
        outreach_event=request.outreach_event,
        idempotency_key=draft_idempotency_key(request),
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def create_draft(
    request: DraftRequest,
    qualification_reader: QualificationReader,
    suppression_reader: SuppressionReader,
    draft_writer: DraftWriter,
    *,
    rubric_version: str = RUBRIC_VERSION,
) -> DraftOutcome:
    """Create at most one draft for one intended outreach event.

    The only write is ``outreach_messages`` with ``approval_status='draft'`` and
    ``sending_status='draft'``. Approval is gate G3 and dispatch is gate G4
    (``orchestration/workflow.py``); this function has no path to either.
    """
    decision = evaluate_draft_eligibility(request, qualification_reader, suppression_reader)
    if not decision.eligible:
        return DraftOutcome(
            status="rejected",
            decision=decision,
            idempotency_key=draft_idempotency_key(request),
            detail=decision.detail,
        )

    key = draft_idempotency_key(request)
    existing = draft_writer.find_existing(key)
    if existing is not None:
        detail, content_matches = _verify_existing(existing, request, key)
        return DraftOutcome(
            status="already_exists",
            decision=decision,
            idempotency_key=key,
            draft_id=str(existing.get("id") or "") or None,
            detail=detail,
            content_matches=content_matches,
        )

    payload = build_draft_payload(
        request, decision, agent_identity=draft_writer.agent_identity, rubric_version=rubric_version
    )
    row = draft_writer.write(payload)
    _verify_persisted(row, payload)
    return DraftOutcome(
        status="created",
        decision=decision,
        idempotency_key=key,
        draft_id=str(row.get("id")),
        payload=payload,
    )


def _verify_existing(existing: Any, request: DraftRequest, key: str) -> tuple[str, bool]:
    """Check the row a writer claims already exists for this slot.

    ``find_existing`` is the writer's answer to "is there already a draft here?",
    and a wrong answer costs a prospect their message: a row belonging to
    another company would make the gate report ``already_exists`` and silently
    skip a draft that was never written. So the row's own identity columns must
    reproduce the requested key; a writer that cannot produce them cannot answer
    the question.

    The slot is deliberately recipient- and text-independent (the body is not part
    of the key), so a row that occupies the slot with *different* text or another
    recipient is a **conflict**, not a duplicate of this request. It is returned
    with ``content_matches=False`` and a detail that names what differs: the
    caller asked for a draft that was not written, and treating that as "already
    done" would silently drop the message it just composed.
    """
    if not isinstance(existing, Mapping):
        raise DraftPersistenceError(
            f"draft writer returned {type(existing).__name__} from find_existing, expected a mapping"
        )
    if str(existing.get("company_id") or "") != str(request.company_id):
        raise DraftPersistenceError(
            "existing draft belongs to a different company than the request; "
            "refusing to treat it as this company's draft"
        )
    try:
        derived = row_idempotency_key(existing)
    except (TypeError, ValueError) as exc:
        raise DraftPersistenceError(
            "draft writer returned an existing row without the identity columns "
            f"(company_id, campaign_id, channel, sequence_step): {exc}"
        ) from exc
    if derived != key:
        raise DraftPersistenceError(
            "existing row's own columns hash to a different slot than the requested key; "
            "the writer's lookup is not the key the gate computed"
        )
    differences = _existing_row_differences(existing, request)
    if differences:
        return (
            "a draft already exists for this company/campaign/channel/step but is not the draft "
            f"this request describes ({', '.join(differences)}); nothing was written -- a human "
            "must resolve the conflict at G3",
            False,
        )
    return "a draft for this company/campaign/channel/step already exists; nothing was written", True


def _existing_row_differences(existing: Mapping[str, Any], request: DraftRequest) -> list[str]:
    """Which part of the requested draft the stored row does not match."""
    differences: list[str] = []
    if str(existing.get("contact_id") or "") != str(request.contact_id):
        differences.append("a different contact")
    if (existing.get("subject") or "") != (request.subject or ""):
        differences.append("a different subject")
    if (existing.get("body") or "") != (request.body or ""):
        differences.append("a different body")
    return differences


def _verify_persisted(row: Any, payload: DraftPayload) -> None:
    """Read back what the writer returned; a non-draft row is an integrity failure.

    This is the application-side half of
    ``chk_outreach_mandatory_human_gate``: if a writer ever returns a row that
    is queued, sent or approved, the workflow stops here instead of treating a
    dispatched message as a finished draft. The content checks matter for the
    same reason in the other direction: the lexical guards ran on the payload,
    so a row whose subject, body or evidence array differs from it was changed
    after the guards and must not be reported as a clean draft.
    """
    if not isinstance(row, Mapping):
        raise DraftPersistenceError(f"draft writer returned {type(row).__name__}, expected a mapping")
    if not str(row.get("id") or "").strip():
        raise DraftPersistenceError("draft writer returned a row without an id")

    if row.get("approval_status") != "draft":
        raise DraftPersistenceError(f"persisted row is not a draft: approval_status={row.get('approval_status')!r}")
    if row.get("sending_status") != "draft":
        raise DraftPersistenceError(f"persisted row is not a draft: sending_status={row.get('sending_status')!r}")
    if row.get("approved_by_human_id") or row.get("approved_at") or row.get("sent_at"):
        raise DraftPersistenceError("persisted row carries approval or delivery metadata")
    if row.get("company_id") != payload.company_id:
        raise DraftPersistenceError("persisted row belongs to a different company than the request")
    if (row.get("campaign_id") or None) != (payload.campaign_id or None):
        raise DraftPersistenceError(
            "persisted row carries a different campaign_id than the request; the draft would be "
            "filed under another campaign's slot"
        )
    if row.get("contact_id") != payload.contact_id:
        raise DraftPersistenceError("persisted row is addressed to a different contact than the request")
    if row.get("channel") != payload.channel:
        raise DraftPersistenceError("persisted row carries a different channel than the request")
    try:
        persisted_step = int(row.get("sequence_step") or 0)
    except (TypeError, ValueError) as exc:
        raise DraftPersistenceError(
            f"persisted row carries a non-integer sequence_step: {row.get('sequence_step')!r}"
        ) from exc
    if persisted_step != payload.sequence_step:
        raise DraftPersistenceError("persisted row carries a different sequence_step than the request")
    if (row.get("body") or "") != payload.body:
        raise DraftPersistenceError(
            "persisted body differs from the text that passed the content guards"
        )
    if (row.get("subject") or "") != (payload.subject or ""):
        raise DraftPersistenceError(
            "persisted subject differs from the text that passed the content guards"
        )
    if list(row.get("personalization_evidence_ids") or []) != list(payload.personalization_evidence_ids):
        raise DraftPersistenceError(
            "persisted personalization_evidence_ids differ from the cited evidence"
        )
    if (row.get("personalization_rationale") or "") != payload.personalization_rationale:
        raise DraftPersistenceError(
            "persisted personalization_rationale differs from the provenance the gate wrote"
        )
    # The database refuses strategic_revenue_sharing (chk_outreach_no_revenue_sharing)
    # but not monthly_reserved_capacity, which is only deferred by this package's
    # policy. The row must carry the model the guards approved.
    if row.get("proposed_commercial_model") != payload.proposed_commercial_model:
        raise DraftPersistenceError(
            "persisted row carries a different proposed_commercial_model than the one that "
            "passed the commercial-model guard"
        )
    if row.get("drafted_by_agent_identity") != payload.drafted_by_agent_identity:
        raise DraftPersistenceError(
            "persisted row was drafted by a different identity than the verified writer identity"
        )
