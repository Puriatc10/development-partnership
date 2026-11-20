# ADR-0003 — One provider contract with normalized failures

**Status:** Accepted
**Date:** 2026-11-13
**Context refs:** target architecture §3, mission §5, `integration-contracts.md`

## Context

Three external tools are selected (OpenGTM for discovery, GPT Researcher for research, Crawl4AI for crawling). Each has its own HTTP surface, error model, and deployment shape — none of which is verified yet. A naive integration binds the workflow to each vendor's quirks; when a tool is replaced, the business code changes.

## Decision

Define one capability-oriented contract in `contracts/provider.py`:

- `ProviderRequest(capability, payload, idempotency_key, timeout_s, trace_id)` where `capability ∈ {discover, crawl, research}`.
- `ProviderResult(status, data, evidence[], provider_info, latency_ms, warnings[])` — `data` is normalized; provider-shaped JSON never leaves the adapter.
- `ProviderError(code, retryable, detail)` with a closed code set: `timeout`, `unavailable`, `unauthorized`, `rate_limited`, `invalid_response`, `schema_violation`, `budget_exceeded`, `not_configured`.
- `Provider` protocol: `health() -> HealthStatus`, `execute(request) -> ProviderResult`.
- Adapters are constructed from explicit configuration objects; a missing credential raises `not_configured` at construction, never a mid-run 401.

`integrations/base.py` provides the shared machinery all adapters use: timeout enforcement, bounded retry on `retryable` errors only, exponential backoff with jitter-free determinism (attempt-indexed), trace records, and metrics aggregation.

## Consequences

**Positive:** swapping GPT Researcher for another research provider touches one adapter; failure modes are testable without network; the workflow can assert on `status`/`code` rather than exceptions from three different libraries.

**Negative:** the normalized `data` shape must be designed per capability, which is real work and must be versioned (`contract_version`).

**Enforcement:** contract tests for each adapter run against recorded fixtures; a fake provider exercises the workflow. No adapter may import `agents` or write to Supabase tables.
