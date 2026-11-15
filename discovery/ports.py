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
    #: Candidates owned by another pending batch; each carries its domain and reason.
    conflicts: tuple[dict[str, Any], ...] = ()

    @property
    def total(self) -> int:
        return self.created + self.recovered


class CandidatePersistenceError(RuntimeError):
    """A candidate's persistence state could not be established.

    Raised when an insert fails for a reason the sink cannot classify — a missing
    table is :class:`StagingUnavailable`, a known unique violation is either a
    recovery or a reported conflict, anything else is unknown. The partial counts
    are carried so the caller can see what did land, but the run is a failure:
    reporting success here would claim rows we cannot verify.
    """

    def __init__(
        self,
        message: str,
        *,
        staged: Sequence[str] = (),
        recovered: Sequence[str] = (),
        conflicts: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.staged = tuple(staged)
        self.recovered = tuple(recovered)
        self.conflicts = tuple(conflicts)


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
        """Stage candidates one row at a time so one conflict cannot block the rest.

        A multi-row INSERT is atomic: a single ``uq_discovery_candidates_pending_domain``
        violation rejects every row in the batch, including candidates that have
        nothing to do with the conflict. Per-row inserts cost one request per
        candidate (the directive caps a batch at 50) and buy exact, per-candidate
        outcomes: staged, recovered, or conflicted.
        """
        if not candidates:
            return StagingResult()

        rows = [dict(c) for c in candidates]
        reject_unknown_columns(rows, CANDIDATE_COLUMNS, self.TABLE)

        staged_domains: list[str] = []
        recovered_domains: list[str] = []
        conflicts: list[dict[str, Any]] = []
        id_by_domain: dict[str, Any] = {}

        for row in rows:
            domain = str(row["domain"])
            batch_key = self._single([row], "batch_key")
            actor = self._single([row], "discovered_by_agent_identity")
            try:
                inserted = self.client.post(self.TABLE, [row])
            except Exception as exc:  # noqa: BLE001 - classify, never mask
                message = str(exc)
                if self._is_missing_table(message):
                    raise StagingUnavailable(
                        "discovery_candidates is not deployed. Apply "
                        "supabase/migrations/pending/*_discovery_candidates.sql first, or use "
                        "JsonlOutboxSink for a local exercise."
                    ) from exc
                constraint = self._constraint_from(message)
                if not constraint:
                    # an unknown failure means this row's state cannot be established
                    raise CandidatePersistenceError(
                        f"candidate {domain!r} could not be staged and its state is unknown: {message}",
                        staged=tuple(staged_domains),
                        recovered=tuple(recovered_domains),
                        conflicts=tuple(conflicts),
                    ) from exc

                # The constraint name is a hint, not the classification. The real indexes
                # are `UNIQUE (domain) WHERE status = 'pending_review'` (partial — this is
                # what an ordinary same-batch retry violates, because the row is still
                # pending) and `UNIQUE (batch_key, domain)` (which only fires once this
                # batch's row is no longer pending). So own the row by reading it, not by
                # reading the error: scoped by batch_key AND actor AND domain.
                owned = self._owned_candidate(batch_key, actor, domain)
                if owned is not None:
                    id_by_domain[domain] = owned["id"]
                    recovered_domains.append(domain)
                    continue
                if constraint == PENDING_DOMAIN_CONSTRAINT:
                    # a pending row exists for this domain but is not ours. The violation
                    # proves the collision; RLS means we cannot see whose it is. Report it
                    # and adopt nothing.
                    conflicts.append(
                        {
                            "domain": domain,
                            "name": row.get("name"),
                            "reason": "domain_staged_elsewhere",
                            "constraint": constraint,
                        }
                    )
                    continue
                # batch_key + domain is taken by something this actor cannot see: neither a
                # recoverable own row nor a pending-domain collision. Refuse to guess.
                raise StagingContractError(
                    f"{constraint} fired for {domain!r} but no row is visible for this "
                    "batch_key + actor + domain, so it is neither recoverable nor a foreign "
                    "pending conflict (that batch's row is probably no longer pending). "
                    "Refusing to attach evidence."
                ) from exc

            id_by_domain[domain] = self._index_inserted(inserted, [domain])[domain]
            staged_domains.append(domain)

        evidence_written = self._write_evidence(
            id_by_domain, evidence, frozenset(str(c["domain"]) for c in conflicts)
        )
        return StagingResult(
            created=len(staged_domains),
            recovered=len(recovered_domains),
            evidence=evidence_written,
            conflicts=tuple(conflicts),
        )

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
        """Turn a single-row insert response into domain -> id, refusing ambiguity."""
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

    def _owned_candidate(self, batch_key: str, actor: str, domain: str) -> Mapping[str, Any] | None:
        """Read the row this batch + actor owns for ``domain``, if any.

        Scoped by ``batch_key`` *and* ``discovered_by_agent_identity`` *and* ``domain``
        so RLS shows only this agent's rows and the answer is addressable per candidate.

        Returns ``None`` when nothing is visible — the caller decides whether that is a
        foreign pending-domain conflict or a contract failure, because only the caller
        knows which constraint fired. Anything ambiguous (two rows, missing id/domain, a
        different domain, a status the evidence policy would refuse) raises instead of
        being resolved by preference. A read that fails is never reported as "no row".
        """
        params = {
            "select": "id,domain,status",
            "batch_key": f"eq.{batch_key}",
            "discovered_by_agent_identity": f"eq.{actor}",
            "domain": f"eq.{domain}",
        }
        try:
            found = self.client.get(self.TABLE, params)
        except Exception as exc:  # noqa: BLE001 - unreadable ownership is not "no row"
            raise StagingContractError(
                f"the ownership read for {domain!r} failed after a unique violation, so whether "
                f"this batch owns the row is unknown: {exc!r}"
            ) from exc

        rows = [row for row in (found or []) if isinstance(row, Mapping)]
        if not rows:
            return None
        incomplete = [row for row in rows if not row.get("id") or not row.get("domain")]
        if incomplete:
            raise StagingContractError(f"ownership rows are missing id/domain: {incomplete!r}")
        domains = [str(row["domain"]) for row in rows]
        if domains != [domain]:
            raise PartialRecoveryError(
                f"ownership read for {domain!r} returned {len(domains)} row(s) {domains}; "
                "a candidate id must be unambiguous before evidence is attached",
                expected=[domain],
                found=domains,
            )
        if str(rows[0].get("status") or "") != "pending_review":
            raise PartialRecoveryError(
                f"owned row {domain!r} is status {rows[0].get('status')!r}, so the evidence insert "
                "policy would refuse it",
                expected=[domain],
                found=domains,
            )
        return rows[0]

    def _write_evidence(
        self,
        id_by_domain: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        skip_domains: frozenset[str] = frozenset(),
    ) -> int:
        """Insert only the evidence that is not already stored for these candidates.

        Evidence for a domain in ``skip_domains`` belongs to a candidate that was
        reported as a cross-batch conflict: it is deliberately not written, and not
        silently dropped either — the conflict is in the caller's result.
        """
        desired = self._evidence_payload(id_by_domain, evidence, skip_domains)
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
        id_by_domain: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
        skip_domains: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """Attach each evidence record to its verified candidate id, dropping the key.

        A record for a conflicted domain is skipped (the conflict is reported, not
        hidden); a record whose domain is neither staged nor conflicted is a
        contract failure, never something to discard. A repeated (candidate, url) in
        one input is collapsed so a retry cannot double-insert it.
        """
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            domain = str(item.get("domain") or "")
            if domain in skip_domains:
                continue
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
