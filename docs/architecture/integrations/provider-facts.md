# Integration facts: OpenGTM, GPT Researcher, Crawl4AI

Verified 2026-11-13 from primary sources only: GitHub REST API, raw files on `main`, PyPI JSON API,
Docker Hub v2 API, official docs (`crawl4ai.com/llms.txt`). Anything not directly observed is marked
**UNVERIFIED**.

---

## 1. OpenGTM

**Canonical repo:** https://github.com/debpalash/OpenGTM (109★, 36 forks, pushed 2026-11-13, homepage
https://opengtm.palash.dev). Four+ forks (`createdbyharsh`, `googio`, `MorMar7`, `Austecch`) point here.

> ⚠️ Name collision: `buildingopen/opengtm` is a **different** project (Python, MIT, 64★, last push
> 2026-04-09, Gemini-only, "Clay/Apollo/Semrush alternative"). Not the same codebase. If the parent
> meant that one, say so — the surfacing below is all `debpalash/OpenGTM`.

| Field | Fact |
|---|---|
| Latest release | **v3.0.0**, published **2026-09-24**. Only release and only semver tag on the repo. |
| Release vs main | README: dashboard workbench is "available in source builds from `main`"; "published release tags may contain an earlier UI". Main is ~7 weeks ahead of the tag. |
| License | **AGPL-3.0** (GNU Affero General Public License v3.0). Network-copyleft: modified versions served to users must offer source. Constrains how a fork of the adapter layer can be shipped. |
| Package | No PyPI package. Go binaries built locally. |

### Deployment
- Self-host only. **No hosted SaaS documented** (the `YUPCHA_CLOUD=1` env exists and changes LLM
  defaults, but no public cloud product/URL is documented → treat hosted as **UNVERIFIED/not offered**).
- `git clone` + `./scripts/install.sh` (or `install.ps1`) → `docker compose up`. Requires Git, Docker
  Compose v2, **~4 GB RAM**.
- Public entrypoint: **http://localhost:3000** (nginx → `api-go:8000`). `api-go` also binds
  `127.0.0.1:8001` for diagnostics only.
- **No prebuilt image.** Every image is `build:`-only: `opengtm-api-go:local`, `opengtm-worker-go:local`,
  `opengtm-scheduler-go:local`, `opengtm-mcp-go:local`, `opengtm-migrate-go:local`.
- Services: `postgres:16-alpine` (loopback :5432), `redis`, `migrate` (one-shot, goose), `api-go`,
  `worker-go`, `scheduler-go`, `browser` (:8090), `nginx`. Profiles: `mcp` (:3100 SSE), `demo` (seed-go),
  `reacher` (:8080), `bench` (providersim :8099).
- Runtime is Go. The Python backend was removed; `GO_*` feature flags are permanently on and an old
  `.env` setting them `false` is ignored and logged.

### HTTP API
Base `http://localhost:3000`. **376 route registrations** in `apps/backend-go/internal/api/*routes.go`.
Responses are deliberately FastAPI-shaped (the Go port emulates the old Python API, incl. 422 validation
envelopes).

**Auth** — OAuth2 password grant, form-encoded:
```
POST /auth/token      username, password, grant_type=password
  → 200 {"access_token": "...", "token_type": "bearer", "refresh_token": "..."}
POST /auth/refresh
GET  /auth/me
```
Bearer JWT in `Authorization`. `ACCESS_TOKEN_EXPIRE_MINUTES` default 30, refresh 14 days, signed with
`SECRET_KEY`. Throttles: 20/min per IP, 5/min + 30/hour per username → **429 with `Retry-After`**.
Errors: `{"detail": "..."}`; validation: FastAPI 422 field-error array.

**Provider-config surface (the interesting one for adapters):**
```
GET  /api/settings/providers                     → {"providers": [...], "default_provider": "..."}
PUT  /api/settings/providers/{provider_id}       body {"api_key","base_url","model","set_default"}
POST /api/settings/providers/{provider_id}/test  → connectivity probe
GET  /api/settings/models/{provider_id}          → model list (OpenRouter models URL)
GET/PUT /api/settings/enrichment-providers[/{provider_id}]
GET  /api/settings/system | /api/settings/llm-usage | /api/settings/integrations
```
`base_url` in the provider body means **OpenAI-compatible endpoints are pluggable at runtime**.
Writes require global admin; reads require any authenticated user.

**Data surface:**
```
GET/POST       /api/records/{kind}          kind = company | person
GET/PATCH      /api/records/{kind}/{id}
GET            /api/records/{kind}/{id}/history
POST           /api/records/company/find-people
POST           /api/records/person/enroll
POST           /api/record-imports(/preview|/inspect), GET /api/record-imports/{key}
POST           /api/record-snapshots(/preview|/restore), GET /api/record-snapshots
GET/POST/PUT/DELETE /api/workbooks... , POST /api/v2/workbooks/{id}/generate-column
POST           /api/lead/{lead_id}/enrich
POST           /api/collect
POST           /api/signals/scan, GET /api/signals(/counts|/analytics)
GET/POST       /api/copilotkit/*  (Agno/CopilotKit protocol)
POST/GET/DELETE /api/mcp/tokens[/{token_id}]
GET            /health, GET/POST /auth/setup
```

**MCP server** (better integration path than REST for agents): `docker compose --profile mcp up`,
command `--sse 3100`, token from `POST /api/mcp/tokens`, `Authorization: Bearer`; stdio and SSE
transports both implemented; per-token capability scopes + audit table. SSE binds loopback unless
`OPENGTM_MCP_SSE_ALLOW_NONLOOPBACK=true`.

### BYO LLM / search — yes, first-class
Env (all optional, pre-seeded then overridable at runtime via Settings):
`ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`, `OPENROUTER_API_KEY`/`OPENROUTER_MODEL`, `GOOGLE_AI_API_KEY`/
`GOOGLE_AI_MODEL`, `GROQ_API_KEY`/`GROQ_MODEL`, `CEREBRAS_API_KEY`/`CEREBRAS_MODEL`, `NVIDIA_API_KEY`/
`NVIDIA_MODEL`, `MISTRAL_API_KEY`/`MISTRAL_MODEL`, `GITHUB_MODELS_API_KEY`/`GITHUB_MODELS_MODEL`,
`LLM_DEFAULT_PROVIDER`, `YUPCHA_CLOUD`.

Search/data providers: `FIRECRAWL_API_KEY`, `SPICRAWL_API_KEY` (+`SPICRAWL_TIMEOUT`),
`SERPINGAPI_API_KEY`, `SERPLY_API_KEY`, `GOOGLE_API_KEY`+`GOOGLE_CSE_ID`, `DDG_SEARCH_TIMEOUT`
(keyless default), and `SEARCH_FALLBACK_ORDER=serpapi,bing,google_cse,brave,serpingapi,serply,firecrawl`.
Enrichment BYOK: `HUNTER_API_KEY`, `APOLLO_API_KEY`, `ABSTRACT_API_KEY`, `NUMVERIFY_API_KEY`,
`IPINFO_TOKEN`.

**Required env** (the stack refuses to start without them):
`SECRET_KEY` (≥32 bytes), `INTERNAL_SERVICE_TOKEN` (≥32 chars), `BROWSER_SERVICE_TOKEN` (≥32 chars),
`POSTGRES_PASSWORD`, `YUPCHA_RUNTIME_DB_PASSWORD`, `DATABASE_URL` (compose derives it).
Secrets at rest: `SECRETS_PROVIDER=local|vault_transit` + `SECRETS_MASTER_KEY` (Fernet / Vault Transit).

### CRM-write safety (matches your constraint)
Credential model already forbids direct DB coupling for CRMs: HubSpot/Salesforce/Instantly/Smartlead/
Sheets/Airtable credentials resolve **per workspace**; global/env fallback is opt-in via
`ALLOW_GLOBAL_CREDENTIAL_FALLBACK=true` and documented as single-tenant-only. Writes leave through
connector/destination records, not SQL. Your adapter should use `/api/records/*` or MCP — never the
`postgres` container.

### Limitations affecting automation
- AGPL-3.0 — copyleft across the network boundary.
- No prebuilt images → every deploy is a ~multi-minute source build; no pinned digest to reference.
- Released tag v3.0.0 lags `main` by weeks; `main` is the only way to get the current UI. Pin by commit.
- One-time `/setup` + `SETUP_TOKEN`; setup **closes permanently** after the first admin. Multi-replica
  first boot requires setting `SETUP_TOKEN` explicitly (generated token is per-process).
- Never expose :3000 directly — nginx is the only intended client; `TRUST_PROXY_CIDRS` and
  `LEAD_NET_SUBNET` default to a fixed `172.29.250.0/24`.
- Provider config is dual-sourced (env seed + Postgres override). Precedence between the two is
  **UNVERIFIED**; call `GET /api/settings/providers` to read effective values instead of assuming.
- CPU requirement: **UNVERIFIED** (README states RAM only: ~4 GB).

---

## 2. GPT Researcher (assafelovic/gpt-researcher)

**Canonical repo:** https://github.com/assafelovic/gpt-researcher — 30,672★, 4,220 forks, pushed
2026-11-12. Docs https://docs.gptr.dev, site https://gptr.dev.

| Field | Fact |
|---|---|
| Latest GitHub release | **v3.8.0**, published **2026-11-09** |
| Latest PyPI | **gpt-researcher 0.17.0**, uploaded 2026-11-09, `requires_python >=3.12` |
| Version skew | GH tag says 3.8.0, PyPI says 0.17.0 — same-day releases, different numbering. Pin both and record the commit. |
| License | **Apache-2.0** — `LICENSE` file and GitHub API say Apache-2.0, **but** `pyproject.toml` declares `license = {text = "MIT"}` and PyPI metadata + gptr.dev/pricing both say **MIT**. Inconsistent metadata; the LICENSE file is the operative text. Flag to legal before shipping. |
| Hosted SaaS | **None.** gptr.dev/pricing: "no SaaS subscription, no API gateway, and no rate limit on this site. You host the agent yourself." |

### Deployment
- **pip**: `pip install gpt-researcher` (Python ≥3.12).
- **Server**: FastAPI app `backend/server/app.py`, run via `backend/run_server.py` →
  `uvicorn server.app:app --host 0.0.0.0 --port 8000` (`reload=True`, so not prod-shaped out of the box).
- **Docker**: image **`gptresearcher/gpt-researcher`**, port **8000**. ⚠️ Docker Hub shows
  `last_updated: 2025-03-14`, tags only `latest` and `0.8.1` → the published image is ~20 months behind
  v3.8.0. `docker compose` also builds a `gptr-nextjs` frontend on 3000. Build from source for current code.
- **MCP server**: `mcp-server/` in repo (separate from the FastAPI app).

### HTTP API (verified in `backend/server/app.py`)
Base `http://localhost:8000`. **No authentication of any kind** — zero occurrences of `Depends`,
`HTTPBearer`, or `Authorization` in the app module.

```
POST /report/                        → generate report
GET  /report/{research_id}
GET  /api/reports                    POST /api/reports
GET  /api/reports/{research_id}      PUT  /api/reports/{research_id}
DELETE /api/reports/{research_id}
GET  /api/reports/{research_id}/chat POST /api/reports/{research_id}/chat
POST /api/chat                       → chat over a report body
GET  /files/                         POST /upload/        DELETE /files/{filename}
POST /api/multi_agents
GET  /                               GET /.well-known/agent-discovery.json
WS   /ws
```

`POST /report/` body (`ResearchRequest`):
```json
{"task": "...", "report_type": "research_report", "report_source": "web", "tone": "Objective",
 "headers": null, "repo_name": "", "branch_name": "", "generate_in_background": true}
```
⚠️ `repo_name` and `branch_name` are **required** (no defaults) — send empty strings. `task,
report_type, report_source, tone` are positional-required. `tone` is looked up as `Tone[...]`, so an
unknown value raises (KeyError → 500), it does not 422.

Response when `generate_in_background=false`:
```json
{"research_id": "...",
 "research_information": {"source_urls": [], "research_costs": 0.05,
                          "visited_urls": [], "research_images": []},
 "report": "# markdown...", "docx_path": "...", "pdf_path": "..."}
```
`generate_in_background=true` → `{"message": "...", "research_id": "..."}` immediately.
Errors: stock FastAPI (`422` validation, `500` traceback); no structured/typed error envelope.

`WS /ws` message types: `logs` (content ∈ `starting_research`, `planning_research`,
`running_subquery_research`, `research_step_finalized`, `agent_generated`, `scraping_urls`,
`mcp_optimization`, `image_planning`, `images_ready`), `report` (chunks), `report_complete`,
`path` (`pdf|docx|md`), `error`, `human_feedback`.

### BYO LLM — yes (env or JSON config)
`SMART_LLM`, `FAST_LLM`, `STRATEGIC_LLM`, `EMBEDDING`, `RETRIEVER`, `LLM_KWARGS`, `EMBEDDING_KWARGS`,
`SMART_TOKEN_LIMIT`/`FAST_TOKEN_LIMIT`/`STRATEGIC_TOKEN_LIMIT`, `REASONING_EFFORT`, `CONTEXT_FILTER`,
`SCRAPER`, `MAX_SCRAPER_WORKERS`, `MAX_SEARCH_RESULTS_PER_QUERY`, `MAX_ITERATIONS`, `TOTAL_WORDS`.
Model syntax is `provider:model` (defaults: `openai:gpt-5.4-mini` / `openai:gpt-6-sol` /
`openai:gpt-5.4`, `RETRIEVER=tavily`, `EMBEDDING=openai:text-embedding-3-small`).
Providers: `openai, anthropic, azure_openai, cohere, google_vertexai, google_genai, fireworks, ollama,
together, mistralai, huggingface, groq, bedrock, litellm, minimax, atlascloud, nebius`.
Any OpenAI-compatible endpoint: `OPENAI_BASE_URL` + `OPENAI_API_KEY` + `openai:<model>`.
Embeddings: `openai, azure_openai, cohere, google_vertexai, google_genai, fireworks, ollama, together,
mistralai, huggingface, nomic, voyageai, bedrock, nebius`.

### BYO search — yes, including arbitrary HTTP
`RETRIEVER=tavily` (default, `TAVILY_API_KEY`), `bing`, `brave` (+`BRAVE_API_KEY`), `groundroute`
(+`GROUNDROUTE_API_KEY`), `bocha` (+`BOCHA_API_KEY`), `google`, `searchapi`, `serpapi`, `serper`,
`searx`, `duckduckgo`, `arxiv`, `openalex` (+`OPENALEX_EMAIL`/`OPENALEX_API_KEY`), `semantic_scholar`,
`exa`, `crw`, `pubmed_central`, `xquik` (+`XQUIK_API_KEY`), `mcp`. Comma-separate for fallback chains.
**Custom retriever contract**: `RETRIEVER=custom`, `RETRIEVER_ENDPOINT=<url>`, extra args as
`RETRIEVER_ARG_*`; the endpoint must return `[{"url": "...", "raw_content": "..."}]`.

### Limitations affecting automation
- **No auth, no tenancy** on the FastAPI surface. Do not expose it; the adapter must front it.
- **Global env mutation**: `update_environment_variables()` writes straight into `os.environ`. Per-request
  config on the WS path mutates process state → concurrent requests with different providers/or keys
  contaminate each other. Only safe if one process = one config.
- `BackgroundTasks` is in-process: a restart loses in-flight reports; report store is local files
  (`report_store.py`), plus `outputs/`, `my-docs/`, `logs/` volume mounts.
- `reload=True` in the shipped runner; single-worker uvicorn.
- Long-output models truncate unless `SMART_TOKEN_LIMIT` etc. are raised (docs warn explicitly).
- RAM/CPU: **UNVERIFIED** officially. Third-party hosting guides cite 1 vCPU / 2 GB minimum — not an
  official figure, do not encode it as a requirement.

---

## 3. Crawl4AI (unclecode/crawl4ai)

**Canonical repo:** https://github.com/unclecode/crawl4ai — 87,315★, 9,107 forks, pushed 2026-11-13.
Library docs https://docs.crawl4ai.com. Cloud docs https://crawl4ai.com/docs/.

| Field | Fact |
|---|---|
| Latest release / PyPI | **v0.9.5**, published **2026-10-14**; PyPI `crawl4ai 0.9.5` same timestamp, `requires_python >=3.10` |
| Docker Hub | **`unclecode/crawl4ai`**, tags `latest`, `0.9`, `0.9.5`, `0.9.4`, `0.9.3`; 4,319,324 pulls; updated 2026-10-14 |
| License | **Apache-2.0** (`pyproject.toml` `license = "Apache-2.0"`; PyPI `license_expression: Apache-2.0`) |

### Deployment — three real options
1. **pip**: `pip install -U crawl4ai && crawl4ai-setup` (installs Playwright browser once).
2. **Docker**: `docker run -d -p 11235:11235 --name crawl4ai --shm-size=1g unclecode/crawl4ai:latest`
   → port **11235**, `--shm-size=1g` is required for Chromium. Prereqs: Docker ≥20.10, **≥4 GB RAM**
   for the container. Bundled Redis + supervisord inside the image.
3. **Hosted: Crawl4AI Cloud** at `https://api.crawl4ai.com` (operated by Context4AI Pte. Ltd.,
   Singapore; "official hosted API of the Crawl4AI project", *not affiliated with crawl4ai.org*).
   Free tier: 1,000 credits/month, 10 req/min, 2 concurrent; paid from $10/mo.

### Self-hosted API (port 11235, no path prefix; `deploy/docker/server.py`)
```
POST /crawl            POST /crawl/stream        POST /crawl/job      GET /crawl/job/{task_id}
POST /md               POST /html                POST /screenshot     POST /pdf
POST /execute_js       GET  /ask                 GET  /llm/{url:path} POST /llm/job
GET  /token            GET  /health              GET  /metrics        GET  /schema
GET  /artifacts/{artifact_id}                    GET  /hooks/info     POST /config/dump
GET  /monitor          /dashboard (UI)           /playground (UI)
```
**Auth** — `AuthGateMiddleware`, outermost ASGI layer, **fails closed** on every route/mount/WebSocket:
- `Authorization: Bearer <token>`; static operator token (`CRAWL4AI_API_TOKEN`, constant-time compare)
  grants `admin`; or an HS256 JWT minted by `POST /token` body `{"email": "...", "api_token": "..."}`
  (60-min expiry, `scope` = `data`|`admin`).
- Missing/bad credential → `401 {"detail": "Authentication required"}` + `WWW-Authenticate: Bearer`;
  WebSocket → close code **4401**. `/health` and `/token` are public.
- Auth is skipped only for loopback binds; **binding 0.0.0.0 without `CRAWL4AI_API_TOKEN` aborts startup**.
  `SECRET_KEY` must be ≥32 chars (weak/known values are rejected outright).

**Request** (`CrawlRequestWithHooks`):
```json
{"urls": ["https://a", "https://b"],        // 1..100, min_length=1, max_length=100
 "browser_config": {},                      // BrowserConfig dict
 "crawler_config": {},                      // CrawlerRunConfig dict
 "crawler_configs": null,                   // optional per-URL configs with url_matcher
 "hooks": {"hooks": [{"action": "...", "params": {}}], "timeout": 30}}
```
`crawler_config` keys are validated with `Provenance.UNTRUSTED`; unknown/untrusted keys →
`400 "Rejected config: ..."`.

**Response**:
```json
{"success": true, "results": [ ...per-URL CrawlResult... ],
 "server_processing_time_s": 1.2, "server_memory_delta_mb": 40.1, "server_peak_memory_mb": 512.0}
```
Per-URL failure is inside `results`: `{"success": false, "error_message": "..."}`. Whole-request
failure: `{"success": false, "error_message": "..."}`.

**Enforced limits** (`deploy/docker/config.yml`): `max_body_bytes 10 MiB` (→413), `max_pages 100`,
`max_depth 5`, `wall_clock_s 300` (**→504 on timeout**), job queue `maxsize 1000` (→503), 4 background
workers, `rate_limiting 1000/minute` (storage `memory://` by default; use `redis://` for multi-replica),
`crawler.memory_threshold_percent 95.0` (MemoryAdaptiveDispatcher throttles rather than OOMs).
`/crawl` has no built-in retry and no per-URL timeout knob beyond the global wall clock.

### BYO LLM — yes (server-wide, not per-request)
`.llm.env`: `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`,
`TOGETHER_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_TOKEN`; overrides `LLM_PROVIDER` (e.g.
`anthropic/claude-3-opus`), `LLM_TEMPERATURE`, `LLM_BASE_URL`, plus per-provider `OPENAI_BASE_URL`,
`GROQ_BASE_URL`, `OPENAI_TEMPERATURE`, `ANTHROPIC_TEMPERATURE`, `GROQ_TEMPERATURE`.
Default from `config.yml`: `llm.provider: openai/gpt-4o-mini`.
**A request may not supply its own LLM endpoint** — `base_url` was deliberately removed from
`MarkdownRequest` (credential-exfiltration vector); the endpoint is derived server-side from the
provider name. No BYO *search* provider in the self-hosted server (it crawls; it does not search).

### Hosted Cloud API (`https://api.crawl4ai.com`, verified from `crawl4ai.com/llms.txt`)
`Authorization: Bearer <key>` (or `x-api-key`). Endpoints:
```
POST /scrape            POST /scrape/batch      POST /scrape/jobs     GET /scrape/jobs/{id}
GET  /search            GET  /serp              GET  /answer          POST /extract
POST /price             GET  /verify            POST /recipes/{name}  POST /monitors
GET  /v1/prices         GET  /v1/billing/balance   POST /v1/estimate
/mcp  (streamable HTTP MCP at https://api.crawl4ai.com/mcp)
```
`POST /scrape` body `{url, format: both|md|html, proxy: standard|extra, country, parse, engine:
auto|browser-light|browser, bypass_cache}` → `{"ok":true,"markdown":"...","html":"...",
"content_hash":"...","ms":312}`.
Batch = NDJSON, ≤50 URLs, one line per URL plus a final `{"summary": {...}}`; jobs ≤10,000 URLs,
`GET /scrape/jobs/{id}/results?after=N` paged 500/page.
Errors: `{"error": "..."}` with meaningful codes — `402 no_credit|plan_cap|spend_cap|plan_needed`,
`429` + `X-RateLimit-*`, `403 blocked` / `403 login-wall`, `502 no-answer` / `502 origin-error:<n>`,
`503 busy` (retry shortly). Cost headers: `x-c4-cost`, `x-c4-balance`, `x-c4-allowance-left`.
Documented client timeout: **120 s** (browser pages 10–60 s, worst case 90 s).
⚠️ `POST /site` and `POST /scan` appear on the `/docs/` overview but not in `llms.txt`'s endpoint
detail, and `llms.txt` states the legacy v1 API (sessions, scan, site, enrich, context) **shut down
2026-11-06**. Treat `/site` and `/scan` as **UNVERIFIED / probably removed**.

### Limitations affecting automation
- Deep crawl: needs `CRAWL4AI_ALLOW_DEEP_CRAWL=true` and **exactly one URL per request** (400 otherwise);
  clamped by `max_pages`/`max_depth`.
- Hooks are off unless `CRAWL4AI_HOOKS_ENABLED=true` (documented RCE risk); inline Python hook `code` was
  removed in 0.9.0 and is accepted-but-never-executed; only enumerated `action` names from
  `GET /hooks/info` work, max 10 per request, 1–120 s each.
- `output_path` on `/screenshot` and `/pdf` is removed/ignored — results land in the artifact store,
  fetch via `GET /artifacts/{artifact_id}`. Files do not appear on your volume.
- Rate-limit storage defaults to `memory://` → limits are per-replica; jobs are Redis-backed.
- Config is untrusted-provenance validated: send exactly the documented `{"type": ..., "params": {...}}`
  shape. A typo'd key is a 400, not a silently ignored option.
- **README is stale**: it still says "latest stable release is 0.9.2" and shows `0.8.x` image tags, and
  its sample `config.yml` says `security.enabled: false` while the shipped file says `true`. Trust
  `config.yml`/`schemas.py`, not the README.
- Proxies: needs `CONNECT`-to-IP support; NTLM/Kerberos proxies unsupported (front with `cntlm`).
- RAM 4 GB is documented; **CPU count is UNVERIFIED**.

---

## Cross-cutting notes for the adapter layer

- **Timeouts differ by an order of magnitude**: OpenGTM research playbook step default 120 s
  (`PLAYBOOK_STEP_TIMEOUT_SECONDS`), GPT Researcher reports run minutes, Crawl4AI `/crawl` hard-caps at
  300 s (504). One shared timeout constant will be wrong for at least two of them.
- **Only Crawl4AI has a machine-readable error taxonomy** (status + `{"error": code}`). OpenGTM and
  GPT Researcher return prose `detail` / tracebacks — the adapter must synthesize structured errors,
  keying on HTTP status, not on message text.
- **Auth**: OpenGTM = OAuth2 password + Bearer JWT (refreshable); Crawl4AI = static token or HS256 JWT;
  GPT Researcher = none. Only OpenGTM has rate-limit `Retry-After`.
- **"BYO provider" is per-request for none of them**: OpenGTM via admin API (persisted, per-workspace),
  GPT Researcher via process env, Crawl4AI via env only. Any per-tenant key routing has to happen by
  running separate processes/containers.
- All three avoid direct CRM DB writes if used through their HTTP surfaces — pass this as a hard rule to
  the adapters (no `psycopg` against the `postgres` container, no `my-docs`/`outputs` file spelunking).

## Crawl4AI live verification (2026-11-14)

Result: **not verified** — `failure_class=docker`. Evidence `reports/eryndex/evidence/crawl4ai-live-verification.json`,
analysis `reports/eryndex/crawl4ai-blocker-analysis.md`. No container started; TCP 127.0.0.1:11235 refused;
`endpoint_verified` stays `False`. **Not proven:** `/health` 200, `POST /crawl` shape and field types, latency,
the `0.9.5` tag itself.

**Proven:** the daemon's network subsystem is wedged — `docker network create` (metadata only),
`docker network ls` and `docker network inspect bridge` all hang to timeout while container/image
reads answer; dockerd repeats `error creating temporary lease: context canceled` 6x today.
Disk was **not** a factor (21.18 GiB before, 20.92 GiB after, drift ≈250 MB).
**Retracted (re-measured by an independent audit):** an earlier version of this note claimed Docker
Hub returned 403. It does not — `registry-1.docker.io/v2/` returns the normal `401` + Bearer
challenge, `auth.docker.io/token` returns 200, `hub.docker.com` lists `0.9.5`, and the PyPI wheel
downloads. No mirror/proxy is required. Adapter fails closed: `TransportUnavailable` →
`ProviderError("unavailable", retryable=True)`, 0 retries.

**Adapter defects (latent — fix before trusting a green run):** `adapter.py:88-94` reads `markdown` as str, but
v0.9.5 `model_dump()` (`crawl4ai/models.py:281`) emits a nested dict → `Evidence.excerpt` becomes a dict repr and
`content_hash` hashes it; `adapter.py:70` sends uppercase `cache_mode` vs lowercase `CacheMode` values
(`cache_context.py:16-20`) through the untrusted gate (`deploy/docker/api.py:771`); `adapter.py:57-63` validates
`extract` then never sends it; `contracts/provider.py:41-70` has no provider-issued id field, so rows key on
`url + content_hash + retrieved_at`.

**Requirement / reproduction:** `timeout 45 docker network create c4a-probe` hangs (exit 124) while
`docker network ls` also hangs — that pair is the wedge. The registry is *not* blocked: expect `401` from
`registry-1.docker.io/v2/`, `200` from `auth.docker.io/token`. Needs ≥20 GiB free with a 5 GiB abort floor
(15 s watchdog), `timeout` on all docker calls, VM RAM ≥4 GiB (`memoryMiB=2048`), and the 15 GiB
`wsl/data/ext4.vhdx` compacted. Owner: restart Docker Desktop (bounces `trading_bot_postgres`). No mirror needed.
