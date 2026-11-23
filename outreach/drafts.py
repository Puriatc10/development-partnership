"""Deterministic draft text: a template, a citation, and nothing else.

Why not an LLM
--------------
The point of this stage is to produce a draft that a human can approve without
having to fact-check it. An LLM adds three failure modes to that: it can invent a
prospect fact, it can promise something the business cannot deliver yet
(AGENTS.md §1.2, §11), and it produces different text for the same input, which
makes "what did we send and why" unanswerable. A template has none of them: the
same company, scorecard and step always produce the same bytes, and every claim
in the body is either a citation or the offer itself.

What the body is allowed to contain
-----------------------------------
* one reason for writing, quoted from the scorecard's own
  ``dimension_evidence``: a verbatim ``excerpt`` plus the ``research_findings.id``
  it came from. Nothing about the prospect is paraphrased, so nothing can drift;
* the offer (fixed-scope backend work, white-label). It states the *starting
  model*, never a delivery timeline, a headcount, a price, a guarantee or a
  reservation of capacity;
* a salutation and a signature, both supplied by the caller from real data.

What it must never contain is enforced twice: the templates are written to avoid
the gate's lexemes, and every rendered draft is run through the gate's own content
guards (:func:`outreach.gate._content_codes`) plus a local price/deadline guard
before it is returned. If a citation's wording trips a guard (an agency whose own
site says "our team of twelve"), the generator moves to the next dimension rather
than quoting text the gate would refuse. If no citation survives, it raises — it
does not sanitise a quote into something the prospect did not write.

The single private import from ``outreach.gate`` is intentional: the alternative
is a second copy of the prohibited-claim lexemes, and two copies drift. The gate
stays the one authority on what may be said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .gate import (
    _UUID_RE,
    _content_codes,  # noqa: PLC2701 - see module docstring: one authority, not two
    DraftRequest,
    DraftRequestError,
    build_provenance,
    format_provenance,
    normalize_for_matching,
    row_idempotency_key,
)
from .ports import (
    OUTREACH_CHANNELS,
    DraftPayload,
    OutreachError,
    QualificationRecord,
)

#: Hard caps. A cold note that needs more than this is not a note.
MAX_SUBJECT_CHARS = 100
MAX_BODY_CHARS = 1600
#: How much of the cited evidence is quoted. Long enough to be specific, short
#: enough that the citation is still a quotation rather than a copy of the page.
MAX_QUOTE_CHARS = 220

#: ``dimension_evidence`` keys, in the fixed order the port defines. Citation
#: selection walks this order, so the choice is deterministic, not \"whichever
#: key the JSONB happened to serialise first\".
_DIMENSION_KEYS: tuple[str, ...] = (
    "customer_flow",
    "development_demand",
    "repeatability_orders",
    "technical_fit",
    "business_size",
    "decision_maker_accessibility",
    "commercial_fit",
)

#: Attributed to the scorecard's own categories only in the generator's internals;
#: never rendered into the body (they are our vocabulary, not the prospect's).
_DIMENSION_LABELS: Mapping[str, str] = {
    "customer_flow": "client flow",
    "development_demand": "development demand",
    "repeatability_orders": "repeat business",
    "technical_fit": "technical fit",
    "business_size": "business size",
    "decision_maker_accessibility": "decision-maker access",
    "commercial_fit": "commercial fit",
}

#: The gate refuses these in prose; the generator refuses them in its own output
#: before the gate ever sees it. The gate's list is the authority for claims about
#: delivery and revenue sharing; this one adds the two things the gate leaves to
#: human review (price and timeline) and the currency glyphs that carry them.
_PRICE_AND_TIMELINE: tuple[tuple[str, str], ...] = (
    (r"[$€£¥]", "quotes a price"),
    (r"\b(?:price|pricing|priced|rate|rates|hourly|daily\s+rate|cost|costs|fee|fees)\b", "discusses price"),
    (r"\b(?:budget|quote|quotation|invoice|discount|retainer|deposit)\b", "discusses commercial terms"),
    (r"\b(?:per\s+(?:hour|day|week|month)|monthly\s+retainer|day\s+rate)\b", "quotes a rate basis"),
    (r"\bdeadline\b", "mentions a deadline"),
    (r"\bby\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|next\s+week|end\s+of\s+week)\b", "promises a date"),
    (r"\bwithin\s+\d+\s*(?:business\s+)?(?:hours?|days?|weeks?)\b", "promises a timeframe"),
    (r"\b(?:asap|as\s+soon\s+as\s+possible|immediately|right\s+away)\b", "promises immediacy"),
    (r"\b\d+\s*(?:-|to)\s*\d+\s*(?:business\s+)?days?\b", "promises a timeframe"),
)


class DraftGenerationError(OutreachError):
    """A draft could not be rendered from the inputs it was given.

    Raised for structural problems (no approved scorecard, no citable finding, no
    contact) and for the self-check: text this generator produced that the gate's
    guards would refuse is never returned.
    """


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepTemplate:
    """One sequence step. The step number selects the template; nothing else does."""

    step: int
    outreach_event: str
    subject: str
    body: str


#: Steps 1-3 are distinct; anything later reuses the closing note, which is what a
#: sequence beyond three touches converges to anyway.
_TEMPLATES: tuple[StepTemplate, ...] = (
    StepTemplate(
        step=1,
        outreach_event="initial_partnership_intro",
        subject="Backend and API work behind {company}'s client projects",
        body=(
            "Hi {first_name},\n"
            "\n"
            'I am writing to {company} about one specific thing I found on your site: '
            '"{quote}" [{finding_id}].\n'
            "\n"
            "That reference is why I am getting in touch. I want to work behind studios like "
            "yours on the layer beneath the design: APIs, authentication, database-backed "
            "flows and third-party integrations. The starting model is fixed-scope project "
            "work with a written scope agreed before a project begins, and it stays "
            "white-label: your relationship with the client, my work behind it.\n"
            "\n"
            "If a current project has that layer unowned, would a short conversation be "
            "useful?\n"
            "\n"
            "Thanks,\n"
            "{signer}"
        ),
    ),
    StepTemplate(
        step=2,
        outreach_event="follow_up_after_silence",
        subject="Following up: the backend layer behind {company}'s builds",
        body=(
            "Hi {first_name},\n"
            "\n"
            "Following up on my earlier note to {company}. The reference still holds: "
            '"{quote}" [{finding_id}].\n'
            "\n"
            "If that layer is already covered on your side, that is useful to know and I will "
            "not write again. If it is not, I can put a fixed-scope outline for one project on "
            "paper so you can judge the fit before any commitment.\n"
            "\n"
            "Thanks,\n"
            "{signer}"
        ),
    ),
    StepTemplate(
        step=3,
        outreach_event="follow_up_final",
        subject="Closing the loop with {company}",
        body=(
            "Hi {first_name},\n"
            "\n"
            "One last short note, then I will leave it with you. The reference I wrote about: "
            '"{quote}" [{finding_id}].\n'
            "\n"
            "The offer is the same: fixed-scope backend work behind your studio's client "
            "projects, white-label, scoped in writing before a project starts. If the timing "
            "is wrong, a one-line reply is enough and I will close the thread.\n"
            "\n"
            "Thanks,\n"
            "{signer}"
        ),
    ),
)


def template_for(step: int) -> StepTemplate:
    """The template for this step; steps past three use the closing note."""
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        raise DraftGenerationError(f"sequence step must be an integer >= 1, got {step!r}")
    index = min(step, len(_TEMPLATES)) - 1
    return _TEMPLATES[index]


# ---------------------------------------------------------------------------
# Citation selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """One quotable piece of scorecard evidence."""

    dimension: str
    finding_id: str
    quote: str

    @property
    def label(self) -> str:
        return _DIMENSION_LABELS.get(self.dimension, self.dimension)


_CITATION_PREFIX = re.compile(r"^\s*(?:\[[0-9a-fA-F-]{36}\]\s*)+")
_WHITESPACE = re.compile(r"\s+")


def _clean_quote(text: Any) -> str:
    """Make a verbatim excerpt safe to place inside a plain-text email body.

    Whitespace is collapsed (the excerpt comes from HTML), control characters are
    dropped, double quotes become single quotes so the surrounding quotation marks
    stay unambiguous, and the result is cut at a word boundary. Nothing is added:
    an excerpt that is empty after cleaning is refused, not filled in.
    """
    if not isinstance(text, str):
        return ""
    cleaned = "".join(ch for ch in text if ch >= " " or ch == "\t")
    cleaned = _CITATION_PREFIX.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().replace('"', "'")
    if len(cleaned) > MAX_QUOTE_CHARS:
        cut = cleaned.rfind(" ", 0, MAX_QUOTE_CHARS)
        cleaned = cleaned[: cut if cut > 40 else MAX_QUOTE_CHARS].rstrip(" ,.;:-")
        cleaned += "..."
    return cleaned


def citations(qualification: QualificationRecord) -> tuple[Citation, ...]:
    """Every dimension that carries both a UUID finding id and quotable evidence.

    ``dimension_evidence`` is not validated by the schema beyond being JSONB, so a
    hand-written or half-migrated block is treated as "no citation here" rather
    than crashing: this function's failure mode is an empty tuple, which
    :func:`render_draft` turns into a typed error.
    """
    found: list[Citation] = []
    seen: set[str] = set()
    for key in _DIMENSION_KEYS:
        entry = (qualification.dimension_evidence or {}).get(key)
        if not isinstance(entry, Mapping):
            continue
        ids = [
            str(ref).strip()
            for ref in (entry.get("finding_ids") or ())
            if _UUID_RE.match(str(ref).strip())
        ]
        if not ids:
            continue
        quote = _clean_quote(entry.get("excerpt")) or _clean_quote(entry.get("evidence"))
        if not quote:
            continue
        finding_id = ids[0]
        if finding_id in seen:
            continue
        seen.add(finding_id)
        found.append(Citation(dimension=key, finding_id=finding_id, quote=quote))
    return tuple(found)


def _shorten(text: str, limit: int) -> str:
    text = _WHITESPACE.sub(" ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return (text[: cut if cut > 10 else limit]).rstrip(" ,.;:-") + "..."


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _require_company(company: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(company, Mapping):
        raise DraftGenerationError(
            f"company must be a mapping with 'id' and 'name', got {type(company).__name__}"
        )
    company_id = str(company.get("id") or "").strip()
    name = _WHITESPACE.sub(" ", str(company.get("name") or "")).strip()
    if not company_id:
        raise DraftGenerationError("company['id'] is required (companies.id)")
    if not name:
        raise DraftGenerationError("company['name'] is required; a body cannot address an unnamed company")
    return company_id, name


def _require_contact(contact: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(contact, Mapping):
        raise DraftGenerationError(
            "contact must be a mapping with 'id' (outreach_messages.contact_id is NOT NULL), "
            f"got {type(contact).__name__}"
        )
    contact_id = str(contact.get("id") or "").strip()
    if not contact_id:
        raise DraftGenerationError("contact['id'] is required (outreach_messages.contact_id)")
    first_name = _WHITESPACE.sub(" ", str(contact.get("first_name") or "")).strip()
    return contact_id, (first_name.split(" ")[0] if first_name else "there")


def _require_signer(signer: str) -> str:
    text = str(signer or "").strip()
    if any(ch in text for ch in "\r\n\t"):
        raise DraftGenerationError("signer must be a single line; a newline here would split the signature")
    return text


def render_draft(
    *,
    company: Mapping[str, Any],
    qualification: QualificationRecord,
    contact: Mapping[str, Any],
    agent_identity: str,
    campaign_id: str | None = None,
    step: int = 1,
    channel: str = "email",
    signer: str = "",
) -> DraftPayload:
    """Render one deterministic draft for one sequence step.

    Returns the exact :class:`outreach.ports.DraftPayload` the gate would build for
    the same request — same body, same evidence ids, same provenance string — so
    the caller can hand the text to ``DraftRequest`` and ``create_draft`` without
    the payload and the row diverging.

    Raises :class:`DraftGenerationError` when there is no approved, measured
    scorecard to cite, no contact to address, or when no candidate citation
    survives the content guards.
    """
    company_id, company_name = _require_company(company)
    contact_id, first_name = _require_contact(contact)
    identity = str(agent_identity or "").strip()
    if not identity:
        raise DraftGenerationError("agent_identity is required; the row must name who drafted it")
    if channel not in OUTREACH_CHANNELS:
        raise DraftGenerationError(f"unknown channel {channel!r}; allowed: {list(OUTREACH_CHANNELS)}")
    if campaign_id is not None and not str(campaign_id).strip():
        raise DraftGenerationError("campaign_id must be a UUID string or None, never blank")
    campaign = str(campaign_id).strip() if campaign_id is not None else None

    if not isinstance(qualification, QualificationRecord):
        raise DraftGenerationError(
            f"qualification must be a QualificationRecord, got {type(qualification).__name__}"
        )
    if qualification.review_status != "approved":
        raise DraftGenerationError(
            f"refusing to cite a scorecard with review_status {qualification.review_status!r}; "
            "only an approved scorecard may justify outreach (gate G2)"
        )
    if str(qualification.company_id) != company_id:
        raise DraftGenerationError(
            f"scorecard belongs to company {qualification.company_id!r}, not {company_id!r}"
        )

    template = template_for(step)
    signer_line = _require_signer(signer)
    candidates = citations(qualification)
    if not candidates:
        raise DraftGenerationError(
            "the scorecard cites no UUID-shaped finding id with quotable evidence; a draft "
            "without a specific, checkable citation is not allowed to exist"
        )

    refs = tuple(str(ref) for ref in qualification.evidence_refs if _UUID_RE.match(str(ref)))
    if not refs:
        raise DraftGenerationError(
            "dimension_evidence carries no UUID-shaped research_findings id; "
            "personalization_evidence_ids would be empty"
        )

    rejections: list[str] = []
    for offset in range(len(candidates)):
        citation = candidates[(int(step) - 1 + offset) % len(candidates)]
        subject, body = _render_text(template, company_name, first_name, citation, signer_line)
        problem = _guard(subject, body, company_id, contact_id, campaign, channel, step, template)
        if problem is None:
            return _payload(
                qualification=qualification,
                company_id=company_id,
                contact_id=contact_id,
                campaign=campaign,
                channel=channel,
                step=int(step),
                template=template,
                subject=subject,
                body=body,
                refs=refs,
                agent_identity=identity,
            )
        rejections.append(f"{citation.dimension}/{citation.finding_id}: {problem}")

    raise DraftGenerationError(
        "no candidate citation produced an acceptable draft (the length caps and the content "
        "guards are checked per candidate): " + "; ".join(rejections)
    )


def _render_text(
    template: StepTemplate,
    company_name: str,
    first_name: str,
    citation: Citation,
    signer: str,
) -> tuple[str, str]:
    short_name = _shorten(company_name, 40)
    subject = template.subject.format(company=short_name)
    body = template.body.format(
        first_name=_shorten(first_name, 40),
        company=short_name,
        quote=citation.quote,
        finding_id=citation.finding_id,
        signer=signer,
    )
    # A signer line that ends the body with a trailing blank line reads as a
    # truncated message; trim it rather than leave the artefact in.
    body = "\n".join(line.rstrip() for line in body.splitlines()).strip() + "\n"
    return subject, body


def _guard(
    subject: str,
    body: str,
    company_id: str,
    contact_id: str,
    campaign: str | None,
    channel: str,
    step: int,
    template: StepTemplate,
) -> str | None:
    """Return why this text may not be used, or ``None`` when it is clean.

    Two layers, both necessary: the gate's own guards decide what the workflow will
    accept (they run again inside ``create_draft``), and the local price/timeline
    guard covers the things the gate deliberately leaves to human review. A quote
    that trips either one is replaced by the next candidate citation.
    """
    if not subject.strip():
        return "subject is empty"
    if len(subject) > MAX_SUBJECT_CHARS:
        return f"subject is {len(subject)} characters, over the {MAX_SUBJECT_CHARS} cap"
    if "\n" in subject or "\r" in subject:
        return "subject contains a line break"
    if len(body) > MAX_BODY_CHARS:
        return f"body is {len(body)} characters, over the {MAX_BODY_CHARS} cap"

    try:
        request = DraftRequest(
            company_id=company_id,
            contact_id=contact_id,
            channel=channel,
            outreach_event=template.outreach_event,
            subject=subject,
            body=body,
            sequence_step=step,
            campaign_id=campaign,
        )
    except DraftRequestError as exc:
        return f"the gate would not accept the request: {exc}"

    codes = _content_codes(request)
    if codes:
        return "the gate's content guards refuse it: " + ", ".join(
            f"{code.value} ({detail})" for code, detail in codes
        )

    normalized = normalize_for_matching(f"{subject}\n{body}")
    for pattern, label in _PRICE_AND_TIMELINE:
        if re.search(pattern, normalized):
            return f"it would {label}"
    return None


def _payload(
    *,
    qualification: QualificationRecord,
    company_id: str,
    contact_id: str,
    campaign: str | None,
    channel: str,
    step: int,
    template: StepTemplate,
    subject: str,
    body: str,
    refs: Sequence[str],
    agent_identity: str,
) -> DraftPayload:
    """Assemble the payload the gate's ``build_draft_payload`` would assemble.

    Kept in one place so the text, the evidence array, the idempotency key and the
    provenance string cannot be built from different values.
    """
    from intelligence.ipp.rubric import RUBRIC_VERSION

    provenance = {
        **build_provenance(qualification, RUBRIC_VERSION),
        "outreach_event": template.outreach_event,
        "sequence_step": step,
    }
    return DraftPayload(
        company_id=company_id,
        contact_id=contact_id,
        campaign_id=campaign,
        channel=channel,
        subject=subject,
        body=body,
        personalization_evidence_ids=tuple(refs),
        personalization_rationale=format_provenance(provenance),
        sequence_step=step,
        proposed_commercial_model="project_based_wholesale",
        drafted_by_agent_identity=agent_identity,
        outreach_event=template.outreach_event,
        idempotency_key=row_idempotency_key(
            {
                "company_id": company_id,
                "campaign_id": campaign,
                "channel": channel,
                "sequence_step": step,
            }
        ),
        provenance=provenance,
    )
