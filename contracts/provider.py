"""Provider contract v1.0 (ADR-0003, docs/architecture/integration-contracts.md).

Workflow code asks for a capability ("crawl", "research", "discover"), never for
a vendor. Adapters translate a ProviderRequest into a vendor call and return a
normalized ProviderResult or raise a ProviderError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol, Sequence

Capability = Literal["discover", "crawl", "research"]

CAPABILITIES: frozenset[str] = frozenset({"discover", "crawl", "research"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class ProviderInfo:
    """Non-secret description of the provider that produced a result."""

    name: str
    version: str
    endpoint: str = ""
    contract_version: str = "1.0"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "endpoint": self.endpoint,
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True)
class Evidence:
    """One citable source. Findings may only be justified by Evidence."""

    url: str
    excerpt: str
    provider: str
    provider_version: str
    retrieved_at: str = field(default_factory=utc_now_iso)
    content_hash: str = ""
    title: str = ""

    MAX_EXCERPT = 600

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"Evidence.url must be http(s): {self.url!r}")
        if len(self.excerpt) > self.MAX_EXCERPT:
            object.__setattr__(self, "excerpt", self.excerpt[: self.MAX_EXCERPT])

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "excerpt": self.excerpt,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "retrieved_at": self.retrieved_at,
            "content_hash": self.content_hash,
            "title": self.title,
        }


@dataclass(frozen=True)
class ProviderRequest:
    capability: Capability
    payload: Mapping[str, Any]
    idempotency_key: str
    timeout_s: float = 60.0
    trace_id: str = ""
    contract_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.capability not in CAPABILITIES:
            raise ValueError(f"Unknown capability: {self.capability!r}")
        if not self.idempotency_key:
            raise ValueError("ProviderRequest.idempotency_key is required for safe retries")
        if self.timeout_s <= 0:
            raise ValueError("ProviderRequest.timeout_s must be > 0")


@dataclass(frozen=True)
class ProviderResult:
    status: Literal["ok", "partial", "error"]
    data: Mapping[str, Any]
    provider: ProviderInfo
    latency_ms: int = 0
    evidence: Sequence[Evidence] = ()
    warnings: Sequence[str] = ()
    trace_id: str = ""

    def __post_init__(self) -> None:
        if self.status not in ("ok", "partial", "error"):
            raise ValueError(f"Invalid provider status: {self.status!r}")
        if self.status == "partial" and not self.warnings:
            raise ValueError("A 'partial' result must explain what is missing in warnings")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "data": dict(self.data),
            "provider": self.provider.to_dict(),
            "latency_ms": self.latency_ms,
            "evidence": [e.to_dict() for e in self.evidence],
            "warnings": list(self.warnings),
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    provider: str
    detail: str
    checked_at: str = field(default_factory=utc_now_iso)
    latency_ms: int | None = None

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "provider": self.provider,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "latency_ms": self.latency_ms,
        }


class Provider(Protocol):
    """Minimal surface every adapter must expose."""

    name: str
    version: str
    capability: Capability

    def health(self) -> HealthStatus: ...

    def execute(self, request: ProviderRequest) -> ProviderResult: ...
