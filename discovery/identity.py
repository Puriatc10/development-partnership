"""Company identity rules shared by discovery, dedupe and domain ownership.

These rules decide whether two records are the same company. They are small,
pure and heavily tested, because a wrong answer here writes the wrong company
into the CRM or moves a domain between companies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# Legal-form suffixes carry no identity information for matching purposes.
_LEGAL_SUFFIXES = (
    "gmbh", "gmbhco", "ltd", "limited", "llc", "inc", "incorporated", "corp",
    "corporation", "co", "company", "plc", "ag", "bv", "nv", "sa", "sas", "srl",
    "pty", "pte", "oy", "ab", "as", "kg", "ug",
)

_WS = re.compile(r"\s+")


def normalize_domain(value: Optional[str]) -> str:
    """Canonical registrable host: lowercase, no scheme/www/port/path/trailing dot.

    Returns ``""`` for anything that is not a plausible host — a candidate
    without a usable domain cannot be deduped and must go to human review, not
    to the staging table with a guessed domain.
    """
    if not value:
        return ""
    d = str(value).strip().lower()
    for scheme in ("https://", "http://", "//"):
        if d.startswith(scheme):
            d = d[len(scheme):]
    d = d.split("/", 1)[0]
    d = d.split("?", 1)[0]
    d = d.split("#", 1)[0]
    if "@" in d:  # never treat credentials as part of a host
        d = d.rsplit("@", 1)[1]
    d = d.split(":", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    d = d.rstrip(".")
    if d.count(".") < 1 or " " in d:
        return ""
    return d


def normalize_name(value: Optional[str]) -> str:
    """Lowercase alphanumeric key with legal suffixes removed.

    ``"Acme Studio, LLC"`` and ``"Acme  Studio"`` collapse to the same key, so a
    re-discovery does not create a second company.
    """
    if not value:
        return ""
    lowered = _WS.sub(" ", str(value)).strip().lower()
    lowered = re.sub(r"[^\w\s&+-]", " ", lowered)  # keep word chars, &, +, -
    tokens = [t for t in lowered.split(" ") if t]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    key = "".join(tokens)
    return key


def domain_root(domain: Optional[str]) -> str:
    """Second-level label of the domain (``acme`` for ``acme.co.uk`` is approximate).

    Deliberately naive: we do not ship a public-suffix list, and using one badly
    is worse than being conservative. Used only for *suggesting* a possible
    duplicate, never for merging.
    """
    d = normalize_domain(domain)
    if not d:
        return ""
    return d.split(".")[0]


@dataclass(frozen=True)
class CandidateIdentity:
    name: str
    domain: str
    source_name: str
    source_url: str
    normalized_name: str
    discovered_at: str
    signals: Mapping[str, Any]

    def to_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "normalized_name": self.normalized_name,
            "domain": self.domain,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "discovered_at": self.discovered_at,
            "signals": dict(self.signals),
            "status": "pending_review",
        }


EXACT_DOMAIN = "exact_domain"
EXACT_NAME = "exact_name"
DOMAIN_CONFLICT = "domain_conflict"
NO_MATCH = "no_match"


def resolve_identity(
    candidate_domain: str,
    candidate_name_key: str,
    existing: list[Mapping[str, Any]],
) -> tuple[str, Optional[Mapping[str, Any]]]:
    """Classify a candidate against existing companies.

    Returns ``(verdict, match)`` where verdict is one of EXACT_DOMAIN,
    EXACT_NAME, DOMAIN_CONFLICT or NO_MATCH.

    ``DOMAIN_CONFLICT`` is the case that must never be auto-merged: the domain
    belongs to one existing company while the name matches a different one. A
    human decides which record is wrong.
    """
    domain = normalize_domain(candidate_domain)
    by_domain = None
    by_name = None
    for row in existing:
        row_domain = normalize_domain(row.get("primary_domain") or row.get("domain"))
        row_name = row.get("normalized_name") or normalize_name(row.get("name"))
        if domain and row_domain == domain:
            by_domain = row
        if candidate_name_key and row_name == candidate_name_key:
            by_name = row

    if by_domain is not None and by_name is not None and by_domain.get("id") != by_name.get("id"):
        return DOMAIN_CONFLICT, None
    if by_domain is not None:
        return EXACT_DOMAIN, by_domain
    if by_name is not None:
        return EXACT_NAME, by_name
    return NO_MATCH, None
