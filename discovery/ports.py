"""Ports: where candidates come from and where they may be staged.

The workflow owns the ports; providers implement them. That is what keeps
Discovery from depending on an undocumented OpenGTM endpoint (Directive 02 §8).

Staging is deliberately not "write to companies". A candidate becomes a company
only through Human Gate 1.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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
    ) -> "StagingResult": ...


@dataclass(frozen=True)
class StagingResult:
    """What a staging write actually did.

    ``created`` counts newly inserted candidates, ``recovered`` counts rows that
    already existed for this batch and were re-used, and ``evidence`` counts
    evidence rows written by *this* call. Keeping them separate is what makes a
    recovered batch distinguishable from a fresh one.
    """

    created: int = 0
    recovered: int = 0
    evidence: int = 0

    @property
    def total(self) -> int:
        return self.created + self.recovered


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
    batch (``uq_discovery_candidates_batch_domain``, which is now *recovered* rather
    than raised) from a domain that another batch already staged.
    """

    def __init__(self, constraint: str, rows_attempted: int, domains: Sequence[str] = ()) -> None:
        super().__init__(f"already staged: {constraint} rejected {rows_attempted} row(s)")
        self.constraint = constraint
        self.rows_attempted = rows_attempted
        self.domains = tuple(domains)

    @property
    def same_batch(self) -> bool:
        return self.constraint == "uq_discovery_candidates_batch_domain"


class PartialRecoveryError(RuntimeError):
    """A same-batch retry found candidate rows that do not match the batch exactly.

    Recovery is only safe when the persisted rows are precisely the expected
    candidate set for this ``batch_key`` + actor. A partial or foreign set means we
    cannot tell what landed, so the run fails instead of attaching evidence to a
    guess.
    """

    def __init__(self, message: str, *, expected: Sequence[str] = (), found: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.expected = tuple(expected)
        self.found = tuple(found)


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
    ) -> "StagingResult":
        written = 0
        with self.path.open("a", encoding="utf-8") as handle:
            for candidate in candidates:
                record = dict(candidate)
                record["sink"] = "jsonl_outbox"
                record["candidate_evidence"] = [
                    e for e in evidence if e.get("domain") == candidate.get("domain")
                ]
                written += len(record["candidate_evidence"])
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return StagingResult(created=len(candidates), evidence=written)


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
BATCH_CONSTRAINT = "uq_discovery_candidates_batch_domain"
PENDING_DOMAIN_CONSTRAINT = "uq_discovery_candidates_pending_domain"
CANDIDATE_UNIQUE_CONSTRAINTS = (BATCH_CONSTRAINT, PENDING_DOMAIN_CONSTRAINT)


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
    ) -> StagingResult:
        if not candidates:
            return StagingResult()

        rows = [dict(c) for c in candidates]
        reject_unknown_columns(rows, CANDIDATE_COLUMNS, self.TABLE)
        batch_key = self._single(rows, "batch_key")
        actor = self._single(rows, "discovered_by_agent_identity")
        expected_domains = [str(row["domain"]) for row in rows]

        try:
            inserted = self.client.post(self.TABLE, rows)
        except Exception as exc:  # noqa: BLE001 - normalize any client error
            message = str(exc)
            if self._is_missing_table(message):
                raise StagingUnavailable(
                    "discovery_candidates is not deployed. Apply "
                    "supabase/migrations/pending/*_discovery_candidates.sql first, or use "
                    "JsonlOutboxSink for a local exercise."
                ) from exc
            constraint = self._constraint_from(message)
            if constraint == BATCH_CONSTRAINT:
                # Scenario C: this batch is already staged. Recover its real ids and
                # finish the evidence stage instead of returning early.
                id_by_domain = self._recover_batch(batch_key, actor, expected_domains)
                written = self._write_evidence(id_by_domain, evidence)
                return StagingResult(created=0, recovered=len(id_by_domain), evidence=written)
            if constraint == PENDING_DOMAIN_CONSTRAINT:
                # Scenario D: another batch owns these domains. Never adopt them.
                raise AlreadyStaged(constraint, len(rows), domains=expected_domains) from exc
            raise

        id_by_domain = self._index_inserted(inserted, expected_domains)
        written = self._write_evidence(id_by_domain, evidence)
        return StagingResult(created=len(rows), evidence=written)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _single(rows: Sequence[Mapping[str, Any]], column: str) -> str:
        values = {str(row.get(column) or "") for row in rows}
        if len(values) != 1 or not next(iter(values)):
            raise StagingContractError(
                f"a staging write must carry exactly one non-empty {column}; got {sorted(values)}"
            )
        return next(iter(values))

    @staticmethod
    def _is_missing_table(message: str) -> bool:
        return "PGRST205" in message or "does not exist" in message or "Could not find the table" in message

    @staticmethod
    def _constraint_from(message: str) -> str:
        for constraint in CANDIDATE_UNIQUE_CONSTRAINTS:
            if constraint in message:
                return constraint
        return ""

    @staticmethod
    def _index_inserted(inserted: Any, expected_domains: Sequence[str]) -> dict[str, Any]:
        """Turn the insert response into domain -> id, refusing anything ambiguous."""
        if not isinstance(inserted, list) or len(inserted) != len(expected_domains):
            raise StagingContractError(
                f"candidate insert returned {len(inserted) if isinstance(inserted, list) else 'no'} row(s) "
                f"for {len(expected_domains)} candidate(s); stage 2 needs one id per row and will not guess"
            )
        id_by_domain: dict[str, Any] = {}
        for row in inserted:
            if not isinstance(row, Mapping) or not row.get("id") or not row.get("domain"):
                raise StagingContractError(f"candidate insert did not return id+domain for every row; got {row!r}")
            domain = str(row["domain"])
            if domain in id_by_domain:
                raise StagingContractError(f"candidate insert returned duplicate rows for domain {domain!r}")
            id_by_domain[domain] = row["id"]
        missing = sorted(set(expected_domains) - set(id_by_domain))
        if missing:
            raise StagingContractError(f"inserted rows did not come back for domain(s): {missing}")
        return id_by_domain

    def _recover_batch(self, batch_key: str, actor: str, expected_domains: Sequence[str]) -> dict[str, Any]:
        """Load the rows this batch + actor already owns and verify they are the whole batch.

        Scoped by ``batch_key`` *and* ``discovered_by_agent_identity`` so RLS shows
        only this agent's rows. Anything other than an exact match — a partial set,
        an extra domain, another actor's row, a non-pending status — fails closed:
        attaching evidence to rows we cannot account for is worse than failing.
        """
        found = self.client.get(
            self.TABLE,
            {
                "select": "id,domain,status",
                "batch_key": f"eq.{batch_key}",
                "discovered_by_agent_identity": f"eq.{actor}",
            },
        )
        rows = [row for row in (found or []) if isinstance(row, Mapping)]
        if not rows:
            raise StagingContractError(
                "the batch unique constraint fired but no rows are visible for this batch_key + actor. "
                "Either RLS is hiding another actor's rows or the write raced; refusing to attach evidence."
            )
        incomplete = [row for row in rows if not row.get("id") or not row.get("domain")]
        if incomplete:
            raise StagingContractError(f"recovered rows are missing id/domain: {incomplete!r}")

        domains = [str(row["domain"]) for row in rows]
        if sorted(domains) != sorted(str(d) for d in expected_domains):
            raise PartialRecoveryError(
                f"recovery found {len(domains)} row(s) {sorted(domains)} but the batch expects "
                f"{len(expected_domains)} {sorted(expected_domains)}; a partial batch is not a recovered batch",
                expected=expected_domains,
                found=domains,
            )
        not_pending = [row for row in rows if str(row.get("status") or "") != "pending_review"]
        if not_pending:
            raise PartialRecoveryError(
                "recovered rows are not pending_review, so the evidence insert policy would refuse them: "
                f"{[(row['domain'], row.get('status')) for row in not_pending]}",
                expected=expected_domains,
                found=domains,
            )
        return {str(row["domain"]): row["id"] for row in rows}

    def _write_evidence(self, id_by_domain: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> int:
        """Insert only the evidence that is not already stored for these candidates."""
        desired = self._evidence_payload(id_by_domain, evidence)
        if not desired:
            return 0
        reject_unknown_columns(desired, EVIDENCE_COLUMNS, self.EVIDENCE_TABLE)

        existing = self._existing_evidence(desired)
        missing = [row for row in desired if (str(row["candidate_id"]), str(row["url"])) not in existing]
        if not missing:
            return 0
        try:
            self.client.post(self.EVIDENCE_TABLE, missing)
        except Exception as exc:  # noqa: BLE001 - a concurrent writer may have won the race
            try:
                after = self._existing_evidence(desired)
            except Exception:  # noqa: BLE001 - completeness now unknown
                after = set()
            if all((str(row["candidate_id"]), str(row["url"])) in after for row in desired):
                return 0
            raise EvidencePersistenceError(len(id_by_domain), str(exc)) from exc
        return len(missing)

    def _existing_evidence(self, desired: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
        candidate_ids = sorted({str(row["candidate_id"]) for row in desired})
        try:
            found = self.client.get(
                self.EVIDENCE_TABLE,
                {"select": "candidate_id,url", "candidate_id": f"in.({','.join(candidate_ids)})"},
            )
        except Exception as exc:  # noqa: BLE001 - unreadable means unverifiable
            raise EvidencePersistenceError(
                len(candidate_ids),
                f"cannot read existing evidence, so completeness is unknown: {exc}",
            ) from exc
        return {
            (str(row.get("candidate_id")), str(row.get("url")))
            for row in (found or [])
            if isinstance(row, Mapping)
        }

    @staticmethod
    def _evidence_payload(
        id_by_domain: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach each evidence record to its verified candidate id, dropping the key.

        A record whose domain has no staged candidate is a contract failure, never
        something to discard, and a repeated (candidate, url) in one input is
        collapsed so the retry cannot double-insert it.
        """
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            domain = str(item.get("domain") or "")
            candidate_id = id_by_domain.get(domain)
            if candidate_id is None:
                raise StagingContractError(
                    f"evidence for domain {domain!r} has no staged candidate; "
                    "refusing to guess which company it belongs to"
                )
            url = str(item.get("url") or "")
            if not url:
                raise StagingContractError(f"evidence record has no url: {item!r}")
            key = (str(candidate_id), url)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "candidate_id": candidate_id,
                    "url": url,
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
