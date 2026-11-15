"""Ports: where candidates come from and where they may be staged.

The workflow owns the ports; providers implement them. That is what keeps
Discovery from depending on an undocumented OpenGTM endpoint (Directive 02 §8).

Staging is deliberately not "write to companies". A candidate becomes a company
only through Human Gate 1.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence


class CandidateSource(Protocol):
    """A discovery source: returns raw candidate mappings, unvalidated."""

    name: str

    def fetch(self, request: Any) -> Sequence[Mapping[str, Any]]: ...


class CandidateSink(Protocol):
    """Where accepted candidates are staged (never the canonical company table)."""

    def write(
        self,
        request: Any,
        candidates: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> int: ...


class StagingUnavailable(RuntimeError):
    """The staging table is not deployed (migration authored, not applied)."""


class StagingContractError(RuntimeError):
    """The client did not return what a two-stage write needs.

    Raised when an insert comes back without the ``id``/``domain`` pairs the
    evidence association depends on. Guessing ids, or pairing evidence by array
    position, would attach a citation to the wrong company — so this fails.
    """


class AlreadyStaged(Exception):
    """The candidate set is already staged; this is not a success count.

    Carries the constraint that fired so the caller can tell a retry of the same
    batch (``uq_discovery_candidates_batch_domain``) from a domain that another
    batch already staged (``uq_discovery_candidates_pending_domain``).
    """

    def __init__(self, constraint: str, rows_attempted: int) -> None:
        super().__init__(f"already staged: {constraint} rejected {rows_attempted} row(s)")
        self.constraint = constraint
        self.rows_attempted = rows_attempted

    @property
    def same_batch(self) -> bool:
        return self.constraint == "uq_discovery_candidates_batch_domain"


class EvidencePersistenceError(RuntimeError):
    """Candidate rows were written but their evidence was not.

    The candidates exist, so the caller must not report the run as complete. The
    write is safe to retry because candidates are unique per (batch_key, domain)
    and evidence per (candidate_id, url); a retry re-attempts only what is
    missing. This error says "candidates landed, evidence did not".
    """

    def __init__(self, staged_candidates: int, message: str) -> None:
        super().__init__(f"{staged_candidates} candidate(s) staged, evidence write failed: {message}")
        self.staged_candidates = staged_candidates


class JsonFileSource:
    """Fixture source: reads ``{"candidates": [...]}`` or a bare list from disk.

    Used for controlled Development tests and for replaying a real discovery run
    without hitting a provider again. Never used in production paths.
    """

    def __init__(self, path: str | os.PathLike[str], name: str = "json_file") -> None:
        self.path = Path(path)
        self.name = name

    def fetch(self, request: Any) -> list[Mapping[str, Any]]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            rows = payload.get("candidates") or payload.get("results") or []
        else:
            rows = payload
        return [row for row in rows if isinstance(row, Mapping)]


class JsonlOutboxSink:
    """Development fallback staging: append-only JSONL on disk.

    This is NOT a system of record and must not be treated as one: it exists so
    the discovery workflow can be exercised end to end before the
    ``discovery_candidates`` migration is applied. Every record carries
    ``"sink": "jsonl_outbox"`` so an auditor can tell it apart from staged rows.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        request: Any,
        candidates: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> int:
        with self.path.open("a", encoding="utf-8") as handle:
            for candidate in candidates:
                record = dict(candidate)
                record["sink"] = "jsonl_outbox"
                record["candidate_evidence"] = [
                    e for e in evidence if e.get("domain") == candidate.get("domain")
                ]
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return len(candidates)


#: Exactly the columns the pending migration declares. A row carrying anything
#: else is a contract bug, not something to strip silently.
CANDIDATE_COLUMNS = frozenset(
    {
        "name",
        "normalized_name",
        "domain",
        "source_name",
        "source_url",
        "discovered_at",
        "signals",
        "status",
        "batch_key",
        "discovered_by_agent_identity",
    }
)
EVIDENCE_COLUMNS = frozenset({"candidate_id", "url", "excerpt", "retrieved_at", "provider"})
CANDIDATE_UNIQUE_CONSTRAINTS = (
    "uq_discovery_candidates_batch_domain",
    "uq_discovery_candidates_pending_domain",
)


def reject_unknown_columns(rows: Sequence[Mapping[str, Any]], allowed: frozenset[str], table: str) -> None:
    """Refuse a payload that would not match the deployed table exactly."""
    for index, row in enumerate(rows):
        unknown = sorted(set(row) - allowed)
        if unknown:
            raise StagingContractError(
                f"{table} row {index} carries column(s) the migration does not declare: {unknown}; "
                f"allowed: {sorted(allowed)}"
            )


class SupabaseCandidateSink:
    """Staging sink for ``discovery_candidates`` — two-stage, id-verified writes.

    Stage 1 inserts candidate rows with ``Prefer: return=representation`` so the
    database returns the generated ``id`` for each row. Stage 2 associates every
    evidence record with the candidate that owns its domain and inserts it into
    ``discovery_candidate_evidence``. Evidence is never paired by array position:
    if the response does not carry ``id`` + ``domain`` for every row, the write
    fails with :class:`StagingContractError` instead of guessing.

    Takes any object exposing ``post(table, rows)`` — the platform's
    ``PostgrestClient`` or a fake.
    """

    TABLE = "discovery_candidates"
    EVIDENCE_TABLE = "discovery_candidate_evidence"

    def __init__(self, client: Any) -> None:
        self.client = client

    def write(
        self,
        request: Any,
        candidates: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> int:
        if not candidates:
            return 0

        rows = [dict(c) for c in candidates]
        reject_unknown_columns(rows, CANDIDATE_COLUMNS, self.TABLE)

        try:
            inserted = self.client.post(self.TABLE, rows)
        except Exception as exc:  # noqa: BLE001 - normalize any client error
            message = str(exc)
            if "PGRST205" in message or "does not exist" in message or "Could not find the table" in message:
                raise StagingUnavailable(
                    "discovery_candidates is not deployed. Apply "
                    "supabase/migrations/pending/*_discovery_candidates.sql first, or use "
                    "JsonlOutboxSink for a local exercise."
                ) from exc
            for constraint in CANDIDATE_UNIQUE_CONSTRAINTS:
                if constraint in message:
                    raise AlreadyStaged(constraint, len(rows)) from exc
            raise

        if not isinstance(inserted, list) or len(inserted) != len(rows):
            raise StagingContractError(
                f"{self.TABLE} insert returned {len(inserted) if isinstance(inserted, list) else 'no'} row(s) "
                f"for {len(rows)} candidate(s); stage 2 needs one id per row and will not guess"
            )
        id_by_domain: dict[str, Any] = {}
        for row in inserted:
            if not isinstance(row, Mapping) or not row.get("id") or not row.get("domain"):
                raise StagingContractError(
                    f"{self.TABLE} insert did not return id+domain for every row; got {row!r}"
                )
            id_by_domain[str(row["domain"])] = row["id"]

        missing = sorted({c["domain"] for c in rows} - set(id_by_domain))
        if missing:
            raise StagingContractError(f"inserted rows did not come back for domain(s): {missing}")

        evidence_rows = self._evidence_payload(id_by_domain, evidence)
        if evidence_rows:
            reject_unknown_columns(evidence_rows, EVIDENCE_COLUMNS, self.EVIDENCE_TABLE)
            try:
                self.client.post(self.EVIDENCE_TABLE, evidence_rows)
            except Exception as exc:  # noqa: BLE001 - candidates are already written
                raise EvidencePersistenceError(len(inserted), str(exc)) from exc

        return len(inserted)

    @staticmethod
    def _evidence_payload(
        id_by_domain: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach each evidence record to its candidate, dropping the association key."""
        out: list[dict[str, Any]] = []
        for item in evidence:
            domain = str(item.get("domain") or "")
            candidate_id = id_by_domain.get(domain)
            if candidate_id is None:
                raise StagingContractError(
                    f"evidence for domain {domain!r} has no staged candidate; "
                    "refusing to guess which company it belongs to"
                )
            out.append(
                {
                    "candidate_id": candidate_id,
                    "url": item.get("url"),
                    "excerpt": item.get("excerpt") or "",
                    "retrieved_at": item.get("retrieved_at"),
                    "provider": item.get("provider"),
                }
            )
        return out


def candidate_evidence_rows(candidate: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    """Extract the evidence rows a normalized candidate carries.

    Provenance is whatever the source actually returned; nothing is synthesized.
    """
    for item in candidate.get("evidence") or []:
        if isinstance(item, Mapping) and item.get("url"):
            yield {
                "domain": candidate.get("domain"),
                "url": item.get("url"),
                "excerpt": item.get("excerpt") or item.get("snippet") or "",
                "retrieved_at": item.get("retrieved_at") or candidate.get("discovered_at"),
                "provider": item.get("provider") or candidate.get("source_name"),
            }
