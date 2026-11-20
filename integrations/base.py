"""Shared adapter machinery: transport, timeouts, bounded retry, normalization.

Every adapter subclasses :class:`BaseProvider`. Nothing vendor-specific belongs
here; nothing vendor-specific is allowed to escape from here either (ADR-0003).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from contracts.errors import ProviderError
from contracts.provider import (
    Capability,
    HealthStatus,
    ProviderInfo,
    ProviderRequest,
    ProviderResult,
)

logger = logging.getLogger("eryndex.integrations")

# HTTP status -> normalized error code. 4xx that we caused are not retryable.
_STATUS_TO_CODE: dict[int, str] = {
    400: "invalid_response",
    401: "unauthorized",
    403: "unauthorized",
    404: "invalid_response",
    408: "timeout",
    409: "invalid_response",
    413: "budget_exceeded",
    422: "schema_violation",
    429: "rate_limited",
    # Crawl4AI uses 504 for its wall-clock budget (verified v0.9.5).
    504: "timeout",
}


class TransportTimeout(Exception):
    """The transport exceeded its deadline."""


class TransportUnavailable(Exception):
    """The transport could not reach the provider at all."""


class TransportResponse:
    __slots__ = ("status", "body", "headers")

    def __init__(self, status: int, body: str, headers: Optional[Mapping[str, str]] = None) -> None:
        self.status = status
        self.body = body
        self.headers = dict(headers or {})

    @property
    def json(self) -> Any:
        try:
            return json.loads(self.body) if self.body else None
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "invalid_response", f"Response is not valid JSON: {exc}", status_code=self.status
            ) from exc


class HttpTransport:
    """Test seam. Tests inject a fake; production uses RequestsTransport."""

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: float = 60.0,
    ) -> TransportResponse:  # pragma: no cover - interface
        raise NotImplementedError


class RequestsTransport(HttpTransport):
    """Default transport. Uses ``requests`` if installed, else ``urllib``."""

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: float = 60.0,
    ) -> TransportResponse:
        try:
            import requests  # noqa: PLC0415 - optional dependency at call site
        except ImportError:  # pragma: no cover - requests is in requirements.txt
            return self._urllib(method, url, json_body, headers, timeout)

        try:
            resp = requests.request(
                method,
                url,
                json=dict(json_body) if json_body is not None else None,
                headers=dict(headers or {}),
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise TransportTimeout(str(exc)) from exc
        except requests.RequestException as exc:
            raise TransportUnavailable(str(exc)) from exc
        return TransportResponse(resp.status_code, resp.text, dict(resp.headers))

    @staticmethod
    def _urllib(
        method: str,
        url: str,
        json_body: Optional[Mapping[str, Any]],
        headers: Optional[Mapping[str, str]],
        timeout: float,
    ) -> TransportResponse:
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        data = json.dumps(dict(json_body)).encode() if json_body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return TransportResponse(resp.status, resp.read().decode())
        except urllib.error.HTTPError as exc:
            return TransportResponse(exc.code, exc.read().decode())
        except TimeoutError as exc:
            raise TransportTimeout(str(exc)) from exc
        except OSError as exc:
            raise TransportUnavailable(str(exc)) from exc


@dataclass(frozen=True)
class ProviderConfig:
    """Explicit, environment-backed configuration. No secret defaults.

    ``token_provider`` covers providers that do not accept a static bearer
    token (OpenGTM uses an OAuth2 password grant). It is called per request and
    is expected to cache the token itself.
    """

    name: str
    base_url: str = ""
    api_token: str = ""
    timeout_s: float = 60.0
    max_retries: int = 2
    paths: Mapping[str, str] = field(default_factory=dict)
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    token_provider: Optional[Callable[[], str]] = None

    def path(self, name: str, default: str) -> str:
        return self.paths.get(name, default)

    def url(self, path: str) -> str:
        if not self.base_url:
            raise ProviderError(
                "not_configured",
                f"{self.name}: base_url is not configured",
                provider=self.name,
            )
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


class BaseProvider:
    """Template for adapters: validate -> call with bounded retry -> normalize."""

    name: str = "base"
    version: str = "0.0.0"
    capability: Capability = "crawl"
    health_path: str = "/health"
    endpoint_verified: bool = False

    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: Optional[HttpTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.transport = transport or RequestsTransport()
        self._sleep = sleep
        self.name = config.name or self.name
        self.metrics: dict[str, int] = {"calls": 0, "retries": 0, "failures": 0}

    # -- contract ---------------------------------------------------------
    def info(self) -> ProviderInfo:
        return ProviderInfo(name=self.name, version=self.version, endpoint=self.config.base_url)

    def headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.token_provider is not None:
            h["Authorization"] = f"Bearer {self.config.token_provider()}"
        elif self.config.api_token:
            h["Authorization"] = f"Bearer {self.config.api_token}"
        h.update(dict(self.config.extra_headers))
        return h

    def health(self) -> HealthStatus:
        """Never raises: an unreachable provider is unhealthy, not exceptional."""
        if not self.config.base_url:
            return HealthStatus(
                healthy=False, provider=self.name, detail="not_configured: base_url is empty"
            )
        started = time.monotonic()
        try:
            resp = self.transport.request(
                "GET",
                self.config.url(self.health_path),
                headers={"Accept": "application/json"},
                timeout=min(self.config.timeout_s, 10.0),
            )
        except (TransportTimeout, TransportUnavailable) as exc:
            return HealthStatus(
                healthy=False,
                provider=self.name,
                detail=f"unreachable: {type(exc).__name__}",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        latency = int((time.monotonic() - started) * 1000)
        ok = 200 <= resp.status < 300
        detail = "ok" if ok else f"health returned HTTP {resp.status}"
        if ok and not self.endpoint_verified:
            detail += " (endpoint shape unverified)"
        return HealthStatus(healthy=ok, provider=self.name, detail=detail, latency_ms=latency)

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.capability != self.capability:
            raise ProviderError(
                "schema_violation",
                f"{self.name} handles '{self.capability}', not '{request.capability}'",
                provider=self.name,
            )
        self.validate(request.payload)

        attempts = max(0, int(self.config.max_retries)) + 1
        last_error: Optional[ProviderError] = None
        invalid_retries = 0
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                self.metrics["calls"] += 1
                result = self._call(request, timeout=request.timeout_s)
                latency = int((time.monotonic() - started) * 1000)
                return ProviderResult(
                    status=result.status,
                    data=result.data,
                    provider=self.info(),
                    latency_ms=latency,
                    evidence=result.evidence,
                    warnings=result.warnings,
                    trace_id=request.trace_id,
                )
            except ProviderError as exc:
                last_error = exc
                self.metrics["failures"] += 1
                logger.warning(
                    "provider=%s code=%s attempt=%d/%d detail=%s",
                    self.name,
                    exc.code,
                    attempt + 1,
                    attempts,
                    exc.detail[:200],
                )
                # A malformed response is retried exactly once: it may be a
                # transient stream truncation, but a second malformed answer
                # means the contract does not match the deployment.
                if exc.code == "invalid_response":
                    invalid_retries += 1
                    if invalid_retries > 1:
                        raise
                if (not exc.retryable and exc.code != "invalid_response") or attempt == attempts - 1:
                    raise
                self.metrics["retries"] += 1
                self._sleep(self._backoff(attempt))
        raise last_error or ProviderError("unavailable", "exhausted retries", provider=self.name)

    # -- overridables -----------------------------------------------------
    def validate(self, payload: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def _call(self, request: ProviderRequest, *, timeout: float) -> ProviderResult:
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _backoff(attempt: int) -> float:
        """Deterministic, attempt-indexed backoff (no jitter, reproducible tests)."""
        return min(0.5 * (2**attempt), 8.0)

    def _post(self, path: str, body: Mapping[str, Any], timeout: float) -> TransportResponse:
        try:
            return self.transport.request(
                "POST",
                self.config.url(path),
                json_body=body,
                headers=self.headers(),
                timeout=timeout,
            )
        except TransportTimeout as exc:
            raise ProviderError("timeout", str(exc), provider=self.name) from exc
        except TransportUnavailable as exc:
            raise ProviderError("unavailable", str(exc), provider=self.name) from exc

    def _raise_for_status(self, resp: TransportResponse, body_preview: int = 300) -> None:
        if 200 <= resp.status < 300:
            return
        code = _STATUS_TO_CODE.get(resp.status)
        if code is None:
            code = "unavailable" if resp.status >= 500 else "invalid_response"
        raise ProviderError(
            code,
            f"HTTP {resp.status}: {(resp.body or '')[:body_preview]}",
            provider=self.name,
            status_code=resp.status,
        )

    @staticmethod
    def _as_items(payload: Any, keys: Sequence[str]) -> list[Mapping[str, Any]]:
        """Accept the common envelope shapes without guessing vendor detail."""
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, Mapping)]
        if isinstance(payload, Mapping):
            for key in keys:
                value = payload.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, Mapping)]
        return []
