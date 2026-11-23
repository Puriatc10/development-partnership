"""Research execution: one registered company in, persisted research out.

Register used to be a dead end — a company row with no research behind it, so every
downstream step (qualification, outreach) had nothing to read. This module closes that loop
with the smallest honest surface:

* a provider produces a :class:`ResearchPayload` for a company (prose and sources, or
  structured findings);
* the executor validates every finding against the *deployed* enums before writing a byte,
  then persists ``research_reports`` -> ``research_findings`` -> ``activities`` and finally
  completes the task, all through the RLS-bound client it was handed.

Three invariants are worth stating because they are what makes this safe to retry:

1. **One writer.** Every call goes through the injected client, so the database decides what
   this identity may see and write. There is no connection, no ``service_role`` and no
   ``SET ROLE`` in this module.
2. **Validate before write.** An invalid payload raises before the task is touched, so a bad
   provider answer cannot leave a half-claimed task behind.
3. **Resume, never duplicate.** ``(company_id, report_version)`` is unique in the deployed
   schema, so the version is chosen deterministically and a report that already exists with
   the same summary is *resumed*: findings already stored are read back by ``(report_id,
   statement)`` and only the missing ones are inserted.

The deployed finding type is ``hypothesis`` — not ``inference`` — and a ``verified_fact``
must carry a URL *and* high/medium confidence (``chk_verified_fact_requires_evidence``).
Both are enforced here first so the caller gets a readable error instead of a 23514.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlparse

from observability.run import RunContext

#: ``finding_category_type`` as deployed in db/migrations/20261107174129_core_crm_schema.sql.
CATEGORIES: frozenset[str] = frozenset(
    {
        "business_model",
        "client_roster",
        "technology_stack",
        "scoping_maturity",
        "growth_signals",
        "pain_points",
        "pricing_rates",
        "team_structure",
        "other",
    }
)

#: ``research_finding_type``. The schema says ``hypothesis``; there is no ``inference``.
FINDING_TYPES: frozenset[str] = frozenset({"verified_fact", "hypothesis", "information_gap_tbd"})

#: ``confidence_level_type``.
CONFIDENCES: frozenset[str] = frozenset({"high", "medium", "low", "unverified"})

#: Mirrors ``chk_verified_fact_requires_evidence``: a verified fact needs evidence_url AND one
#: of these confidences. ``low`` on a verified_fact is refused by the database.
VERIFIED_FACT_CONFIDENCES: frozenset[str] = frozenset({"high", "medium"})

#: Roles ``is_human_operator()`` accepts.
HUMAN_ROLES: frozenset[str] = frozenset({"operator", "admin", "founder"})

SUPPORTED_URL_SCHEMES: tuple[str, ...] = ("http", "https")

#: Agents write activities as ``performed_by_actor_type = 'agent'``; human operators as 'human'.
ACTOR_AGENT = "agent"
ACTOR_HUMAN = "human"


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------


class ResearchError(RuntimeError):
    """Base class: the research run could not complete as asked."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ResearchValidationError(ValueError):
    """A payload finding would violate the deployed enums or constraints."""


class ResearchIdentityError(ResearchError):
    """The client carries no identity that could own a research report."""


class CompanyNotVisibleError(ResearchError):
    """The company does not exist, or this identity cannot see it."""


class CompanySuppressedError(ResearchError):
    """A suppressed company must not be researched."""


class TaskNotVisibleError(ResearchError):
    """The task does not exist, or this identity cannot see it."""


class TaskScopeError(ResearchError):
    """The task belongs to another company than the one being researched."""


class TaskOwnershipError(ResearchError):
    """An agent tried to complete a task that is not assigned to it."""


class TaskStateError(ResearchError):
    """The task is cancelled, or moved in a way an agent may not move it."""


class DuplicateReportVersion(ResearchError):
    """A different report already owns this (company_id, report_version)."""


class ResearchWriteDenied(ResearchError):
    """The database refused the write (SQLSTATE 42501).

    ``missing_claim`` names the exact JWT claim or scope the policy needed, so the caller
    does not have to guess which part of the identity is wrong.
    """

    def __init__(self, message: str, *, table: str = "", missing_claim: str | None = None) -> None:
        super().__init__(message, code="42501")
        self.table = table
        self.missing_claim = missing_claim


class ResearchPersistenceError(ResearchError):
    """A read or write failed for a reason other than a policy denial."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        report_id: str | None = None,
        findings_written: int = 0,
    ) -> None:
        super().__init__(message, code=code)
        self.report_id = report_id
        self.findings_written = findings_written


class ResearchProviderError(ResearchError):
    """The provider returned something that cannot be turned into a payload."""


# -----------------------------------------------------------------------------
# Payload
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One citable source. Stored alongside the activity, not in its own table."""

    url: str
    title: str = ""
    excerpt: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"url": self.url, "title": self.title, "excerpt": self.excerpt}


@dataclass(frozen=True)
class Finding:
    """One atomic evidence statement: the shape ``research_findings`` stores."""

    category: str
    finding_type: str
    statement: str
    confidence: str
    evidence_url: str | None = None
    evidence_excerpt: str | None = None

    def as_row(self, *, report_id: str, company_id: str) -> dict[str, Any]:
        return {
            "report_id": report_id,
            "company_id": company_id,
            "finding_type": self.finding_type,
            "category": self.category,
            "statement": self.statement,
            "evidence_url": self.evidence_url or None,
            "evidence_excerpt": self.evidence_excerpt or None,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ResearchPayload:
    """What a provider hands back for one company."""

    summary: str
    recommended_angle: str | None = None
    overall_confidence: str = "medium"
    sources: Sequence[Source] = ()
    findings: Sequence[Finding] = ()
    gaps_and_unknowns: str | None = None


@runtime_checkable
class ResearchProvider(Protocol):
    """The only thing the executor needs from a research source."""

    name: str

    def produce(self, company: Mapping[str, Any]) -> ResearchPayload: ...


# -----------------------------------------------------------------------------
# Coercion and validation
# -----------------------------------------------------------------------------


def _coerce_source(raw: Any, index: int) -> Source:
    if isinstance(raw, Source):
        return raw
    if isinstance(raw, str):
        return Source(url=raw.strip())
    if isinstance(raw, Mapping):
        return Source(
            url=str(raw.get("url") or raw.get("href") or "").strip(),
            title=str(raw.get("title") or ""),
            excerpt=str(raw.get("excerpt") or raw.get("snippet") or ""),
        )
    raise ResearchValidationError(f"source[{index}] must be a URL string, Source or mapping")


def _coerce_finding(raw: Any, index: int) -> Finding:
    if isinstance(raw, Finding):
        return raw
    if not isinstance(raw, Mapping):
        raise ResearchValidationError(f"finding[{index}] must be a Finding or mapping")
    return Finding(
        category=str(raw.get("category") or "").strip(),
        finding_type=str(raw.get("finding_type") or "").strip(),
        statement=str(raw.get("statement") or "").strip(),
        confidence=str(raw.get("confidence") or "").strip(),
        evidence_url=(str(raw.get("evidence_url") or "").strip() or None),
        evidence_excerpt=(str(raw.get("evidence_excerpt") or "").strip() or None),
    )


def load_payload(raw: Any) -> ResearchPayload:
    """Accept a ResearchPayload or its mapping form and normalise the members."""
    if isinstance(raw, ResearchPayload):
        payload = raw
    elif isinstance(raw, Mapping):
        payload = ResearchPayload(
            summary=str(raw.get("summary") or "").strip(),
            recommended_angle=(str(raw.get("recommended_angle") or "").strip() or None),
            overall_confidence=str(raw.get("overall_confidence") or "medium").strip(),
            sources=tuple(raw.get("sources") or ()),
            findings=tuple(raw.get("findings") or ()),
            gaps_and_unknowns=(str(raw.get("gaps_and_unknowns") or "").strip() or None),
        )
    else:
        raise ResearchValidationError(
            f"a provider must return a ResearchPayload or mapping, got {type(raw).__name__}"
        )

    findings = tuple(_coerce_finding(f, i) for i, f in enumerate(payload.findings or ()))
    sources = tuple(_coerce_source(s, i) for i, s in enumerate(payload.sources or ()))
    return ResearchPayload(
        summary=str(payload.summary or "").strip(),
        recommended_angle=payload.recommended_angle,
        overall_confidence=str(payload.overall_confidence or "").strip(),
        sources=sources,
        findings=findings,
        gaps_and_unknowns=(str(payload.gaps_and_unknowns).strip() or None)
        if payload.gaps_and_unknowns
        else None,
    )


def _is_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in SUPPORTED_URL_SCHEMES and bool(parsed.netloc)


def validate_payload(payload: ResearchPayload) -> tuple[Finding, ...]:
    """Refuse anything the database would refuse, but with a readable message.

    Every check here mirrors a deployed enum or constraint. Duplicate statements are
    collapsed (first wins) so a provider that repeats itself cannot write the same evidence
    twice inside one report.
    """
    if not payload.summary or not payload.summary.strip():
        raise ResearchValidationError("research_reports.summary is NOT NULL: the payload has no summary")

    if payload.overall_confidence not in CONFIDENCES:
        raise ResearchValidationError(
            f"overall_confidence {payload.overall_confidence!r} is not a confidence_level_type; "
            f"allowed: {', '.join(sorted(CONFIDENCES))}"
        )

    if not payload.findings:
        raise ResearchValidationError(
            "the payload carries no findings; a report with no evidence cannot back qualification"
        )

    seen: set[str] = set()
    unique: list[Finding] = []
    for index, finding in enumerate(payload.findings):
        where = f"finding[{index}]"
        if finding.category not in CATEGORIES:
            raise ResearchValidationError(
                f"{where}.category {finding.category!r} is not a deployed finding_category_type; "
                f"allowed: {', '.join(sorted(CATEGORIES))}"
            )
        if finding.finding_type not in FINDING_TYPES:
            raise ResearchValidationError(
                f"{where}.finding_type {finding.finding_type!r} is not a deployed research_finding_type; "
                f"allowed: {', '.join(sorted(FINDING_TYPES))} (the schema says 'hypothesis', not 'inference')"
            )
        if finding.confidence not in CONFIDENCES:
            raise ResearchValidationError(
                f"{where}.confidence {finding.confidence!r} is not a confidence_level_type; "
                f"allowed: {', '.join(sorted(CONFIDENCES))}"
            )
        if not finding.statement:
            raise ResearchValidationError(f"{where}.statement is empty; research_findings.statement is NOT NULL")

        if finding.finding_type == "verified_fact":
            if not _is_http_url(finding.evidence_url):
                raise ResearchValidationError(
                    f"{where} is a verified_fact without an http(s) evidence_url "
                    f"({finding.evidence_url!r}); chk_verified_fact_requires_evidence would refuse it (23514)"
                )
            if finding.confidence not in VERIFIED_FACT_CONFIDENCES:
                raise ResearchValidationError(
                    f"{where} is a verified_fact with confidence {finding.confidence!r}; the database "
                    f"requires one of {', '.join(sorted(VERIFIED_FACT_CONFIDENCES))} (23514)"
                )

        key = finding.statement
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)

    return tuple(unique)


# -----------------------------------------------------------------------------
# Result
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchResult:
    """The machine-readable outcome of one research execution."""

    status: str  # created | resumed | already_completed
    company_id: str
    company_name: str
    report_id: str | None
    report_version: int | None
    identity: str
    provider: str
    overall_confidence: str | None
    findings_total: int
    findings_written: int
    findings_existing: int
    activity_id: str | None
    task_id: str | None
    task_status: str | None
    task_transitions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def resumed(self) -> bool:
        return self.status == "resumed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "report_id": self.report_id,
            "report_version": self.report_version,
            "identity": self.identity,
            "provider": self.provider,
            "overall_confidence": self.overall_confidence,
            "findings_total": self.findings_total,
            "findings_written": self.findings_written,
            "findings_existing": self.findings_existing,
            "activity_id": self.activity_id,
            "task_id": self.task_id,
            "task_status": self.task_status,
            "task_transitions": list(self.task_transitions),
            "sources": list(self.sources),
            "metadata": dict(self.metadata),
        }


# -----------------------------------------------------------------------------
# Identity
# -----------------------------------------------------------------------------


def claims_of(client: Any) -> dict[str, Any]:
    claims = getattr(client, "claims", None)
    return dict(claims) if isinstance(claims, Mapping) else {}


def _app_metadata(claims: Mapping[str, Any]) -> dict[str, Any]:
    value = claims.get("app_metadata")
    return dict(value) if isinstance(value, Mapping) else {}


def user_role(claims: Mapping[str, Any]) -> str:
    return str(_app_metadata(claims).get("user_role") or "")


def is_agent(claims: Mapping[str, Any]) -> bool:
    return user_role(claims) == "agent"


def actor_type(claims: Mapping[str, Any]) -> str:
    """The value ``activities.performed_by_actor_type`` must carry."""
    return ACTOR_AGENT if is_agent(claims) else ACTOR_HUMAN


def resolve_identity(claims: Mapping[str, Any]) -> str:
    """``current_agent_identity()`` for agents, ``sub`` for humans."""
    app = _app_metadata(claims)
    if app.get("user_role") == "agent" and app.get("agent_id"):
        return str(app["agent_id"])
    return str(claims.get("sub") or "")


def explain_write_denial(
    claims: Mapping[str, Any],
    *,
    table: str,
    identity: str,
    task: Mapping[str, Any] | None,
    detail: str = "",
) -> tuple[str, str | None]:
    """Name the claim or scope the policy needed, for a 42501 refusal.

    Ordered from the outermost requirement inward, because fixing the role before the
    subagent type is the only order that makes the next attempt meaningful.
    """
    app = _app_metadata(claims)
    role = str(app.get("user_role") or "")

    if not claims:
        return (
            "no JWT claims were supplied to the client, so every RLS policy denies the write",
            "app_metadata.user_role",
        )
    if not role:
        return (
            "app_metadata.user_role is missing; human writes need operator/admin/founder and "
            "agent writes need 'agent'",
            "app_metadata.user_role",
        )
    if role not in HUMAN_ROLES and role != "agent":
        return (
            f"app_metadata.user_role is {role!r}; research writes need one of "
            f"{', '.join(sorted(HUMAN_ROLES))} or 'agent'",
            "app_metadata.user_role",
        )

    if role == "agent":
        subagent = str(app.get("subagent_type") or "")
        if subagent != "researcher":
            return (
                f"app_metadata.subagent_type is {subagent or 'absent'!r}; research writes require "
                "'researcher' (is_agent_type('researcher'))",
                "app_metadata.subagent_type",
            )
        if not app.get("agent_id"):
            return (
                "app_metadata.agent_id is missing; research_agent_identity must equal "
                "current_agent_identity(), which falls back to 'sub'",
                "app_metadata.agent_id",
            )
        if str(app.get("agent_id")) != identity:
            return (
                f"the identity passed to the executor ({identity!r}) is not the claim's agent_id "
                f"({app.get('agent_id')!r}); research_agent_identity must equal "
                "current_agent_identity()",
                "app_metadata.agent_id (must equal the identity the executor writes)",
            )
        if task is None:
            return (
                "no task_id was supplied; agent RLS requires an assigned task with status "
                "pending/in_progress for this company",
                "tasks.status (assigned task in pending/in_progress)",
            )
        status = str(task.get("status") or "")
        if status not in ("pending", "in_progress"):
            return (
                f"the assigned task {task.get('id')} is {status!r}; agent RLS requires pending or "
                "in_progress while writing research",
                "tasks.status (assigned task in pending/in_progress)",
            )
        if task.get("assigned_actor_identity") not in (None, identity):
            return (
                f"the task is assigned to {task.get('assigned_actor_identity')!r}, not to this agent",
                "tasks.assigned_actor_identity (must equal current_agent_identity())",
            )

    return (
        f"the policy still refused {table} with the expected claims present "
        f"(user_role={role!r}); the row's company/task scope or the table grant does not match. "
        f"Database said: {detail[:200]}",
        None,
    )


# -----------------------------------------------------------------------------
# Data access — all through the injected client
# -----------------------------------------------------------------------------


def _call(
    operation: str,
    *,
    table: str,
    client: Any,
    claims: Mapping[str, Any],
    identity: str,
    task: Mapping[str, Any] | None,
    fn: Any,
) -> Any:
    """Run one client call and translate its failure into a research error."""
    try:
        return fn()
    except ResearchError:
        raise
    except Exception as exc:  # noqa: BLE001 - the client's error type is its own business
        code = getattr(exc, "code", None)
        if code == "42501" or "permission denied" in str(exc).lower():
            message, claim = explain_write_denial(
                claims, table=table, identity=identity, task=task, detail=str(exc)
            )
            raise ResearchWriteDenied(
                f"{operation} on {table} was denied for {identity!r}: {message} [42501]",
                table=table,
                missing_claim=claim,
            ) from exc
        raise ResearchPersistenceError(
            f"{operation} on {table} failed for {identity!r}: {exc}",
            code=str(code) if code else None,
        ) from exc


def _company_columns() -> str:
    return (
        "id,name,primary_domain,primary_segment,lifecycle_stage,is_suppressed,"
        "research_status,website_url"
    )


def _read_company(client: Any, company_id: str, *, claims: Mapping[str, Any], identity: str) -> dict[str, Any] | None:
    rows = _call(
        "read",
        table="companies",
        client=client,
        claims=claims,
        identity=identity,
        task=None,
        fn=lambda: client.get(
            "companies", {"select": _company_columns(), "id": f"eq.{company_id}", "limit": 1}
        ),
    )
    return dict(rows[0]) if rows else None


def _task_columns() -> str:
    return "id,status,company_id,assigned_actor_type,assigned_actor_identity,completed_at"


def _read_task(client: Any, task_id: str, *, claims: Mapping[str, Any], identity: str) -> dict[str, Any] | None:
    rows = _call(
        "read",
        table="tasks",
        client=client,
        claims=claims,
        identity=identity,
        task=None,
        fn=lambda: client.get("tasks", {"select": _task_columns(), "id": f"eq.{task_id}", "limit": 1}),
    )
    return dict(rows[0]) if rows else None


def _read_reports(client: Any, company_id: str, *, claims: Mapping[str, Any], identity: str) -> list[dict[str, Any]]:
    rows = _call(
        "read",
        table="research_reports",
        client=client,
        claims=claims,
        identity=identity,
        task=None,
        fn=lambda: client.get(
            "research_reports",
            {
                "select": "id,company_id,research_agent_identity,report_version,summary,overall_confidence",
                "company_id": f"eq.{company_id}",
                "order": "report_version.desc",
            },
        ),
    )
    return [dict(row) for row in rows]


def _read_findings(client: Any, report_id: str, *, claims: Mapping[str, Any], identity: str) -> list[dict[str, Any]]:
    rows = _call(
        "read",
        table="research_findings",
        client=client,
        claims=claims,
        identity=identity,
        task=None,
        fn=lambda: client.get(
            "research_findings",
            {"select": "id,statement,finding_type,category,confidence", "report_id": f"eq.{report_id}"},
        ),
    )
    return [dict(row) for row in rows]


def _read_activities(client: Any, company_id: str, *, claims: Mapping[str, Any], identity: str) -> list[dict[str, Any]]:
    rows = _call(
        "read",
        table="activities",
        client=client,
        claims=claims,
        identity=identity,
        task=None,
        fn=lambda: client.get(
            "activities",
            {
                "select": "id,activity_type,metadata",
                "company_id": f"eq.{company_id}",
                "order": "occurred_at.desc",
                "limit": 200,
            },
        ),
    )
    return [dict(row) for row in rows]


def _write_report(
    client: Any,
    row: Mapping[str, Any],
    *,
    claims: Mapping[str, Any],
    identity: str,
    task: Mapping[str, Any] | None,
) -> dict[str, Any]:
    try:
        created = _call(
            "insert",
            table="research_reports",
            client=client,
            claims=claims,
            identity=identity,
            task=task,
            fn=lambda: client.post("research_reports", dict(row)),
        )
    except ResearchPersistenceError as exc:
        if exc.code == "23505":
            # Another session took this version between our read and our insert. The unique
            # index is the backstop the local check cannot replace.
            raise DuplicateReportVersion(
                f"report_version {row.get('report_version')} for company {row.get('company_id')} "
                "was written concurrently by another session (23505 on "
                "uq_research_reports_company_version); refusing to write a second report at "
                "the same version. Re-run to pick the next version."
            ) from exc
        raise
    if not created or not isinstance(created[0], Mapping) or not created[0].get("id"):
        raise ResearchPersistenceError(
            "research_reports insert returned no row id; refusing to attach findings to an unknown report"
        )
    return dict(created[0])


def _insert_finding(
    client: Any,
    row: Mapping[str, Any],
    *,
    claims: Mapping[str, Any],
    identity: str,
    task: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """One statement per finding, so an interruption leaves a resumable prefix."""
    created = _call(
        "insert",
        table="research_findings",
        client=client,
        claims=claims,
        identity=identity,
        task=task,
        fn=lambda: client.post("research_findings", dict(row)),
    )
    if not created or not isinstance(created[0], Mapping) or not created[0].get("id"):
        raise ResearchPersistenceError(
            "research_findings insert returned no row id; cannot confirm the finding landed"
        )
    return dict(created[0])


def _patch_task(
    client: Any,
    task_id: str,
    status: str,
    *,
    claims: Mapping[str, Any],
    identity: str,
    task: Mapping[str, Any] | None,
) -> dict[str, Any]:
    updated = _call(
        "update",
        table="tasks",
        client=client,
        claims=claims,
        identity=identity,
        task=task,
        fn=lambda: client.patch("tasks", {"status": status}, params={"id": f"eq.{task_id}"}),
    )
    if not updated or not isinstance(updated[0], Mapping):
        raise TaskNotVisibleError(
            f"task {task_id} could not be moved to {status!r}: the update matched no visible row. "
            "Agent RLS only updates tasks assigned to the caller (agent_update_assigned_tasks)."
        )
    if str(updated[0].get("status") or "") != status:
        raise TaskStateError(
            f"task {task_id} reported status {updated[0].get('status')!r} after a {status!r} update"
        )
    return dict(updated[0])


def _insert_activity(
    client: Any,
    row: Mapping[str, Any],
    *,
    claims: Mapping[str, Any],
    identity: str,
    task: Mapping[str, Any] | None,
) -> dict[str, Any]:
    created = _call(
        "insert",
        table="activities",
        client=client,
        claims=claims,
        identity=identity,
        task=task,
        fn=lambda: client.post("activities", dict(row)),
    )
    return dict(created[0]) if created and isinstance(created[0], Mapping) else {}


# -----------------------------------------------------------------------------
# Version selection
# -----------------------------------------------------------------------------


def _same_session(report: Mapping[str, Any], *, payload: ResearchPayload, identity: str) -> bool:
    """Is this stored report *this* research session, interrupted?

    Summary + author is the narrowest test that is still deterministic: the same payload a
    retry produces resumes the same report, while a genuinely new report (different summary)
    gets the next version.
    """
    return (
        str(report.get("research_agent_identity") or "") == identity
        and str(report.get("summary") or "").strip() == payload.summary.strip()
    )


def choose_report_version(
    reports: Sequence[Mapping[str, Any]],
    *,
    payload: ResearchPayload,
    identity: str,
    requested: int | None,
) -> tuple[Mapping[str, Any] | None, int]:
    """Return ``(report_to_resume, version)`` deterministically.

    * explicit version: reuse it only if it is this same session, otherwise refuse
      (:class:`DuplicateReportVersion`) — never a second report at one version;
    * no version: the highest existing version is resumed when it is this session,
      otherwise version = highest + 1 (1 when the company has no reports).
    """
    ordered = sorted(reports, key=lambda r: int(r.get("report_version") or 0))

    if requested is not None:
        match = [r for r in ordered if int(r.get("report_version") or 0) == int(requested)]
        if match:
            if _same_session(match[0], payload=payload, identity=identity):
                return match[0], int(requested)
            raise DuplicateReportVersion(
                f"report_version {requested} already exists for this company "
                f"(report {match[0].get('id')}, author {match[0].get('research_agent_identity')!r}); "
                "refusing to write a second report with the same version. Leave report_version "
                "unset to let the executor pick the next one."
            )
        return None, int(requested)

    if not ordered:
        return None, 1

    top = int(ordered[-1].get("report_version") or 0)
    at_top = [r for r in ordered if int(r.get("report_version") or 0) == top]
    for report in at_top:
        if _same_session(report, payload=payload, identity=identity):
            return report, top
    return None, top + 1


# -----------------------------------------------------------------------------
# Executor
# -----------------------------------------------------------------------------


def _gap_summary(payload: ResearchPayload) -> str | None:
    if payload.gaps_and_unknowns:
        return payload.gaps_and_unknowns
    gaps = [f.statement for f in payload.findings if f.finding_type == "information_gap_tbd"]
    return "; ".join(gaps) if gaps else None


def execute_research(
    client: Any,
    company_id: str,
    provider: ResearchProvider,
    *,
    task_id: str | None = None,
    report_version: int | None = None,
    identity: str | None = None,
    run: RunContext | None = None,
) -> ResearchResult:
    """Research one company and persist the report, its findings and the activity.

    Everything happens through ``client``: RLS decides visibility, the database enforces its
    own enums, and the task is completed last — after the report, the findings and the
    activity exist — because the agent policies only permit those writes while the task is
    still pending/in_progress.

    ``run`` is optional instrumentation, not behaviour: the same writes happen with or
    without it. Passing one makes the run traceable (``research.started`` ->
    ``research.findings.persisted`` -> ``research.task.completed`` carry its run/trace ids,
    the report id and the counts); omitting it starts a private context so the events are
    still emitted, under an id the caller did not ask for.
    """
    if not company_id:
        raise ValueError("company_id is required")
    if provider is None:
        raise ValueError("a research provider is required")

    claims = claims_of(client)
    resolved_identity = identity or resolve_identity(claims)
    if not resolved_identity:
        raise ResearchIdentityError(
            "the client carries no identity: pass identity= or supply claims with sub / "
            "app_metadata.agent_id, because research_reports.research_agent_identity is NOT NULL"
        )

    run = run if run is not None else RunContext.begin(stage="research")
    started = run.clock()
    run.structured_event(
        "research.started",
        stage="research",
        counts={},
        company_id=str(company_id),
        task_id=str(task_id) if task_id else None,
        provider=_provider_name(provider),
        identity=resolved_identity,
    )

    # 1. A supplied task defines the run: read it before the company. A completed task removes
    #    the company from an *agent's* visibility (agent_select_companies requires a task in
    #    pending/in_progress), so a retry must be able to say "already completed" instead of
    #    "not visible" — which is what reading the company first would produce.
    task: dict[str, Any] | None = None
    transitions: tuple[str, ...] = ()
    if task_id:
        task = _read_task(client, task_id, claims=claims, identity=resolved_identity)
        if task is None:
            raise TaskNotVisibleError(
                f"task {task_id} is not visible to {resolved_identity!r}; an agent sees only tasks "
                "assigned to it (agent_select_assigned_tasks)"
            )
        if str(task.get("company_id") or "") != str(company_id):
            raise TaskScopeError(
                f"task {task_id} belongs to company {task.get('company_id')}, not {company_id}"
            )
        status = str(task.get("status") or "")
        if status == "cancelled":
            raise TaskStateError(f"task {task_id} is cancelled and cannot be executed")
        if status == "completed":
            # A completed task is the strongest signal the run already landed. Do not write
            # again: agent governance forbids touching completed tasks, and a second report
            # would be a duplicate research session. The company may no longer be visible, so
            # the name is best effort.
            reports = _read_reports(client, company_id, claims=claims, identity=resolved_identity)
            latest = max(reports, key=lambda r: int(r.get("report_version") or 0)) if reports else {}
            visible = _read_company(client, company_id, claims=claims, identity=resolved_identity)
            run.structured_event(
                "research.already_completed",
                stage="research",
                outcome="skipped",
                counts={"findings": 0},
                duration_ms=(run.clock() - started) * 1000,
                company_id=str(company_id),
                task_id=str(task_id),
                report_id=str(latest["id"]) if latest.get("id") else None,
            )
            return ResearchResult(
                status="already_completed",
                company_id=str(company_id),
                company_name=str((visible or {}).get("name") or ""),
                report_id=str(latest["id"]) if latest.get("id") else None,
                report_version=int(latest["report_version"]) if latest.get("report_version") else None,
                identity=resolved_identity,
                provider=_provider_name(provider),
                overall_confidence=latest.get("overall_confidence"),
                findings_total=0,
                findings_written=0,
                findings_existing=0,
                activity_id=None,
                task_id=str(task_id),
                task_status=status,
                task_transitions=(),
                metadata={"reason": "task already completed; nothing was written"},
            )
        if is_agent(claims):
            assigned = task.get("assigned_actor_identity")
            if assigned and str(assigned) != resolved_identity:
                raise TaskOwnershipError(
                    f"task {task_id} is assigned to {assigned!r}, not {resolved_identity!r}; an agent "
                    "may only complete its own tasks"
                )

    # 2. The company must be visible to this identity. An invisible row and a missing row are
    #    deliberately the same answer: RLS is not an existence oracle.
    company = _read_company(client, company_id, claims=claims, identity=resolved_identity)
    if company is None:
        raise CompanyNotVisibleError(
            f"company {company_id} is not visible to {resolved_identity!r}. If this is an agent, "
            "companies are visible only while an assigned task is pending/in_progress "
            "(agent_select_companies); if it is a human, app_metadata.user_role must be one of "
            f"{', '.join(sorted(HUMAN_ROLES))}."
        )
    if company.get("is_suppressed"):
        raise CompanySuppressedError(
            f"company {company_id} is suppressed; suppressed prospects are never researched"
        )

    # 3. Produce and validate. Nothing has been written yet, so a bad payload is free.
    raw = provider.produce(dict(company))
    payload = load_payload(raw)
    findings = validate_payload(payload)

    # 4. Pick the version before claiming the task, so a version conflict costs nothing.
    reports = _read_reports(client, company_id, claims=claims, identity=resolved_identity)
    resume_report, version = choose_report_version(
        reports, payload=payload, identity=resolved_identity, requested=report_version
    )

    # 5. Claim the task: pending -> in_progress.
    if task_id and task is not None and str(task.get("status")) == "pending":
        _patch_task(
            client,
            task_id,
            "in_progress",
            claims=claims,
            identity=resolved_identity,
            task=task,
        )
        transitions = (*transitions, "pending->in_progress")
        task = {**task, "status": "in_progress"}

    # 6. The report: resume it, or insert exactly one at this version.
    if resume_report is not None:
        report = dict(resume_report)
        status = "resumed"
    else:
        report = _write_report(
            client,
            {
                "company_id": str(company_id),
                "research_agent_identity": resolved_identity,
                "report_version": int(version),
                "summary": payload.summary,
                "recommended_angle": payload.recommended_angle,
                "overall_confidence": payload.overall_confidence,
                "gaps_and_unknowns": _gap_summary(payload),
            },
            claims=claims,
            identity=resolved_identity,
            task=task,
        )
        status = "created"

    report_id = str(report["id"])
    stored_version = int(report.get("report_version") or version)

    # 7. Findings: read back by (report_id, statement), insert only what is missing.
    written = 0
    try:
        existing = _read_findings(client, report_id, claims=claims, identity=resolved_identity)
        stored = {str(row.get("statement") or "").strip() for row in existing}
        for finding in findings:
            if finding.statement in stored:
                continue
            _insert_finding(
                client,
                finding.as_row(report_id=report_id, company_id=str(company_id)),
                claims=claims,
                identity=resolved_identity,
                task=task,
            )
            stored.add(finding.statement)
            written += 1
    except ResearchError as exc:
        if isinstance(exc, ResearchPersistenceError):
            raise ResearchPersistenceError(
                f"{exc} (report {report_id} v{stored_version} exists; re-run to resume it)",
                code=exc.code,
                report_id=report_id,
                findings_written=written,
            ) from exc
        raise

    run.structured_event(
        "research.findings.persisted",
        stage="persist",
        counts={
            "findings": len(findings),
            "written": written,
            "existing": len(findings) - written,
        },
        duration_ms=(run.clock() - started) * 1000,
        report_id=report_id,
        report_version=stored_version,
        company_id=str(company_id),
    )

    # 8. Activity: one note per report. Re-read so a retry after the activity landed does not
    #    write a second one.
    activity_id = _existing_activity_id(
        client, company_id=str(company_id), report_id=report_id, claims=claims, identity=resolved_identity
    )
    if activity_id is None:
        activity = _insert_activity(
            client,
            {
                "company_id": str(company_id),
                "activity_type": "note",
                "subject": f"Research report v{stored_version} recorded for {company.get('name') or company_id}",
                "description": payload.summary[:500],
                "performed_by_actor_type": actor_type(claims),
                "performed_by_actor_identity": resolved_identity,
                "metadata": {
                    "report_id": report_id,
                    "report_version": stored_version,
                    "findings_count": len(findings),
                    "findings_written": written,
                    "overall_confidence": payload.overall_confidence,
                    "provider": _provider_name(provider),
                    "sources": [source.url for source in payload.sources][:20],
                },
            },
            claims=claims,
            identity=resolved_identity,
            task=task,
        )
        activity_id = str(activity.get("id") or "") or None

    # 9. Complete the task last: the policies that allowed the writes above require it to
    #    still be pending/in_progress. completed_at is left to fn_enforce_task_governance.
    if task_id and task is not None:
        completed = _patch_task(
            client,
            task_id,
            "completed",
            claims=claims,
            identity=resolved_identity,
            task=task,
        )
        transitions = (*transitions, "in_progress->completed")
        task_status = str(completed.get("status") or "")
    else:
        task_status = None

    run.structured_event(
        "research.task.completed" if task_status else "research.completed",
        stage="complete",
        counts={"written": written, "transitions": len(transitions)},
        duration_ms=(run.clock() - started) * 1000,
        report_id=report_id,
        report_version=stored_version,
        task_id=str(task_id) if task_id else None,
        task_status=task_status,
    )

    return ResearchResult(
        status=status,
        company_id=str(company_id),
        company_name=str(company.get("name") or ""),
        report_id=report_id,
        report_version=stored_version,
        identity=resolved_identity,
        provider=_provider_name(provider),
        overall_confidence=str(report.get("overall_confidence") or payload.overall_confidence),
        findings_total=len(findings),
        findings_written=written,
        findings_existing=len(findings) - written,
        activity_id=activity_id,
        task_id=str(task_id) if task_id else None,
        task_status=task_status,
        task_transitions=transitions,
        sources=tuple(source.url for source in payload.sources),
    )


def _existing_activity_id(
    client: Any,
    *,
    company_id: str,
    report_id: str,
    claims: Mapping[str, Any],
    identity: str,
) -> str | None:
    """Find the note this report already wrote, so a retry does not add a second one."""
    rows = _read_activities(client, company_id, claims=claims, identity=identity)
    for row in rows:
        if str(row.get("activity_type") or "") != "note":
            continue
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            import json  # local: only needed when a driver hands jsonb back as text

            try:
                metadata = json.loads(metadata)
            except ValueError:
                metadata = {}
        if isinstance(metadata, Mapping) and str(metadata.get("report_id") or "") == report_id:
            return str(row.get("id") or "") or None
    return None


def _provider_name(provider: Any) -> str:
    name = getattr(provider, "name", None)
    return str(name) if isinstance(name, str) and name else type(provider).__name__


# -----------------------------------------------------------------------------
# Providers
# -----------------------------------------------------------------------------


SYNTHETIC_MARKER = "[SYNTHETIC FIXTURE]"


class FixtureResearchProvider:
    """A deterministic, clearly-labelled payload so the write path can be exercised offline.

    Nothing here is research: every statement is prefixed so it can never be mistaken for an
    observation, and the URLs are not fetched. It exists to prove the executor writes what a
    provider hands it, not to produce evidence.
    """

    name = "fixture"
    synthetic = True

    def __init__(self, *, include_url: bool = True) -> None:
        self.include_url = include_url

    def produce(self, company: Mapping[str, Any]) -> ResearchPayload:
        name = str(company.get("name") or "Unknown company").strip()
        domain = str(company.get("primary_domain") or "example.invalid").strip()
        base = f"https://{domain}"
        url = f"{base}/about" if self.include_url else None
        return ResearchPayload(
            summary=(
                f"{SYNTHETIC_MARKER} Fixture research for {name} ({domain}). This payload was "
                "generated locally, not researched; it exists only to exercise persistence."
            ),
            recommended_angle=(
                f"{SYNTHETIC_MARKER} Position as a backend engineering partner if the studio's "
                "published work shows Webflow or Framer builds needing custom logic."
            ),
            overall_confidence="low",
            sources=(Source(url=url or f"{base}/", title=f"{SYNTHETIC_MARKER} fabricated source"),)
            if url
            else (),
            gaps_and_unknowns=(
                "TBD: pricing, team composition and current backend vendor are unmeasured in a fixture."
            ),
            findings=(
                Finding(
                    category="technology_stack",
                    finding_type="verified_fact",
                    statement=f"{SYNTHETIC_MARKER} {name} publishes its work on {domain}.",
                    confidence="medium",
                    evidence_url=url,
                    evidence_excerpt="Fabricated excerpt for the fixture provider.",
                ),
                Finding(
                    category="growth_signals",
                    finding_type="hypothesis",
                    statement=(
                        f"{SYNTHETIC_MARKER} {name} may be growing; this is a placeholder hypothesis."
                    ),
                    confidence="low",
                ),
                Finding(
                    category="pricing_rates",
                    finding_type="information_gap_tbd",
                    statement=f"{SYNTHETIC_MARKER} No pricing information was measured for {name}.",
                    confidence="unverified",
                ),
            ),
        )


class IntegrationResearchProvider:
    """Adapts an ``integrations/`` adapter (``execute(ProviderRequest)``) to this protocol.

    The adapter is passed in, never imported here: the integrations layer is only touched when
    a caller asks for it (see :func:`provider_from_integration`). Adapters return prose plus
    sources by design and refuse to grade their own confidence, so unless the response carries
    a structured ``findings`` list this produces one explicit ``information_gap_tbd`` finding —
    honest about the fact that prose was not converted into atomic claims.
    """

    PROSE_GAP_STATEMENT = (
        "[UNSTRUCTURED] The provider returned a prose report without atomic findings; "
        "intelligence extraction is required before this can back a qualification."
    )

    def __init__(
        self,
        provider: Any,
        *,
        max_sources: int = 12,
        questions: Sequence[str] = ("services", "tech_stack", "clients", "team", "growth", "engineering_gaps"),
        timeout_s: float = 180.0,
    ) -> None:
        self.provider = provider
        self.name = _provider_name(provider)
        self.max_sources = max_sources
        self.questions = tuple(questions)
        self.timeout_s = timeout_s

    def produce(self, company: Mapping[str, Any]) -> ResearchPayload:
        from contracts.keys import content_key
        from contracts.provider import ProviderRequest

        name = str(company.get("name") or "").strip()
        domain = str(company.get("primary_domain") or "").strip()
        if not name or not domain:
            raise ResearchProviderError(
                "the provider needs company.name and company.primary_domain; both are required"
            )

        payload = {
            "company": {
                "name": name,
                "primary_domain": domain,
                "segment": str(company.get("primary_segment") or "digital_agency"),
            },
            "questions": list(self.questions),
            "max_sources": int(self.max_sources),
        }
        request = ProviderRequest(
            capability="research",
            payload=payload,
            idempotency_key=content_key({"provider": self.name, **payload}),
            timeout_s=self.timeout_s,
        )
        try:
            result = self.provider.execute(request)
        except Exception as exc:  # noqa: BLE001 - provider errors are the provider's contract
            raise ResearchProviderError(f"provider {self.name} failed: {exc}") from exc

        data = dict(getattr(result, "data", {}) or {})
        report = str(data.get("report") or data.get("research_report") or data.get("summary") or "").strip()
        if not report:
            raise ResearchProviderError(f"provider {self.name} returned an empty report")

        sources = tuple(
            Source(url=str(getattr(e, "url", "")), title=str(getattr(e, "title", "")),
                   excerpt=str(getattr(e, "excerpt", "")))
            for e in (getattr(result, "evidence", ()) or ())
        )
        warnings = tuple(str(w) for w in (getattr(result, "warnings", ()) or ()))

        findings_raw = data.get("findings")
        if isinstance(findings_raw, list) and findings_raw:
            findings = tuple(_coerce_finding(item, i) for i, item in enumerate(findings_raw))
        else:
            findings = (
                Finding(
                    category="other",
                    finding_type="information_gap_tbd",
                    statement=self.PROSE_GAP_STATEMENT,
                    confidence="unverified",
                    evidence_excerpt=report[:600],
                ),
            )

        confidence = "low" if sources else "unverified"
        return ResearchPayload(
            summary=report[:8000],
            recommended_angle=(str(data.get("recommended_angle") or "").strip() or None),
            overall_confidence=confidence,
            sources=sources,
            findings=findings,
            gaps_and_unknowns="; ".join(warnings) if warnings else None,
        )


def provider_from_integration(
    name: str = "gpt_researcher",
    *,
    base_url: str,
    api_token: str = "",
    **kwargs: Any,
) -> ResearchProvider:
    """Build a :class:`ResearchProvider` around an ``integrations/`` adapter, on demand.

    The imports live inside this function so importing ``research.executor`` never pulls the
    integrations layer (or the HTTP stack) into a process that does not need it.
    """
    if name != "gpt_researcher":
        raise ValueError(f"unsupported research integration {name!r}; known: 'gpt_researcher'")

    from integrations.base import ProviderConfig
    from integrations.gpt_researcher import GPTResearcherProvider

    adapter = GPTResearcherProvider(
        ProviderConfig(name=name, base_url=base_url, api_token=api_token)
    )
    return IntegrationResearchProvider(adapter, **kwargs)


__all__ = [
    "ACTOR_AGENT",
    "ACTOR_HUMAN",
    "CATEGORIES",
    "CONFIDENCES",
    "FINDING_TYPES",
    "VERIFIED_FACT_CONFIDENCES",
    "CompanyNotVisibleError",
    "CompanySuppressedError",
    "DuplicateReportVersion",
    "Finding",
    "FixtureResearchProvider",
    "IntegrationResearchProvider",
    "ResearchError",
    "ResearchIdentityError",
    "ResearchPayload",
    "ResearchPersistenceError",
    "ResearchProvider",
    "ResearchProviderError",
    "ResearchResult",
    "ResearchValidationError",
    "ResearchWriteDenied",
    "Source",
    "TaskNotVisibleError",
    "TaskOwnershipError",
    "TaskScopeError",
    "TaskStateError",
    "choose_report_version",
    "execute_research",
    "explain_write_denial",
    "load_payload",
    "provider_from_integration",
    "resolve_identity",
    "validate_payload",
]
