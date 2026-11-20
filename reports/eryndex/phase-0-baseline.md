# Phase 0 — Repository Baseline and Safety

**Date:** 2026-11-13
**Branch:** `feat/eryndex-platform-transform` (created from `main` @ `72a1886`, 2026-11-08)
**Environment:** Supabase **Development** `vyfqmrdznmocnmzyjjcj` (verified at runtime)
**Production:** `vqnamszywsuqjxuqseas` — **never contacted** during Phase 0
**Status:** Baseline established. Two environment blockers recorded in §10.

---

## 1. What this phase guarantees

Phase 0 exists to make the rest of the transformation safe to perform. It answers, with evidence rather than assumption:

1. Which repository state we start from.
2. What the Development database actually contains today.
3. What is designed but not deployed.
4. What the test suite really does (including tests that used to pass for the wrong reason).
5. What recovery options exist if a later migration goes wrong.
6. What requires human authorization before Phase 2+ can be completed.

---

## 2. Git baseline

| Item | Value |
|---|---|
| Current branch | `feat/eryndex-platform-transform` (new, isolated) |
| Base commit | `72a1886` — *docs: refine IPP rubric evidence consistency* |
| Working tree at start | clean (no uncommitted or untracked changes) |
| Remote | `https://github.com/Puriatc10/development-partnership.git` |
| History depth | 15 commits, Phase 1 → Phase 3.1 |
| Default branch | `main` — not merged to, not force-pushed |

The transformation happens on the feature branch only. `main` remains the pre-transformation reference point and the rollback target.

---

## 3. Artifact inventory (measured)

| Area | Files | Lines | Notes |
|---|---:|---:|---|
| Agent code (`agents/researcher/*.py`) | 9 | 1,703 | stdlib + `requests` only; no `requirements.txt` existed |
| Business docs (`business/`, `delivery/`) | 5 | 530 | canonical business definitions |
| Agent contracts & protocols | 5 | 2,363 | contracts, operating model, workflows, task protocol, IPP v2 rubric |
| `AGENTS.md` | 1 | 143 | operational manual / behavioural boundaries |
| SQL (`crm/*.sql`, `supabase/migrations/*.sql`) | 4 | 6,306 | 1 deployed migration + 2 undeployed expansion designs |
| Phase reports (`reports/*.md`) | 14 | 3,088 | Phase 1 → Phase 3.1 history |

`crm/schema.sql` and `supabase/migrations/20261107174129_core_crm_schema.sql` are identical apart from the header block (16 diff lines, comments only). The migration is therefore a faithful record of the deployed core schema.

---

## 4. Live Development database state (read-only, verified)

Captured via the operator identity through PostgREST. Raw evidence: `reports/eryndex/evidence/phase-0-db-snapshot.json`.

| Table | Rows | Deployed |
|---|---:|---|
| `companies` | 1 | yes |
| `company_domains` | 1 | yes |
| `contacts` | 0 | yes |
| `tasks` | 1 | yes |
| `research_reports` | 1 | yes |
| `research_findings` | **13** | yes |
| `activities` | 1 | yes |
| `audit_logs` | 3 | yes |
| `lead_qualifications` | 0 | yes |
| `outreach_campaigns` | 0 | yes |
| `outreach_messages` | 0 | yes |
| `suppression_registry` | 1 | yes |
| `conversations` | 0 | yes |
| `conversation_messages` | 0 | yes |
| `partnerships` | — | **NO (404 PGRST205)** |
| `projects` | — | **NO** |
| `project_milestones` | — | **NO** |
| `project_change_requests` | — | **NO** |

Schema objects in the deployed migration: **14 tables, 22 enums, 44 RLS policies, 17 functions/triggers**.

### 4.1 Refokus historical data is intact

- Company `1afd5547-ce67-45d3-8aaa-35619514f835` (Refokus, `refokus.com`, `webflow_studio`, `identified`).
- Task `cf41afa8-5b26-4353-b645-bd5524751e07` — `company_research`, **completed**.
- Research report `050de7d6-ae17-458b-8e00-490b7c95d020` — version 1, `overall_confidence=high`.
- **13** `research_findings` rows visible to both the researcher and the operator identity.

Confirmed read-only from both identities. **Refokus research will not be re-run, duplicated, or edited** (mission constraint §3).

### 4.2 Identity / RLS baseline confirmed working

Sign-in through GoTrue succeeded for both scoped identities, and the claims are exactly as the contract requires:

| Identity | `user_role` | Agent claims |
|---|---|---|
| `researcher.v1@agents.internal` | `agent` | `subagent_type=researcher`, `agent_id=hermes-researcher-v1` |
| `operator@partnership.internal` | `operator` | — |

The anonymous key cannot read `companies` (`42501 permission denied for table companies`) and `/rest/v1/` with the anon key is rejected (`Only the service_role API key can be used for this endpoint`). Least privilege is enforced at the grant layer, not only by RLS.

### 4.3 Designed-but-not-deployed delivery layer

`crm/phase2_delivery_expansion.sql` (818 lines) and `crm/delivery-governance-expansion.sql` (1,309 lines) define `partnerships`, `projects`, `project_milestones`, `project_change_requests` — none exist in Development. This is the single largest designed-vs-deployed gap and directly blocks the "Manage Partnership" end of the lifecycle (Workflow 5).

---

## 5. Test baseline — and a defect found in the harness

**Before changes:** `15 passed, 5 failed`.

The five failures (`test_05`, `test_06`, `test_07`, `test_08`, `test_13` in `agents/researcher/test_runner_validation.py`) attempted a **real** Supabase sign-in (`401 Invalid API key`) even though each test declares `@patch("agents.researcher.runner.SupabaseAuthClient")`.

**Root cause:** `agents/` had no `__init__.py`, so pytest prepended `agents/researcher` to `sys.path` and imported the code a second time as `researcher.*`. The tests then patched `agents.researcher.runner.*` while the executing code lived in `researcher.runner.*` — two distinct module objects, so the patch had no effect. The tests that "passed" were the ones that raise before touching the network.

This matters beyond the fix: a suite that reports green while exercising live credentials is not evidence of anything. The fix makes the mock targets real.

**Fix applied:**
- `agents/__init__.py` — makes `agents` a regular package so each module is imported exactly once.
- `pytest.ini` — pins `testpaths` and file discovery.
- `requirements.txt` — the dependency set was implicit before.

**After changes:** `20 passed in 24.03s` (0 failures, 0 network calls in unit tests, no DB writes).

---

## 6. Implementation status matrix (Design / Implement / Execute / Test / Verify)

| Capability | Designed | Implemented | Executed | Tested | Verified |
|---|---|---|---|---|---|
| Core CRM schema + RLS + state machine triggers | yes | yes | yes (Dev) | partially (SQL review reports) | yes (live) |
| Agent identities & scoped auth | yes | yes | yes | yes | yes |
| Researcher vertical slice (Workflows 2) | yes | yes | yes (Refokus) | yes (20 tests) | yes |
| Task governance / transitions | yes | yes (DB) | yes | yes | yes |
| Operator CLI (register company, create task, inspect) | yes | yes | yes | **no** | partially |
| Discovery (Workflow 1) | yes | **no** | no | no | no |
| Discovery staging table | **no** | no | no | no | no |
| IPP v2 qualification engine | yes (rubric, 886 lines) | **no code** | no | no | no |
| Research providers (GPT Researcher / Crawl4AI) | design only | **no** | no | no | no |
| OpenGTM discovery adapter | design only | **no** | no | no | no |
| Outreach drafting & approval (Workflow 4) | yes | **no** | no | no | no |
| Inbound / partnership mgmt (Workflow 5) | yes | **no** (tables undeployed) | no | no | no |
| Hermes orchestration runtime | intended | **no** | no | no | no |
| Eryndex brand alignment | partially | **no** (project still unnamed internally) | no | n/a | no |
| Portfolio for `eryndex.net` | reference exists | **no** | no | n/a | no |

Read literally: **one workflow of five is implemented and verified.** Everything downstream of research is design-only. This is the honest starting point for the roadmap.

---

## 7. Contradictions and risks found in the baseline

| # | Finding | Impact |
|---|---|---|
| B-01 | `reports/phase-3.1` claims "all 14 unit and integration tests passing"; the suite is now 20 tests and 5 of them were false-green (see §5). | Historical reports overstate verification. All prior "verified" claims must be re-read with this caveat. |
| B-02 | No `requirements.txt`, no `pytest.ini`, no CI configuration. | The project was not reproducibly installable by anyone but its author. |
| B-03 | 4 delivery/partnership tables are fully specified in `crm/` but absent from the database, and no `supabase/migrations/` entry exists for them. | Workflow 5 cannot be implemented; the design would be lost on a fresh deploy. |
| B-04 | `SupabaseAuthClient`, `PostgrestClient` and the runner live inside `agents/researcher/`, i.e. generic infrastructure is coupled to one agent's package. | Blocks provider adapters and orchestration (Phase 2/7) until extracted. |
| B-05 | No idempotency key or unique constraint on research persistence; a retried runner would insert a second report + duplicate findings. | Direct violation of the mission's idempotency principle; must be fixed before any automated retry exists. |
| B-06 | Operator CLI accepts `--password` on the command line. | Credential leakage into shell history / process list. |
| B-07 | Discovery has no staging table, so the "no direct INSERT into `companies`" boundary has nowhere to write. | Human Gate 1 has no implementation surface yet. |
| B-08 | The project has no name for the platform layer (everything is `researcher`-centric); brand is "Eryndex" nowhere in the repo. | Phase 8 alignment work is real work, not a rename. |

---

## 8. Recovery and rollback strategy

Established before any change:

1. **Git:** isolated branch; `main` @ `72a1886` is the rollback target. No force-push, no merge to `main` without authorization.
2. **Schema:** reproducible from `supabase/migrations/20261107174129_core_crm_schema.sql`, verified byte-identical (modulo header comments) to `crm/schema.sql`.
3. **Data:** logical backup of all 14 deployed tables taken read-only through the operator identity **before** any mutation phase:
   - `backups/dev-snapshot-20261113T194803Z.json` — 22 rows total across 14 tables
   - sha256 `360b70ad234523379e4a7fd669688b127a13c88bc2faf9fad0a339f49b111e5c`
   - `backups/` is git-ignored: business data never enters Git history.
4. **Not available:** physical `pg_dump` backup — see blocker BLK-01.

---

## 9. What changed in this phase

| File | Change | Reason |
|---|---|---|
| `agents/__init__.py` | new | fixes the double-import that defeated `unittest.mock.patch` |
| `pytest.ini` | new | deterministic discovery and test paths |
| `requirements.txt` | new | reproducible environment (stdlib + requests, pytest) |
| `.gitignore` | updated | ignore `.venv/` and `backups/` (business data must not be committed) |
| `reports/eryndex/phase-0-baseline.md` | new | this document |
| `reports/eryndex/evidence/phase-0-db-snapshot.json` | new | read-only DB evidence (counts + column inventory, no secrets) |
| `backups/dev-snapshot-20261113T194803Z.json` + `.sha256` | new (git-ignored) | pre-transformation data recovery point |

No database writes. No schema changes. No changes to `agents/researcher/*` product code. Production untouched.

---

## 10. Blockers (require human action)

**BLK-01 — No DDL credentials for the Development project.**
The Development project is reachable through PostgREST (anon key + agent/operator JWTs), but there is **no database password and no personal access token for the organization that owns `vyfqmrdznmocnmzyjjcj`** (`supabase projects list` returns a different project, `AuraVibe`; `supabase/.temp/pooler-url` contains no usable password). Consequences:
- new tables (discovery staging, delivery/partnership layer) cannot be deployed;
- migrations can be authored and reviewed, but not applied or tested against the live prerequisite;
- no `pg_dump` recovery point.
**Needed:** either the Development database password, or a Supabase personal access token with access to that project, or explicit approval to apply migrations through the dashboard.

**BLK-02 — Docker daemon is not running.**
`docker --version` → 25.0.3, but `docker info` → `cannot find the file specified` (daemon down). Containerised providers (Crawl4AI, GPT Researcher, any OpenGTM service) cannot be deployed or health-checked locally until it is started.

**BLK-03 — No LLM / search-provider credentials.**
GPT Researcher cannot run without model and retriever credentials. None are present in `.env`. Adapters can be built and contract-tested with fakes; live verification is impossible without keys.

**BLK-04 — No agent identity for Discovery / Qualification.**
Creating Supabase Auth users with `app_metadata` (`user_role=agent`, `subagent_type=…`) requires admin rights. The Discovery and Qualification agents cannot be executed under RLS until those identities exist.

**BLK-05 — Portfolio profile details "pending approval".**
The reference portfolio marks a co-founder profile as pending and carries performance figures "based on information supplied for this profile". Publishing any of that on `eryndex.net` needs the operator's written confirmation.

None of these block Phase 1 (architecture and contracts) or the parts of Phases 2/5 that can be built against interfaces and fixtures. Work continues on those while the blockers are resolved.

---

## 11. Exit criteria for Phase 0

- [x] Repository inspected end to end; both the code and the documentation read.
- [x] Git baseline recorded; isolated feature branch created.
- [x] Live Development schema and data inventoried read-only, with evidence file.
- [x] Refokus history confirmed intact (1 report, 13 findings, completed task).
- [x] Baseline test results recorded and the false-green harness defect fixed (20/20 passing).
- [x] Recovery point created for data and schema.
- [x] Contradictions, risks and blockers recorded with required human actions.

**Phase 0 is complete.** Phase 1 (architecture and contract refactoring) starts from this document.
