"""Error hierarchy for the Eryndex platform.

Every external failure is normalized into one of these types before it reaches
business code (ADR-0003). Provider-specific exceptions never escape an adapter.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class EryndexError(Exception):
    """Base class for all platform errors."""


class ConfigurationError(EryndexError):
    """The platform is misconfigured (missing endpoint, malformed env)."""


class EnvironmentGuardError(ConfigurationError):
    """The runtime is pointed at an environment that is not allow-listed.

    Raised before any network call, so a misconfigured run cannot touch a
    non-Development project (mission §9).
    """


class SchemaViolation(EryndexError):
    """Data does not match a versioned contract."""


class GateViolation(EryndexError):
    """A human approval gate was attempted by an unauthorized actor (ADR-0005)."""


class ProviderError(EryndexError):
    """A normalized provider failure.

    ``code`` is a closed set so workflow code can branch on it without
    importing a vendor library:

    not_configured | unauthorized | timeout | unavailable | rate_limited |
    invalid_response | schema_violation | budget_exceeded
    """

    CODES = frozenset(
        {
            "not_configured",
            "unauthorized",
            "timeout",
            "unavailable",
            "rate_limited",
            "invalid_response",
            "schema_violation",
            "budget_exceeded",
        }
    )
    RETRYABLE = frozenset({"timeout", "unavailable", "rate_limited", "invalid_response"})

    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        provider: str = "",
        status_code: Optional[int] = None,
        retryable: Optional[bool] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if code not in self.CODES:
            raise ValueError(f"Unknown provider error code: {code!r}")
        self.code = code
        self.detail = detail
        self.provider = provider
        self.status_code = status_code
        self.retryable = self.RETRYABLE.__contains__(code) if retryable is None else retryable
        self.context = dict(context or {})
        super().__init__(f"[{provider or 'provider'}:{code}] {detail}".strip())

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "code": self.code,
            "detail": self.detail,
            "status_code": self.status_code,
            "retryable": self.retryable,
        }
