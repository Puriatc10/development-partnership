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


class SupabaseCandidateSink:
    """Staging sink for ``discovery_candidates`` (migration authored, not applied).

    Takes any object exposing ``post(table, rows)`` — the platform's
    ``PostgrestClient`` or a fake. Fails with an explicit, actionable error
    rather than silently dropping candidates when the table is missing.
    """

    TABLE = "discovery_candidates"

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
        try:
            rows = self.client.post(self.TABLE, list(candidates))
        except Exception as exc:  # noqa: BLE001 - normalize any client error
            message = str(exc)
            if "PGRST205" in message or "does not exist" in message or "Could not find the table" in message:
                raise StagingUnavailable(
                    "discovery_candidates is not deployed. Apply "
                    "supabase/migrations/pending/*_discovery_candidates.sql first, or use "
                    "JsonlOutboxSink for a local exercise."
                ) from exc
            raise
        return len(rows) if isinstance(rows, list) else len(candidates)


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
