# Eryndex Implementation Progress Board

**Updated:** 2026-11-20
**Branch:** `main` @ `447dc947166abed168ba0821f0f9c3314dc4524d` (154 tracked files)
**Plan:** `docs/ERYNDEX_MASTER_EXECUTION_ROADMAP.md` defines scope, dependencies and done. This board reports status only.
**Statuses:** `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `IN_REVIEW`, `COMPLETED`, `DEFERRED`

### Evidence grades — a task is never "done" because code exists

| Grade | Means |
|---|---|
| `code` | written and imported by something |
| `unit` | deterministic tests pass |
| `contract` | adapter behaviour proven against a fake transport, never the vendor |
| `policy` | RLS predicates exercised with simulated claims inside a rolled-back transaction |
| `live` | executed against Development with a real JWT, and read back |
| `operational` | a human ran it in the normal course of work |

`COMPLETED` names its strongest grade. No item on this board currently reaches `operational`.

---

## Baseline — measured 2026-11-20

| Fact | Value | Evidence |
|---|---|---|
| Branch / HEAD | `main` @ `447dc94` | `git rev-parse HEAD` |
| Tracked files | 154 | `git ls-files \| wc -l` |
| Full suite | **402 collected / 396 passed / 6 skipped / 0 failed** | `pytest -q` |
| Skipped | all six in `tests/db/`, `ERYNDEX_DB_URL` unset | `tests/db/test_security_fixes_db.py:31-33` |
| Python LOC | 13,647 | `wc -l` over the eight packages |
| Migrations applied | `20261107174129`, `20261114194622`, `20261115212728` | remote `supabase_migrations.schema_migrations` |
| Deployed tables | 16 (14 core + 2 discovery), RLS on all | catalogue read-back, `directive-03-7` §4 |
| Production | untouched | no contact in any run |

---

## WS-A — Architecture and contracts

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| A1 | `TaskSource` / `Runtime` protocols in code | Orchestrator | NOT_STARTED | — | doc snippet only: `docs/architecture/eryndex-target-architecture.md:71-75`; zero `.py` hits |
| A2 | Boundary test covers `agents/` + `orchestration/` | QA | NOT_STARTED | — | `tests/test_architecture_boundaries.py:19-24` exempts both |
| A3 | Docs truth pass (lifecycle stages, `human_gates.py`) | Architecture | NOT_STARTED | — | `docs/eryndex-business-lifecycle.md:20-26` still says IPP and outreach are unimplemented; three docs cite a non-existent `orchestration/human_gates.py` |
| A4 | One schema truth — `crm/schema.sql` marked design-only | Architecture | NOT_STARTED | — | `crm/schema.sql:7` says `DESIGN ONLY` while `qualification_repository.py:54` cites it as the schema |

## WS-B — Database and CRM

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| B1 | Executable DB policy suite | QA | BLOCKED | — | 6 skips, no DSN — `OW-2` |
| B2 | H-06 composite FK column list | CRM | NOT_STARTED | — | `20261107174129:710,723-728`; proposal only, `OW-6` |
| B3 | Audit triggers for `companies` + discovery review | CRM | NOT_STARTED | — | triggers exist on 3 tables only (`:1664-1674`) |
| B4 | Missing constraints (`report_version`, one primary domain, task index) | CRM | NOT_STARTED | — | `:453-466`, `:389-397`, `:1708` |
| B5 | One delivery design file | CRM | NOT_STARTED | — | `crm/phase2_delivery_expansion.sql` vs `crm/delivery-governance-expansion.sql` |
| B6 | H-01…H-05 hardening | CRM | **COMPLETED — applied** | policy | present in `20261114194622:38-222`; execution proof awaits B1 |

## WS-C — Discovery

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| C1 | Production runner over `SupabaseCandidateSink` | Discovery | NOT_STARTED | — | nothing builds the sink outside tests; `scripts/discovery_cycle.py:88-96` uses the JSONL sink |
| C2 | `StagingUnavailable` names the applied migration | Discovery | NOT_STARTED | — | `discovery/ports.py:284-286` still points at `pending/` |
| C3 | Dedupe fed from real CRM data | Discovery | NOT_STARTED | — | `existing_companies` caller-supplied (`service.py:188,242`); an agent sees no companies (`20261107174129:1810-1835`) |
| C4 | Human Gate 1 review → company registration | Discovery/CRM | NOT_STARTED | — | no review code; `register_company` writes `companies` blind (`agents/researcher/operator_cli.py:46-74`) |
| C5 | Operator CLI for the Gate 1 loop | Operations | NOT_STARTED | — | 4 verbs, none of them review |
| C6 | Policy-level RLS probes with simulated claims | QA | NOT_STARTED | — | discovery policies never exercised |
| C7 | Staging write path (insert / recover / conflict / evidence) | Discovery | COMPLETED | contract | `discovery/ports.py:229-543`; 71 tests; `directive-03-7-1…` |
| C8 | Discovery migration applied + catalogue-verified | CRM | COMPLETED | live | `20261115212728…`; `directive-03-7…` §4 |

## WS-D — Research and providers

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| D1 | Crawl4AI live run | Integration | BLOCKED | — | `endpoint_verified=False` (`integrations/crawl4ai/adapter.py:165`) — `OW-4` |
| D2 | Research executor (company → findings → rows) | Research | NOT_STARTED | — | `agents/researcher/runner.py:135-144` receives research as arguments; no producer exists |
| D3 | GPT Researcher live path | Research | BLOCKED | — | no LLM/search credentials — `OW-5` |
| D4 | OpenGTM decision | Orchestrator | BLOCKED | — | AGPL-3.0, no documented discovery route — `OW-3` |
| D5 | Adapter contract compliance (3 adapters) | Integration | COMPLETED | contract | `test_provider_contract.py`, `test_crawl4ai_adapter_contract.py` |
| D6 | Any provider executed live | Integration | NOT_STARTED | — | `reports/eryndex/integration-verification.md:6` |

## WS-E — Evidence and intelligence

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| E1 | One canonical evidence model + converters | Intelligence | NOT_STARTED | — | five shapes: `contracts/provider.py:42-70`; `contracts/models.py:58-98` (zero importers); `intelligence/ipp/models.py:73-94`; `intelligence/bridge.py:15`; `discovery/ports.py:530-543` |
| E2 | Excerpts survive into scoring | Intelligence | NOT_STARTED | — | `bridge.py:49-59` drops `evidence_excerpt` |
| E3 | Real finding UUIDs instead of `evidence-{index}` | Intelligence | NOT_STARTED | — | `bridge.py:51`; rejected by `outreach/gate.py:864-873` |
| E4 | Enum reconciliation (`segment` 5 vs 7) | Intelligence | NOT_STARTED | — | `contracts/models.py:36-38` vs `intelligence/ipp/models.py:39-47`; `bridge.py:75` defaults to `digital_agency` |
| E5 | IPP v2 engine | Intelligence | COMPLETED | unit | `intelligence/ipp/`; 45 tests; 32/100 @ 0.714 on real Refokus data (`ipp-calibration.md`) |

## WS-F — Qualification and Gate 2

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| F1 | findings → `QualificationInput` loader | Qualification | NOT_STARTED | — | nothing selects `research_findings` for scoring |
| F2 | Live scorecard persistence | Qualification | NOT_STARTED | — | `qualification_repository.py:137` called only from tests with `StubCard` |
| F3 | Gate 2 approve/reject write path | Qualification | NOT_STARTED | — | `orchestration/workflow.py:52-59` models the gate; nothing writes `review_status` |
| F4 | Scorecard repository contract | Qualification | COMPLETED | contract | 21 tests; draft-only, evidence-per-dimension, human-review preconditions |

## WS-G — Outreach governance

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| G1 | Implement `QualificationReader` / `SuppressionReader` / `DraftWriter` | Outreach | NOT_STARTED | — | only fakes, in `tests/test_outreach_gate.py` |
| G2 | Draft generator grounded in approved evidence | Outreach | NOT_STARTED | — | no drafter module anywhere |
| G3 | Gate 3 / Gate 4 operator commands | Operations | NOT_STARTED | — | `assert_gate` has zero production call sites |
| G4 | Eligibility gate (content → qualification → suppression, fail-closed) | Outreach | COMPLETED | unit | `outreach/gate.py:625,903`; 134 tests; adversarial review closed 2 critical / 6 high |
| G5 | Dispatch unreachable | Outreach | COMPLETED | unit | `dispatch.py:22,33-75`; DB trigger `20261107174129:1062` |

## WS-H — Human operations

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| H1 | One operator CLI covering G1–G3 | Operations | NOT_STARTED | — | four verbs under an agent package: `agents/researcher/operator_cli.py:152-176` |
| H2 | Every refusal names the next action | Operations | NOT_STARTED | — | an uncaught `PostgrestError` is today's duplicate-domain behaviour |
| H3 | Operator identity can sign in | Operations | COMPLETED | live | `OPERATOR_EMAIL`/`OPERATOR_PASSWORD`; user `e1cd1ff2-…`, `user_role=operator` |

## WS-I — Orchestration and runtime

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| I1 | `Runtime`/`TaskSource` over the `tasks` table | Orchestrator | NOT_STARTED | — | claim/transition/complete live inside `agents/researcher/agent.py:35-55,57,190` |
| I2 | Hermes adapter with no business logic | Orchestrator | NOT_STARTED | — | no adapter exists |
| I3 | Retry / timeout / failure normalisation / escalation | Orchestrator | NOT_STARTED | — | no retry or lease code in `orchestration/` |
| I4 | Gate definitions + idempotency keys | Orchestrator | COMPLETED | unit | `orchestration/workflow.py:44-111`, `idempotency.py`; `tests/test_human_gates.py` |

## WS-J — Reliability and observability

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| J1 | Run/trace ids persisted per run | Platform | NOT_STARTED | — | `trace_id` never persisted (`contracts/provider.py:79`); `run_id` only stripped for idempotency (`orchestration/idempotency.py:26`) |
| J2 | Structured logging + shared redaction | Platform | NOT_STARTED | — | printf logs (`agents/researcher/runner.py:37`); discovery logs nothing; redaction only in `outreach/gate.py:283-378` |
| J3 | Machine-readable run summary | Platform | NOT_STARTED | — | none |

## WS-K — Security and access control

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| K1 | Tighten cross-agent reads + `company_domains` suppression filter | Security | NOT_STARTED | — | `20261107174129:1884-1893`, `:1955-1963`, `:1822` |
| K2 | Adversarial RLS suite | Security | NOT_STARTED | — | no runtime policy exercise at all |
| K3 | Service-role requirement out of `agents/` | Security | NOT_STARTED | — | `agents/researcher/provision.py:74,83` |
| K4 | anon denied at policy *and* grant layer | Security | COMPLETED | live | catalogue read-back, `directive-03-7` §4 |

## WS-L / M / N — Testing, readiness, brand

| ID | Item | Owner | Status | Grade | Evidence / blocker |
|---|---|---|---|---|---|
| L1 | Lifecycle end-to-end run | QA | NOT_STARTED | — | depends on C4, D2, F1, G2 |
| L2 | Metrics (precision, coverage, persistence, retries, latency) | QA | NOT_STARTED | — | all unknown |
| M1 | Environment isolation proof in every entry point | QA | NOT_STARTED | — | only `contracts/environment.py` guards; no test drives a Production ref |
| M2 | Production readiness assessment | Orchestrator | NOT_STARTED | — | after L1 |
| N1 | Portfolio claim consistency check | Brand | NOT_STARTED | — | analysis only (`docs/brand/eryndex-portfolio-analysis.md`) |
| N2 | Build/deploy config in repo | Brand | NOT_STARTED | — | none tracked |

---

## Open blockers — owner actions

| ID | Blocker | Unblocks |
|---|---|---|
| OW-1 | A Development Auth user with `app_metadata {"user_role":"agent","subagent_type":"discovery","agent_id":"hermes-discovery-v1"}` — dashboard/admin API only; no service-role key exists locally and the project signs with ES256/JWKS, so no JWT can be minted | live agent-path verification (C1-live, C6-live, K2) |
| OW-2 | A Development Postgres DSN for `tests/db/`, or a persistent role | B1, execution proof for B6 |
| OW-3 | OpenGTM: accept AGPL-3.0 or drop it | D4 |
| OW-4 | Crawl4AI reachable (Docker restart or hosted) | D1 |
| OW-5 | GPT Researcher credentials, or an explicit deferral | D2/D3 |
| OW-6 | Approval to apply a migration (H-06, K1, B3, B4) | those proposals |
| OW-7 | GPT Researcher licence conflict | M2 |

## Retired claims — corrected

| Previous board said | Reality |
|---|---|
| "Hardening not applied; W-S1…W-S5 AUTHORED, unapplied" | Applied 2026-11-14; B6 |
| "Discovery migration pending; W-3.2 BLOCKED" | Applied and catalogue-verified; C8 |
| "W-6.1/6.2/6.3 NOT_STARTED" | The eligibility gate is implemented and tested (G4); the wiring is what is missing (G1–G3) |
| "BLK-01 no DDL credentials" | DDL ran twice through the CLI; retired |
| "29 questions across 7 dimensions" | 29 questions across **6** — `business_size` is never asked (`intelligence/briefs.py:18-25`) |
| "W-5.5 research briefs NOT_STARTED" | `intelligence/briefs.py` exists, 29 anchor-targeted questions |
| "W-1.6 / W-1.7 / W-2.4 IN_PROGRESS" | Complete at contract grade (D5) |
| "W-2.6 licence notices COMPLETED" | No `LICENSE`/`NOTICE` file is tracked; only a table row in a report |

## Retained history

Phase 0 and Phase 1 artefacts remain valid and are not restated here: the baseline inventory
(`phase-0-baseline.md`), the recovery point (`backups/dev-snapshot-20261113T194803Z.json`),
architecture and ADR-0001…0006, the integration contracts, and the four directive-03.x execution
reports. Claims those documents make that later work superseded are corrected above rather than
deleted.

## Rules for this board

1. No item moves to `COMPLETED` because code was written; name the grade and the artefact.
2. `6 skipped` is never reported as `6 passed`.
3. A blocked item names the owner action; "waiting" is not a status.
4. When a document contradicts code, this board is corrected — the document is not defended.
