# Integration Verification — OpenGTM, GPT Researcher, Crawl4AI

**Date:** 2026-11-13
**Method:** GitHub REST API, raw `main` files, PyPI JSON, Docker Hub JSON, vendor `llms.txt`. Page scraping was not used (the extractor was rate-limited; API sources are stronger anyway).
**Full fact base:** `docs/architecture/integrations/provider-facts.md` (356 lines, with exact route lists).
**Bottom line:** all three surfaces are now documented from primary sources; **none has been executed against a live instance**. The adapter code has been corrected to the verified facts, but the "live verification" column stays empty on purpose.

---

## 1. Verified facts

| | Crawl4AI | GPT Researcher | OpenGTM |
|---|---|---|---|
| Canonical repo | `unclecode/crawl4ai` | `assafelovic/gpt-researcher` | `debpalash/OpenGTM` ⚠️ name collision |
| Version (date) | 0.9.5 (2026-10-14) | 3.8.0 GitHub / **0.17.0 PyPI** (2026-11-09) | 3.0.0 (2026-09-24) |
| License | **Apache-2.0** (clean) | **Apache-2.0 vs MIT — conflict** | **AGPL-3.0** (network copyleft) |
| Deploy | Docker `unclecode/crawl4ai`, port **11235**, `--shm-size=1g`, ≥4 GB RAM; pip; hosted cloud | pip (py≥3.12) / uvicorn `server.app:app` :8000; Docker Hub image **stale since 2025-03-14**; no SaaS | Docker Compose only, no prebuilt images; Go + Postgres 16 + Redis; UI/API :3000 (api-go :8000); ~4 GB RAM; optional MCP :3100 |
| Auth | Bearer static token or HS256 JWT; fail-closed middleware; refuses 0.0.0.0 without token | **none** (no `Depends`/Bearer anywhere) — internal network only | OAuth2 password grant `POST /auth/token` (form-encoded) → Bearer; refresh 30 min / 14 days |
| Core route | `POST /crawl` `{urls, browser_config, crawler_config}`; `/crawl/stream`, `/crawl/job`, `/md`, `/html`, `/screenshot`, `/pdf`, `/health`, `/metrics` | `POST /report/` requires `repo_name` + `branch_name` (no defaults) and `tone` from a closed enum; `/report/{id}`, `/api/chat`, `WS /ws` | 376 routes in `internal/api/*routes.go`; **no documented discovery route** |
| Error taxonomy | 413 body budget, 503 queue full, **504 wall-clock → timeout**, 429 1000/min, 400 unknown config key | bad `tone` → **500**, not 422 | 429 + `Retry-After` |
| BYO LLM | `.llm.env` server-wide only; per-request `base_url` removed deliberately | `.env`: `SMART_LLM`/`FAST_LLM`/`STRATEGIC_LLM`/`EMBEDDING` as `provider:model`; `OPENAI_BASE_URL`; `RETRIEVER=custom` + `RETRIEVER_ENDPOINT` | `PUT /api/settings/providers/{id}` `{api_key, base_url, model}` — runtime-pluggable, OpenAI-compatible `base_url` |

## 2. Decisions this forces (need a human)

| # | Decision | Why it matters |
|---|---|---|
| D-1 | **Which OpenGTM?** `debpalash/OpenGTM` (Go, AGPL, active) vs `buildingopen/opengtm` (MIT, Python, stale since 2026-04). | Different language, different licence, different API. Building against the wrong one is wasted work. |
| D-2 | **Is AGPL-3.0 acceptable?** | Network copyleft: if Eryndex modifies OpenGTM and exposes it as a service, the modified source must be offered. A business/legal call, not an engineering one. |
| D-3 | **Resolve the GPT Researcher licence conflict** (Apache-2.0 in `LICENSE`, MIT in `pyproject`/PyPI/site). | We redistribute nothing today (self-hosted, internal), but the notice we publish must be right. |
| D-4 | **GPT Researcher has zero authentication.** | It must never be reachable outside the internal Docker network. This is a deployment constraint, not a preference. |
| D-5 | **Discovery route unverified.** | No `discover` endpoint is documented in v3.0.0. The adapter now refuses to run without an explicitly configured path rather than guessing one. Selecting the route may mean writing a thin service that drives OpenGTM's agent API — a scope decision. |

## 3. Adapter alignment (code changed in this pass)

| Adapter | Change | Reason |
|---|---|---|
| `integrations/base.py` | 504 → `timeout` (retryable) | Crawl4AI's wall-clock budget arrives as 504, not 408; treating it as `unavailable` would hide a budget problem in retry noise |
| `integrations/base.py` | `ProviderConfig.token_provider` | OpenGTM needs an OAuth2 password grant, not a static token |
| `integrations/gpt_researcher` | sends `repo_name=""`, `branch_name=""`; `tone` validated against the enum; warns "no auth" | The documented route 500s without these fields |
| `integrations/opengtm` | **no default discovery path**; construction raises `not_configured` unless configured; version 3.0.0; AGPL/ambiguity documented | Guessing a route would fabricate a contract |
| `integrations/crawl4ai` | version 0.9.5; docstring states the verified surface and remaining gap | The code was already shaped correctly; the claim around it was not |

Test count after the change: **146 passing** (`python -m pytest -q`), including five new provider-specific tests (504 mapping, required research fields, tone rejection, fail-closed discovery route, token provider).

## 4. What "verified" still does not mean

| Provider | Interface | Contract tests | Live call |
|---|---|---|---|
| Crawl4AI | yes | yes (fake transport) | **no** — Docker daemon down (BLK-02) |
| GPT Researcher | yes | yes (fake transport) | **no** — no LLM/search credentials (BLK-03) |
| OpenGTM | partial | yes (fake transport) | **no** — no deployment, project identity unresolved |

To convert any row to "yes" we need, in order: a running Docker daemon, credentials, and one approved public URL to crawl / one company to research. The moment that exists, `endpoint_verified` flips to `True` and this file records the response id and timestamp.

## 5. Resource envelope (for the deployment step)

- Crawl4AI: ≥4 GB RAM, `--shm-size=1g`, port 11235 — the lightest of the three.
- GPT Researcher: unverified RAM/CPU officially (third-party guides say 1 vCPU / 2 GB — not treated as fact); needs fast + smart + embedding models, so cost is dominated by tokens, not containers.
- OpenGTM: ~4 GB RAM, Postgres 16, Redis, five locally-built images. This is the heaviest component and the one with the licensing question — worth deciding before spending a day on compose files.

## 6. Next actions

1. Human answers D-1…D-5 (especially D-1, which gates everything discovery-related).
2. Start Docker; bring up Crawl4AI alone and record a live `/health` + one `/crawl` response.
3. With keys, bring up GPT Researcher and record one `/report/` response for a public company.
4. Only then wire a provider into the research workflow, and only with `endpoint_verified=True`.
