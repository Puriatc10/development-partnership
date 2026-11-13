"""Discovery service: directive -> normalized, deduped, staged candidates.

Deterministic and provider-agnostic. It never writes a company, never invents a
domain, and never resolves a domain conflict by itself.
"""

from __future__ import annotations

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
from .ports import CandidateSink, CandidateSource, candidate_evidence_rows

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
    staged: int
    idempotency_key: str
    source_name: str
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
            "staged": self.staged,
            "idempotency_key": self.idempotency_key,
            "source_name": self.source_name,
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


def run_discovery(
    request: DiscoveryRequest,
    source: CandidateSource,
    sink: CandidateSink,
    *,
    existing_companies: Sequence[Mapping[str, Any]] = (),
    now: str | None = None,
) -> DiscoveryOutcome:
    """Execute one discovery directive against one source.

    ``existing_companies`` is the read-side truth (id, name, primary_domain) and
    is supplied by the caller so this function stays pure and testable.
    """
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
        row["discovery_source"] = source.name
        row["source_url"] = identity.source_url
        accepted.append(row)
        seen_domains.add(identity.domain)
        seen_names.add(identity.normalized_name)
        evidence.extend(candidate_evidence_rows({**item, "domain": identity.domain, "discovered_at": discovered_at}))

    staged = sink.write(request, accepted, evidence)
    key = content_key({"accepted": sorted(c["domain"] for c in accepted), "campaign_id": request.campaign_id})

    stats = {
        "raw": len(raw),
        "accepted": len(accepted),
        "duplicates_in_batch": len(duplicates),
        "existing_matches": len(matches),
        "conflicts": len(conflicts),
        "rejected": len(rejected),
        "staged": staged,
    }
    return DiscoveryOutcome(
        accepted=tuple(accepted),
        duplicates_in_batch=tuple(duplicates),
        existing_matches=tuple(matches),
        conflicts=tuple(conflicts),
        rejected=tuple(rejected),
        staged=staged,
        idempotency_key=key,
        source_name=source.name,
        stats=stats,
    )
