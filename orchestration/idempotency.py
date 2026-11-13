"""Content-addressed idempotency keys (ADR-0004).

The platform cannot yet add UNIQUE constraints to the CRM (BLK-01), so retries
are made safe in code: the same logical payload always produces the same key,
and a second execution compares against what is already persisted.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

# Keys that describe *how* a run happened rather than *what* it produced.
_VOLATILE_KEYS = frozenset(
    {"trace_id", "started_at", "finished_at", "latency_ms", "run_id", "idempotency_key"}
)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 safe."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_key(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_statement(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    scheme, _, rest = u.partition("://")
    host, _, path = rest.partition("/")
    return f"{scheme.lower()}://{host.lower()}/{path}".rstrip("/")


def research_idempotency_key(
    company_id: str,
    report_version: int,
    findings: Iterable[Mapping[str, Any] | Any],
) -> str:
    """Stable key for a research execution.

    Order-insensitive: findings are sorted, statements whitespace-normalized and
    URLs canonicalized, so a re-run with the same content hits the same key even
    if the model reorders its output.
    """
    normalized: list[dict[str, Any]] = []
    for f in findings:
        if isinstance(f, Mapping):
            get = f.get
        else:  # dataclass / object with attributes
            get = lambda name, _f=f: getattr(_f, name, "")  # noqa: E731
        normalized.append(
            {
                "type": str(get("finding_type") or ""),
                "category": str(get("category") or ""),
                "statement": _normalize_statement(str(get("statement") or "")),
                "url": _normalize_url(str(get("evidence_url") or "")),
            }
        )
    normalized.sort(key=lambda item: (item["category"], item["statement"], item["url"]))
    return content_key(
        {
            "company_id": str(company_id),
            "report_version": int(report_version),
            "findings": normalized,
        }
    )


def strip_volatile(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove run metadata so retries with the same intent match."""
    return {k: v for k, v in payload.items() if k not in _VOLATILE_KEYS}


def provider_request_key(capability: str, payload: Mapping[str, Any]) -> str:
    return content_key({"capability": capability, "payload": strip_volatile(payload)})


def describe_findings(findings: Sequence[Mapping[str, Any]]) -> str:
    """Short human-readable fingerprint for activity metadata."""
    return f"{len(findings)} findings; key-prefix={research_idempotency_key('', 1, findings)[:12]}"
