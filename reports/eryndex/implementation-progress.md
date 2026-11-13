# Eryndex Implementation Progress Board

**Updated:** 2026-11-13
**Branch:** `feat/eryndex-platform-transform`
**Statuses:** `NOT_STARTED → IN_PROGRESS → BLOCKED / IN_REVIEW → COMPLETED`
A work item is `COMPLETED` only with a verification artefact (test output, DB read-back, provider response id).

---

## Phase 0 — Baseline and safety

| ID | Item | Owner | Status | Evidence |
|---|---|---|---|---|
| W-0.1 | Full repository audit (code + 3k lines of docs) | Auditor | COMPLETED | `reports/eryndex/phase-0-baseline.md` §3, §7 |
| W-0.2 | Git baseline + isolated feature branch | Orchestrator | COMPLETED | branch `feat/eryndex-platform-transform` @ `c2571aa` |
| W-0.3 | Live Development schema/data inventory (read-only) | Orchestrator | COMPLETED | `reports/eryndex/evidence/phase-0-db-snapshot.json` |
| W-0.4 | Confirm Refokus history intact | Orchestrator | COMPLETED | 1 report / 13 findings / completed task, read back from DB |
| W-0.5 | Baseline test run + harness defect fix | QA | COMPLETED | 15/5 → **20/20 passing** (`pytest`) |
| W-0.6 | Data recovery point | Orchestrator | COMPLETED | `backups/dev-snapshot-20261113T194803Z.json`, sha256 `360b70ad…1e5c` |
| W-0.7 | Blockers recorded with required human action | Orchestrator | COMPLETED | baseline §10 (BLK-01…05) |

## Phase 1 — Architecture and contracts

| ID | Item | Owner | Status | Evidence |
|---|---|---|---|---|
| W-1.1 | Target architecture document | Orchestrator | COMPLETED | `docs/architecture/eryndex-target-architecture.md` |
| W-1.2 | ADRs 0001–0005 | Orchestrator | COMPLETED | `docs/architecture/architecture-decision-records/` |
| W-1.3 | Integration contracts v1.0 | Orchestrator | COMPLETED | `docs/architecture/integration-contracts.md` |
| W-1.4 | Reconcile `agent-contracts.md` / `agent-operating-model.md` with the new model | Orchestrator | IN_PROGRESS | partial: `docs/architecture/multi-agent-operating-model.md` |
| W-1.5 | Business lifecycle spec (9 stages, 4 gates) | Orchestrator | COMPLETED | `docs/eryndex-business-lifecycle.md` |
| W-1.6 | Provider contract + base adapter machinery (code) | Integration Eng. | IN_PROGRESS | `contracts/provider.py`, `integrations/base.py` + tests |
| W-1.7 | Extract Supabase auth/client out of `agents/researcher` (shims keep 20 tests green) | CRM Eng. | IN_PROGRESS | `integrations/supabase/` |

## Phase 2 — Integration infrastructure

| ID | Item | Owner | Status | Blocked by |
|---|---|---|---|---|
| W-2.1 | Crawl4AI adapter (crawl capability) | Integration Eng. | IN_PROGRESS | BLK-02 (docker), endpoint verification |
| W-2.2 | GPT Researcher adapter (research capability) | Integration Eng. | IN_PROGRESS | BLK-03 (LLM/search keys) |
| W-2.3 | OpenGTM adapter (discover capability) | Integration Eng. | IN_PROGRESS | endpoint/deployment unverified |
| W-2.4 | Provider health checks + failure normalization | Integration Eng. | COMPLETED (code) | verified by contract tests |
| W-2.5 | Live deployment of providers (compose) | Integration Eng. | BLOCKED | BLK-02 |
| W-2.6 | License & version notices for each provider | Integration Eng. | BLOCKED | verification evidence pending |

## Phase 3 — Discovery integration

| ID | Item | Owner | Status | Blocked by |
|---|---|---|---|---|
| W-3.1 | `discovery_candidates` migration (staging + provenance + status) | CRM Eng. | NOT_STARTED | — (authoring unblocked) |
| W-3.2 | Apply migration to Development | CRM Eng. | BLOCKED | BLK-01 (no DDL credentials) |
| W-3.3 | Discovery agent: normalize + dedupe + stage | Discovery Eng. | NOT_STARTED | W-3.2 for live path; file-outbox fallback designed |
| W-3.4 | Human Gate 1 ingestion path (operator CLI) | CRM Eng. | NOT_STARTED | W-3.1 |
| W-3.5 | Discovery identity + RLS (no INSERT on `companies`) | CRM Eng. | BLOCKED | BLK-04 |

## Phase 5 — Qualification intelligence

| ID | Item | Owner | Status | Blocked by |
|---|---|---|---|---|
| W-5.1 | IPP v2 engine (deterministic, citeable scoring) | Research & Intel Eng. | IN_PROGRESS | — |
| W-5.2 | Engine test fixtures + calibration set | Research & Intel Eng. | IN_PROGRESS | W-5.1 |
| W-5.3 | Scorecard → `lead_qualifications` persistence | CRM Eng. | NOT_STARTED | W-5.1, enum mapping verified from DDL |
| W-5.4 | Human Gate 2 review path | CRM Eng. | NOT_STARTED | W-5.3 |

## Phase 6 — Outreach and partnership operations

| ID | Item | Owner | Status | Blocked by |
|---|---|---|---|---|
| W-6.1 | Suppression check in the drafting path | Outreach Eng. | NOT_STARTED | — |
| W-6.2 | Draft generation from scorecard + evidence | Outreach Eng. | NOT_STARTED | W-5.1 |
| W-6.3 | G3 approval + G4 dispatch separation | Outreach Eng. | NOT_STARTED | ADR-0005 implemented |
| W-6.4 | Inbound conversation + partnership foundations | CRM Eng. | NOT_STARTED | delivery tables (BLK-01) |

## Phase 7 — Hermes runtime compatibility

| ID | Item | Owner | Status | Blocked by |
|---|---|---|---|---|
| W-7.1 | `TaskSource` / `Runtime` interfaces | Orchestrator | IN_PROGRESS | — |
| W-7.2 | `AntigravityRuntime` (manual trigger) preserved | Orchestrator | IN_PROGRESS | — |
| W-7.3 | `HermesRuntime` (delegated execution) | Orchestrator | NOT_STARTED | W-7.1 |
| W-7.4 | Scheduled execution design (cron) | Orchestrator | NOT_STARTED | W-7.3 |

## Phase 8 — Brand and portfolio

| ID | Item | Owner | Status | Evidence |
|---|---|---|---|---|
| W-8.1 | Portfolio reference inspection (design system + content) | Brand Eng. | IN_PROGRESS | `docs/brand/eryndex-portfolio-analysis.md` |
| W-8.2 | Claim-safety audit of existing copy | Brand Eng. | IN_PROGRESS | same document |
| W-8.3 | `eryndex.net` site structure + content plan | Brand Eng. | NOT_STARTED | — |
| W-8.4 | Deployable portfolio implementation | Brand Eng. | NOT_STARTED | — (no DNS/hosting changes without authorization) |
| W-8.5 | Brand alignment across repo artifacts | Orchestrator | NOT_STARTED | — |

## Phase 9–10 — Evaluation and readiness

| ID | Item | Owner | Status | Blocked by |
|---|---|---|---|---|
| W-9.1 | End-to-end evaluation in Development with synthetic + approved public fixtures | QA | NOT_STARTED | Phases 3/5/6 |
| W-9.2 | Metrics: precision, idempotency, RLS, latency, cost | QA | NOT_STARTED | W-9.1 |
| W-10.1 | Final architecture and operational readiness review | Orchestrator | NOT_STARTED | all above |

---

## Open blockers (owner: human operator)

| ID | Blocker | Unblocks |
|---|---|---|
| BLK-01 | No DDL credentials / PAT for `vyfqmrdznmocnmzyjjcj` | W-3.2, W-6.4, all new tables |
| BLK-02 | Docker daemon not running | W-2.5, live provider verification |
| BLK-03 | No LLM / search credentials | live research runs, W-2.2 |
| BLK-04 | No agent identities for Discovery/Qualification | W-3.5, live agent runs |
| BLK-05 | Portfolio profile details pending approval | any public publish |

## Rules for this board

- No item moves to `COMPLETED` because code was written.
- Every `IN_REVIEW` item names its artefact.
- Blocked items state what would unblock them; "waiting" is not a status.
