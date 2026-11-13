# Integration Contracts v1.0

**Status:** Active
**Date:** 2026-11-13
**Implements:** ADR-0003
**Code:** `contracts/provider.py`, `integrations/base.py`, `integrations/*/`

---

## 1. Why a contract instead of three integrations

The workflow must ask for *capabilities*, not vendors: "crawl this URL", "research this company", "discover agencies matching this directive". Three providers are configured today; any of them may be replaced without touching business code. That is only true if every adapter has the same shape.

```
workflow  →  ProviderRequest  →  Adapter  →  Provider (HTTP/SDK)
                                     ↓
                              ProviderResult / ProviderError   (normalized)
```

## 2. Types

### 2.1 Request

```python
@dataclass(frozen=True)
class ProviderRequest:
    capability: Capability          # "discover" | "crawl" | "research"
    payload: Mapping[str, Any]      # validated per capability (§3)
    idempotency_key: str            # sha256 of canonical payload (ADR-0004)
    timeout_s: float = 60.0
    trace_id: str = ""              # set by orchestration; never by the provider
    contract_version: str = "1.0"
```

### 2.2 Result

```python
@dataclass(frozen=True)
class ProviderResult:
    status: Literal["ok", "partial", "error"]
    data: Mapping[str, Any]         # normalized shape (§3), never raw vendor JSON
    evidence: tuple[Evidence, ...]  # what the data is grounded in
    provider: ProviderInfo          # name, version, endpoint (no secrets)
    latency_ms: int
    warnings: tuple[str, ...] = ()
    trace_id: str = ""
```

`status="partial"` means usable data plus explicit gaps — it must never be silently treated as `ok`. A partially-researched company is qualified with lower confidence, not with invented facts.

### 2.3 Evidence

```python
@dataclass(frozen=True)
class Evidence:
    url: str                    # canonical http(s) URL, no tracking params
    excerpt: str                # <= 600 chars, verbatim from the source
    retrieved_at: str           # ISO-8601 UTC
    provider: str               # "crawl4ai" | "gpt_researcher" | "opengtm"
    provider_version: str
    content_hash: str           # sha256 of normalized excerpt
```

Evidence is the unit that later justifies a score. The IPP engine may only cite `Evidence` it received; it cannot fetch.

### 2.4 Errors

| Code | Meaning | Retryable |
|---|---|---|
| `not_configured` | credential/endpoint missing at construction | no |
| `unauthorized` | provider rejected our credential (401/403) | no |
| `timeout` | deadline exceeded | yes |
| `unavailable` | connection refused / 5xx / daemon down | yes |
| `rate_limited` | provider asked us to slow down (429) | yes (with delay) |
| `invalid_response` | malformed body, missing required fields | yes (once) then no |
| `schema_violation` | valid JSON, wrong shape for the capability | no |
| `budget_exceeded` | per-run cost/step ceiling hit | no |

Adapters raise only `ProviderError`. A raw `requests` exception escaping an adapter is a defect.

### 2.5 Health

```python
@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    provider: str
    detail: str            # "ok" | "connection refused on 11235" | ...
    checked_at: str
    latency_ms: int | None
```

`health()` must not raise. An unreachable provider is `healthy=False`, never an exception.

## 3. Capability payloads (normalized)

### `crawl`
```json
{ "urls": ["https://example.com/about"], "max_pages": 5,
  "extract": ["text", "links", "metadata"], "respect_robots": true,
  "user_agent": "EryndexResearchBot/1.0 (+https://eryndex.net/bot)" }
```
→ `data: {"pages": [{"url","title","text","metadata","word_count","status"}]}`

### `research`
```json
{ "company": {"name","primary_domain","segment"},
  "questions": ["services","tech_stack","clients","team","growth","engineering_gaps"],
  "max_sources": 12, "evidence_required": true,
  "crawl_provider": "crawl4ai" }
```
→ `data: {"findings": [{"finding_type","category","statement","confidence","evidence_url","evidence_excerpt"}], "gaps": [...], "summary", "recommended_angle"}`

### `discover`
```json
{ "directive": {"segments": ["digital_agency","webflow_studio","framer_studio"],
                "sources": ["webflow_experts","framer_partners","clutch"],
                "region": "global", "limit": 25, "exclude_domains": [] },
  "normalize": true }
```
→ `data: {"candidates": [{"name","domain","source_url","source_name","discovered_at","signals":{}}]}`

## 4. Adapter obligations

1. Validate the request payload before any network call; raise `schema_violation`.
2. Never write to the CRM. Adapters return data; persistence belongs to a repository under the agent's identity.
3. Never log secrets, JWTs, or full provider responses containing them.
4. Bound every call with the request's `timeout_s`; enforce total budget (`max_sources`, `max_pages`).
5. Populate `ProviderInfo.version` from a constant checked against the deployment (documented in `reports/eryndex/integration-verification.md`).
6. Return `partial` with warnings rather than inflating completeness.

## 5. Configuration

Environment-only, prefixed per provider, no secret defaults:

| Variable | Used by | Required |
|---|---|---|
| `ERYNDEX_CRAWL4AI_URL` | Crawl4AI adapter | yes (for crawl) |
| `ERYNDEX_CRAWL4AI_API_TOKEN` | Crawl4AI adapter | if server enforces auth |
| `ERYNDEX_GPTR_*` (endpoint / model / retriever keys) | GPT Researcher adapter | yes (for research) |
| `ERYNDEX_OPENGTM_*` | OpenGTM adapter | yes (for discover) |
| `ERYNDEX_PROVIDER_TIMEOUT_S` | all | no (default 60) |
| `ERYNDEX_MAX_RETRIES` | all | no (default 2) |

Names are fixed by this contract so that deployment config is reviewable in one place. Exact provider-side requirements are verified in `reports/eryndex/integration-verification.md` — **not assumed**.

## 6. Replacement procedure

When a provider is replaced:

1. Implement the same capability in a new adapter directory.
2. Point configuration at it.
3. Run the contract test suite (`tests/test_provider_contract.py`) — unchanged, it asserts the interface, not the vendor.
4. Run one live crawl/research against an approved public URL in Development.
5. Record the decision in a new ADR and update §5.

Business code is not edited during a replacement. If it must be, the contract was wrong and that is a defect to fix, not a vendor to work around.

## 7. Verification status

| Adapter | Interface | Unit/contract tests | Live verification |
|---|---|---|---|
| Crawl4AI | designed | pending | **blocked** (BLK-02 docker, endpoint unknown) |
| GPT Researcher | designed | pending | **blocked** (BLK-03 credentials) |
| OpenGTM | designed | pending | **blocked** (endpoint/deployment unverified) |

No claim of working integration is made until the live column is filled with a timestamp and a response id.
