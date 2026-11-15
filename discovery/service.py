"""Discovery service: directive -> normalized, deduped, staged candidates.

Deterministic and provider-agnostic. It never writes a company, never invents a
domain, and never resolves a domain conflict by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from contracts.keys import content_key
from contracts.models import SEGMENTS

from .identity import (
    DOMAIN_CONFLICT,
    EXACT_DOMAIN,
    EXACT_NAME,
    CandidateIdentity,
    normalize_domain,
    normalize_name,
    resolve_identity,
)
from .ports import AlreadyStaged, CandidateSink, CandidateSource, StagingResult, candidate_evidence_rows

APPROVED_SOURCES = frozenset(
    {
        "webflow_experts",
        "webflow_showcase",
        "framer_partners",
        "framer_made_in",
        "clutch",
        "dribbble",
        "behance",
    }
)


class DiscoveryRequestError(ValueError):
    """The directive is not executable as given."""


class DiscoveryIdentityError(RuntimeError):
    """No trusted execution identity was supplied.

    ``discovered_by_agent_identity`` is a claim about *who staged this row*, and
    the pending migration's RLS policy compares it to the JWT. It therefore comes
    from the execution context, never from a discovery provider's payload, and a
    blank identity is a hard failure rather than a default.
    """


#: Agent identity shape: lower-case, starts alphanumeric, no spaces. Deliberately
#: strict — this value is compared to ``current_agent_identity()`` in the policy.
ACTOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,63}$")


@dataclass(frozen=True)
class DiscoveryRequest:
    segments: Sequence[str]
    sources: Sequence[str]
    limit: int = 25
    region: str = "global"
    exclude_domains: Sequence[str] = ()
    campaign_id: str = ""

    def __post_init__(self) -> None:
        if not self.segments:
            raise DiscoveryRequestError("at least one segment is required")
        unknown = [s for s in self.segments if s not in SEGMENTS]
        if unknown:
            raise DiscoveryRequestError(f"unknown segment(s): {unknown}; allowed: {sorted(SEGMENTS)}")
        if not self.sources:
            raise DiscoveryRequestError("at least one source is required")
        bad = [s for s in self.sources if s not in APPROVED_SOURCES]
        if bad:
            raise DiscoveryRequestError(
                f"unapproved source(s): {bad}; allowed: {sorted(APPROVED_SOURCES)}"
            )
        if not 1 <= int(self.limit) <= 200:
            raise DiscoveryRequestError("limit must be between 1 and 200")


@dataclass(frozen=True)
class DiscoveryOutcome:
    accepted: tuple[dict[str, Any], ...]
    duplicates_in_batch: tuple[dict[str, Any], ...]
    existing_matches: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    rejected: tuple[dict[str, Any], ...]
    already_staged: tuple[dict[str, Any], ...]
    staged: int
    recovered: int
    evidence_staged: int
    idempotency_key: str
    source_name: str
    actor: str
    stats: Mapping[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.accepted) or not self.rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": list(self.accepted),
            "duplicates_in_batch": list(self.duplicates_in_batch),
            "existing_matches": list(self.existing_matches),
            "conflicts": list(self.conflicts),
            "rejected": list(self.rejected),
            "already_staged": list(self.already_staged),
            "staged": self.staged,
            "recovered": self.recovered,
            "evidence_staged": self.evidence_staged,
            "idempotency_key": self.idempotency_key,
            "source_name": self.source_name,
            "actor": self.actor,
            "stats": dict(self.stats),
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce(candidate: Mapping[str, Any], source_name: str, discovered_at: str) -> CandidateIdentity | None:
    """Validate one raw candidate. Returns None when it cannot be identified."""
    name = str(candidate.get("name") or candidate.get("company_name") or "").strip()
    domain = normalize_domain(
        str(candidate.get("domain") or candidate.get("website") or candidate.get("url") or "")
    )
    if not name or not domain:
        return None
    source_url = str(candidate.get("source_url") or candidate.get("url") or "")
    return CandidateIdentity(
        name=name,
        domain=domain,
        source_name=str(candidate.get("source_name") or source_name),
        source_url=source_url,
        normalized_name=normalize_name(name),
        discovered_at=str(candidate.get("discovered_at") or discovered_at),
        signals=dict(candidate.get("signals") or {}),
    )


def compute_batch_key(request: DiscoveryRequest, accepted: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic key for this directive's candidate set.

    Sorted domains make it independent of provider order; campaign and source are
    included so the same domain discovered by a different campaign is a different
    batch (the partial unique index on pending domains still prevents a duplicate
    *row*). Computed before the write and stored on each row, so the database's
    ``(batch_key, domain)`` uniqueness turns a retry into a conflict rather than a
    second candidate.
    """
    return content_key(
        {
            "campaign_id": request.campaign_id,
            "sources": sorted(set(request.sources)),
            "domains": sorted({str(row["domain"]) for row in accepted}),
        }
    )


def run_discovery(
    request: DiscoveryRequest,
    source: CandidateSource,
    sink: CandidateSink,
    *,
    actor: str,
    existing_companies: Sequence[Mapping[str, Any]] = (),
    now: str | None = None,
) -> DiscoveryOutcome:
    """Execute one discovery directive against one source.

    ``actor`` is the trusted execution identity that will be written to
    ``discovered_by_agent_identity``; it must come from the runtime, never from a
    provider payload, and it is validated here rather than defaulted.

    ``existing_companies`` is the read-side truth (id, name, primary_domain) and
    is supplied by the caller so this function stays pure and testable.
    """
    if not isinstance(actor, str) or not ACTOR_PATTERN.match(actor):
        raise DiscoveryIdentityError(
            "run_discovery requires a trusted execution identity in 'actor' "
            f"(got {actor!r}); expected {ACTOR_PATTERN.pattern}. "
            "The staging policy compares this value to the JWT identity, so it cannot be "
            "inferred or defaulted — pass the identity the runner authenticated as."
        )

    discovered_at = now or _now()
    excluded = {normalize_domain(d) for d in request.exclude_domains if normalize_domain(d)}

    raw = list(source.fetch(request))
    accepted: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    seen_domains: set[str] = set()
    seen_names: set[str] = set()

    for item in raw:
        if len(accepted) >= request.limit:
            break
        identity = _coerce(item, source.name, discovered_at)
        if identity is None:
            rejected.append(
                {
                    "name": item.get("name"),
                    "reason": "unidentifiable: name and a valid domain are both required",
                    "source_url": item.get("source_url") or item.get("url"),
                }
            )
            continue
        if identity.domain in excluded:
            rejected.append({"name": identity.name, "domain": identity.domain, "reason": "excluded by directive"})
            continue
        if identity.domain in seen_domains or identity.normalized_name in seen_names:
            duplicates.append({"name": identity.name, "domain": identity.domain, "reason": "duplicate_in_batch"})
            continue

        verdict, match = resolve_identity(identity.domain, identity.normalized_name, list(existing_companies))
        if verdict == DOMAIN_CONFLICT:
            conflicts.append(
                {
                    "name": identity.name,
                    "domain": identity.domain,
                    "reason": "domain owned by one company while the name matches another; human review required",
                }
            )
            continue
        if verdict in (EXACT_DOMAIN, EXACT_NAME):
            matches.append(
                {
                    "name": identity.name,
                    "domain": identity.domain,
                    "matched_company_id": (match or {}).get("id"),
                    "reason": verdict,
                }
            )
            continue

        row = identity.to_row()
        row["source_url"] = identity.source_url
        accepted.append(row)
        seen_domains.add(identity.domain)
        seen_names.add(identity.normalized_name)
        evidence.extend(candidate_evidence_rows({**item, "domain": identity.domain, "discovered_at": discovered_at}))

    # The batch key is computed *before* persistence and carried on every row, so a
    # retry collides on (batch_key, domain) instead of inserting a second copy.
    batch_key = compute_batch_key(request, accepted)
    for row in accepted:
        row["batch_key"] = batch_key
        row["discovered_by_agent_identity"] = actor

    already_staged: list[dict[str, Any]] = []
    result = StagingResult()
    try:
        result = sink.write(request, accepted, evidence)
    except AlreadyStaged as exc:
        # Scenario D: a different batch owns these domains. Report them explicitly
        # and adopt nothing — no evidence is attached to another batch's candidate.
        conflicting = set(exc.domains) or {str(row["domain"]) for row in accepted}
        already_staged = [
            {
                "name": row["name"],
                "domain": row["domain"],
                "reason": "domain_staged_elsewhere",
                "constraint": exc.constraint,
                "same_batch": exc.same_batch,
            }
            for row in accepted
            if str(row["domain"]) in conflicting
        ]

    stats = {
        "raw": len(raw),
        "accepted": len(accepted),
        "duplicates_in_batch": len(duplicates),
        "existing_matches": len(matches),
        "conflicts": len(conflicts),
        "rejected": len(rejected),
        "already_staged": len(already_staged),
        "staged": result.created,
        "recovered": result.recovered,
        "evidence_staged": result.evidence,
    }
    return DiscoveryOutcome(
        accepted=tuple(accepted),
        duplicates_in_batch=tuple(duplicates),
        existing_matches=tuple(matches),
        conflicts=tuple(conflicts),
        rejected=tuple(rejected),
        already_staged=tuple(already_staged),
        staged=result.created,
        recovered=result.recovered,
        evidence_staged=result.evidence,
        idempotency_key=batch_key,
        source_name=source.name,
        actor=actor,
        stats=stats,
    )
