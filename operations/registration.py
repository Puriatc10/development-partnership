"""Human Gate 1, second half: turning an approved candidate into a CRM company.

Registration is the only path by which Discovery reaches ``companies``. The discovery
agent's policy cannot insert there at all, so this module runs exclusively under an
operator JWT — that is the gate, not a convention.

Three rules keep it honest:

1. **Duplicate first.** An approved candidate whose domain already exists in the CRM is
   recorded as ``duplicate`` and linked to the existing company. Nothing is created, and
   the operator is told which company it matched.
2. **Idempotent.** A candidate that already carries ``converted_company_id`` returns that
   company unchanged; a second call after a dropped connection cannot create a second
   record. ``companies.primary_domain`` is UNIQUE and ``fn_sync_company_primary_domain``
   raises on a re-parented domain, so a blind retry would otherwise fail loudly.
3. **No invented facts.** ``primary_segment`` is NOT NULL in the schema, so it must come
   from the operator or from the candidate's own recorded signals; it is never defaulted
   to a plausible-looking value (AGENTS.md §9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from discovery.identity import normalize_domain, normalize_name

from .candidates import (
    APPROVED,
    Gate1Error,
    get_candidate,
    link_company,
    mark_duplicate,
)

COMPANY_TABLE = "companies"
DOMAIN_TABLE = "company_domains"

#: The deployed ``market_segment_type`` enum (core migration line 128).
SEGMENT_VALUES = (
    "digital_agency",
    "webflow_studio",
    "framer_studio",
    "ecommerce_agency",
    "independent_consultant",
    "small_saas",
    "other_recurring",
)

#: ``discovery_source`` is free text; this value marks a company that came through Gate 1.
DISCOVERY_SOURCE = "discovery_candidate"


@dataclass(frozen=True)
class RegistrationResult:
    company: Mapping[str, Any]
    candidate: Mapping[str, Any]
    created: bool
    duplicate: bool

    @property
    def company_id(self) -> str:
        return str(self.company.get("id"))


def _company_by_domain(client: Any, domain: str) -> Mapping[str, Any] | None:
    rows = client.get(
        COMPANY_TABLE,
        {
            "select": "id,name,primary_domain,lifecycle_stage,qualification_tier",
            "primary_domain": f"eq.{domain}",
        },
    )
    return rows[0] if rows else None


def _company_owning_domain(client: Any, domain: str) -> Mapping[str, Any] | None:
    """A secondary domain may belong to a company whose primary domain differs."""
    rows = client.get(
        DOMAIN_TABLE,
        {"select": "company_id,domain,is_primary", "domain": f"eq.{domain}"},
    )
    return rows[0] if rows else None


def _resolve_segment(candidate: Mapping[str, Any], segment: str | None) -> str:
    if segment:
        if segment not in SEGMENT_VALUES:
            raise Gate1Error(
                f"unknown segment {segment!r}; expected one of {list(SEGMENT_VALUES)}"
            )
        return segment
    signals = candidate.get("signals") or {}
    recorded = str(signals.get("segment") or "") if isinstance(signals, Mapping) else ""
    if recorded in SEGMENT_VALUES:
        return recorded
    raise Gate1Error(
        "this candidate records no usable market segment, and primary_segment is NOT NULL. "
        f"Pass --segment with one of {list(SEGMENT_VALUES)} — the platform will not guess a "
        "segment on the operator's behalf."
    )


def _assert_company_row(row: Mapping[str, Any], *, domain: str, name: str) -> None:
    if not row.get("id"):
        raise Gate1Error("the created company came back without an id")
    if str(row.get("primary_domain")) != domain:
        raise Gate1Error(
            f"the created company's domain is {row.get('primary_domain')!r}, expected {domain!r}"
        )
    if str(row.get("name")) != name:
        raise Gate1Error(f"the created company's name is {row.get('name')!r}, expected {name!r}")


def register_candidate(
    client: Any,
    candidate_id: str,
    *,
    operator_id: str,
    segment: str | None = None,
    website_url: str | None = None,
) -> RegistrationResult:
    """Create the CRM company for an approved candidate, or explain why it was not created."""
    candidate = get_candidate(client, candidate_id)

    if candidate.get("converted_company_id"):
        company_id = str(candidate["converted_company_id"])
        rows = client.get(
            COMPANY_TABLE,
            {
                "select": "id,name,primary_domain,lifecycle_stage,qualification_tier",
                "id": f"eq.{company_id}",
            },
        )
        if not rows:
            raise Gate1Error(
                f"candidate {candidate_id!r} points at company {company_id!r}, which is not visible "
                "to this operator; refusing to create a second company"
            )
        return RegistrationResult(
            company=rows[0], candidate=candidate, created=False, duplicate=False
        )

    status = str(candidate.get("status"))
    if status != APPROVED:
        raise Gate1Error(
            f"candidate {candidate_id!r} is {status!r}. Gate 1 requires an explicit approval "
            f"first: `candidates approve {candidate_id}`."
        )

    name = str(candidate.get("name") or "").strip()
    domain = normalize_domain(str(candidate.get("domain") or ""))
    if not name or not domain:
        raise Gate1Error(f"candidate {candidate_id!r} has no usable name/domain")

    existing = _company_by_domain(client, domain)
    if existing is not None:
        updated = mark_duplicate(
            client,
            candidate_id,
            operator_id=operator_id,
            company_id=str(existing["id"]),
            note=f"domain {domain} is already company {existing['id']}",
        )
        return RegistrationResult(company=existing, candidate=updated, created=False, duplicate=True)

    owner = _company_owning_domain(client, domain)
    if owner is not None:
        company_id = str(owner["company_id"])
        rows = client.get(
            COMPANY_TABLE,
            {
                "select": "id,name,primary_domain,lifecycle_stage,qualification_tier",
                "id": f"eq.{company_id}",
            },
        )
        updated = mark_duplicate(
            client,
            candidate_id,
            operator_id=operator_id,
            company_id=company_id,
            note=f"domain {domain} is already registered to company {company_id}",
        )
        return RegistrationResult(
            company=(rows[0] if rows else {"id": company_id, "primary_domain": domain}),
            candidate=updated,
            created=False,
            duplicate=True,
        )

    resolved_segment = _resolve_segment(candidate, segment)
    signals = candidate.get("signals") if isinstance(candidate.get("signals"), Mapping) else {}
    payload = {
        "name": name,
        "normalized_name": normalize_name(name) or domain,
        "primary_domain": domain,
        "website_url": (website_url or f"https://{domain}").strip(),
        "primary_segment": resolved_segment,
        "discovery_source": DISCOVERY_SOURCE,
        "discovery_details": {
            "candidate_id": str(candidate["id"]),
            "source_name": candidate.get("source_name"),
            "source_url": candidate.get("source_url"),
            "batch_key": candidate.get("batch_key"),
            "discovered_by_agent_identity": candidate.get("discovered_by_agent_identity"),
            "signal_keys": sorted(signals) if signals else [],
            "gate": "G1",
            "reviewed_by": str(candidate.get("reviewed_by_human_id") or operator_id),
            "reviewed_at": candidate.get("reviewed_at"),
            "registration_operator": operator_id,
        },
        "lifecycle_stage": "identified",
    }

    created_rows: Sequence[Mapping[str, Any]] = client.post(COMPANY_TABLE, payload)
    if not created_rows:
        raise Gate1Error(
            f"the company for candidate {candidate_id!r} was not created and no row came back; "
            "re-run `register` after checking `candidates show` — the candidate is still approved"
        )
    company = created_rows[0]
    _assert_company_row(company, domain=domain, name=name)

    candidate_after = link_company(client, candidate_id, company_id=str(company["id"]))
    return RegistrationResult(
        company=company, candidate=candidate_after, created=True, duplicate=False
    )
