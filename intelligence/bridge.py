"""Bridge: normalized discovery evidence -> IPP scorecard.

One scoring implementation exists (``intelligence.ipp``); this module only maps
a candidate's evidence rows onto its input model, refusing anything it cannot
classify rather than guessing a category or promoting a hypothesis.

Identity
--------
A finding's identity is whatever the row already carries. When a row is a
persisted ``research_findings`` row its ``id`` (or an explicit ``finding_id``) is
the finding's UUID and is preserved verbatim, so a scorecard dimension can cite
a row that actually exists and ``outreach/gate.py`` can join a draft back to
research. Only a row with no id at all gets a minted ``evidence-{index}``, and
that fallback is reported in :class:`BridgedEvidence` (``synthetic_ids`` /
:meth:`BridgedEvidence.require_real_ids`) instead of being indistinguishable
from a real citation. Minted ids are deliberately not UUID-shaped.

Provenance payload (``evidence_url``, ``evidence_excerpt``, ``source_url``) is
carried through to ``intelligence.ipp.models.Finding``; the engine never scores
it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .ipp import Finding, QualificationInput, QualificationScorecard, score_qualification
from .ipp.models import CONFIDENCE_LEVELS, FINDING_CATEGORIES, FINDING_TYPES

REQUIRED_FIELDS = ("statement", "category", "finding_type", "confidence")

#: Prefix of an id minted here. Not UUID-shaped by construction: a caller that
#: needs a citable ``research_findings.id`` must refuse it.
SYNTHETIC_ID_PREFIX = "evidence-"

#: Shape of ``research_findings.id`` (crm/schema.sql L482), mirroring the check
#: ``outreach/gate.py`` applies to personalization evidence ids.
FINDING_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: Row keys that carry an already-persisted finding id, most specific first.
ID_FIELDS = ("finding_id", "id")


class EvidenceContractError(ValueError):
    """An evidence row cannot be represented as a policy-compliant finding."""


@dataclass(frozen=True)
class BridgedEvidence:
    """Findings plus the provenance decision taken while binding each one.

    ``synthetic_ids`` is empty for evidence that came from real
    ``research_findings`` rows. A caller that needs machine-checkable provenance
    (an outreach draft's ``personalization_evidence_ids`` is UUID[]) must call
    :meth:`require_real_ids` or check :attr:`has_synthetic_ids` rather than
    assuming every returned id exists in the database.
    """

    findings: tuple[Finding, ...] = ()
    synthetic_ids: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.findings)

    def __iter__(self):
        return iter(self.findings)

    def __getitem__(self, index):
        return self.findings[index]

    @property
    def has_synthetic_ids(self) -> bool:
        return bool(self.synthetic_ids)

    @property
    def has_only_real_ids(self) -> bool:
        return not self.synthetic_ids

    def require_real_ids(self) -> None:
        """Raise unless every finding carries a persisted, citable id."""
        if self.synthetic_ids:
            raise EvidenceContractError(
                "evidence carried no persisted research_findings id for "
                f"{len(self.synthetic_ids)} row(s); minted {list(self.synthetic_ids)} "
                "cannot be cited as provenance - persist the findings first or pass "
                "the real ids in"
            )


def _row_identity(row: Mapping[str, Any], index: int) -> tuple[str, bool]:
    """Return ``(finding_id, synthetic)`` for one evidence row.

    A row that supplies an id keeps it untouched, whatever its format; that is
    the only way an identity survives the hop. A row that supplies none gets a
    minted id and is flagged so the caller can refuse it.
    """
    for field_name in ID_FIELDS:
        value = str(row.get(field_name) or "").strip()
        if value:
            return value, False
    return f"{SYNTHETIC_ID_PREFIX}{index}", True


def bridge_evidence(rows: Iterable[Mapping[str, Any]]) -> BridgedEvidence:
    """Validate evidence rows and bind them to engine findings.

    Raises:
        EvidenceContractError: on a missing/invalid required field, a category
            outside the schema enum, or a ``verified_fact`` without a URL. Every
            message names the row index so the offending evidence is findable.
    """
    findings: list[Finding] = []
    synthetic: list[str] = []

    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_FIELDS if not str(row.get(field) or "").strip()]
        if missing:
            raise EvidenceContractError(f"evidence[{index}] missing required field(s): {missing}")

        category = str(row["category"])
        finding_type = str(row["finding_type"])
        confidence = str(row["confidence"])
        if category not in FINDING_CATEGORIES:
            raise EvidenceContractError(
                f"evidence[{index}] category '{category}' is not in the schema enum; "
                "map it explicitly instead of defaulting to 'other'"
            )
        if finding_type not in FINDING_TYPES:
            raise EvidenceContractError(f"evidence[{index}] invalid finding_type '{finding_type}'")
        if confidence not in CONFIDENCE_LEVELS:
            raise EvidenceContractError(f"evidence[{index}] invalid confidence '{confidence}'")

        url = str(row.get("evidence_url") or "").strip()
        if finding_type == "verified_fact" and not url:
            raise EvidenceContractError(
                f"evidence[{index}] is a verified_fact without evidence_url (mirrors chk_verified_fact_requires_evidence)"
            )

        finding_id, is_synthetic = _row_identity(row, index)
        if is_synthetic:
            synthetic.append(finding_id)

        findings.append(
            Finding(
                finding_id=finding_id,
                category=category,
                finding_type=finding_type,  # type: ignore[arg-type]
                confidence=confidence,  # type: ignore[arg-type]
                statement=str(row["statement"]).strip(),
                evidence_url=url or None,
                source_key=str(row.get("source_key") or "") or None,
                excerpt=str(row.get("evidence_excerpt") or "").strip() or None,
                source_url=str(row.get("source_url") or "").strip() or None,
            )
        )

    return BridgedEvidence(findings=tuple(findings), synthetic_ids=tuple(synthetic))


def findings_from_evidence(rows: Iterable[Mapping[str, Any]]) -> list[Finding]:
    """Validate and convert evidence rows; raises rather than inventing values.

    Compatibility shim over :func:`bridge_evidence` that returns only the
    findings. It **discards** the synthetic-id marker, so any caller whose
    citations must be joinable (``personalization_evidence_ids``) has to use
    :func:`bridge_evidence` and call :meth:`BridgedEvidence.require_real_ids`.
    """
    return list(bridge_evidence(rows).findings)


def score_candidate(
    candidate: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    employee_estimate: int | None = None,
    region: str | None = None,
    require_real_ids: bool = False,
) -> QualificationScorecard:
    """Run the canonical engine over a discovery candidate's evidence.

    ``require_real_ids`` refuses evidence that had to be given a minted id; it
    changes nothing about how the surviving findings are scored.
    """
    binding = bridge_evidence(evidence_rows)
    if require_real_ids:
        binding.require_real_ids()
    return score_qualification(
        QualificationInput(
            findings=binding.findings,
            segment=str(candidate.get("segment") or candidate.get("primary_segment") or "digital_agency"),
            employee_estimate=employee_estimate,
            region=region,
            company_name=str(candidate.get("name") or ""),
        )
    )
