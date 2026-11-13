"""Versioned contracts shared by every Eryndex layer.

Nothing in this package may import ``agents``, ``orchestration`` or any vendor
SDK. It is the innermost ring of the architecture (ADR-0001).
"""

from .errors import (
    EryndexError,
    GateViolation,
    ProviderError,
    SchemaViolation,
)
from .provider import (
    Capability,
    Evidence,
    HealthStatus,
    ProviderInfo,
    ProviderRequest,
    ProviderResult,
)

__all__ = [
    "Capability",
    "EryndexError",
    "Evidence",
    "GateViolation",
    "HealthStatus",
    "ProviderError",
    "ProviderInfo",
    "ProviderRequest",
    "ProviderResult",
    "SchemaViolation",
]
