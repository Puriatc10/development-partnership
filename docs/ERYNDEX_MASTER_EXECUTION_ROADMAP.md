# Eryndex Master Execution Roadmap

**Status:** authoritative engineering execution plan
**Baseline:** `main` @ `447dc947166abed168ba0821f0f9c3314dc4524d` (154 tracked files)
**Supersedes:** `reports/eryndex/implementation-progress.md` as the *plan*; the board remains the *status* record.
**Currency rule:** a capability is listed as working only where an artefact proves it ran. Everything else is named by its actual state — `code-complete`, `contract-tested`, `policy-verified`, `live-verified`, `blocked`.

---

## 1. Mission and scope

Eryndex is a wholesale engineering / managed-development partner business. The platform exists to run one loop, with a human at every gate:

```
Discover → Register → Research → Qualify → Strategize → Prepare Outreach → Approve → Dispatch (disabled) → Engage → Manage Partnership
```

This roadmap covers the **technical platform only**. Commercial prospecting, partner conversations and business development are the owner's parallel work and are out of scope.

Non-negotiable invariants (do not reopen without an ADR):

1. Supabase PostgreSQL is the single canonical system of record. No second CRM.
2. Business logic is orchestration-runtime-independent; Hermes is one runtime, not the architecture.
3. Human approval is mandatory at G1–G4, enforced by verified JWT claims, not by a code path.
4. Agents never hold `service_role`; no agent path bypasses RLS.
5. No autonomous external communication. Dispatch stays unreachable in code and in the database.
6. IPP v2 weights, anchors, thresholds and classifications are frozen.
7. Refokus historical research is immutable.
8. Development (`vyfqmrdznmocnmzyjjcj`) and Production (`vqnamszywsuqjxuqseas`) are isolated; Production is out of scope for this mission.

---

## 2. Verified baseline

**Post-migration note (2026-11-23).** The platform no longer uses Supabase: PostgreSQL 17 runs in
Docker Compose with our own roles, `auth` schema and migration ledger (ADR-0007,
`reports/eryndex/migration-to-postgres-execution.md`). Rows below that cite a Supabase project
describe the state the mission started from; the security model they describe is unchanged in shape
and is now executable against our own database.

| Capability | State | Evidence |
|---|---|---|
| Schema deployed (16 tables, 3 migrations) | **verified in Development** | `supabase/migrations/20261107174129_core_crm_schema.sql`, `20261114194622_hardening_batch1.sql`, `20261115212728_discovery_candidates.sql`; remote history holds all three; `directive-03-7-discovery-migration-application.md` |
| RLS on every table, anon denied at policy *and* grant layer | **verified by catalogue read-back** | migration files; `directive-03-7` §4; grants at `20261107174129:2069-2085` |
| Hardening H-01…H-05 | **applied** (fix present in the deployed migration); **not verified by execution** | `20261114194622:38-222`; the six tests that would prove it are skipped (`tests/db/test_security_fixes_db.py:31-33`) |
| IPP v2 engine | **code-complete + unit-tested on real data** | `intelligence/ipp/` (45 tests); calibration 32/100 at 0.714 coverage, `ipp-calibration.md` |
| Discovery staging write path | **code-complete, contract-tested only** | `discovery/ports.py:229-543`; 71 discovery tests; never executed against PostgREST |
| Human Gate 1 (candidate → company) | **absent at mission start; now implemented and live** | mission start: no review code, `register_company` wrote `companies` blindly. Now: `operations/` + `reports/eryndex/stage-2-discovery-gate1.md` §3 |
| Research execution | **absent** | `run_researcher_vertical_slice` receives the research as arguments (`agents/researcher/runner.py:135-144`); nothing produces it |
| Provider adapters (Crawl4AI, GPT Researcher, OpenGTM) | **contract-tested, zero live runs** | `integration-verification.md:6`; adapters imported only by tests |
| Qualification persistence | **code-complete; still no production caller** | `integrations/supabase/qualification_repository.py:137`; the read side now works live (`qualification/loader.py`), the write side needs a qualifier identity (OW-1) |
| Outreach drafting gate | **code-complete, 134 tests, no production wiring** | `outreach/gate.py:625,903`; no `DraftWriter`/`SuppressionReader` implementation outside tests |
| Dispatch | **unreachable by construction** | `outreach/dispatch.py:22,33-75`; DB trigger `20261107174129:1062` |
| Runtime / TaskSource abstraction | **documentation only** | snippet at `docs/architecture/eryndex-target-architecture.md:71-75`; zero `.py` hits |
| Observability | **absent** | no run/trace id persisted; `audit_logs` has no correlation columns (`20261107174129:782-793`) |
| Test suite | **448 collected / 442 passed / 6 skipped / 0 failed** (402 / 396 at mission start) | `pytest -q`; skips all from the `ERYNDEX_DB_URL` guard |

Two facts that decide the execution order:

- **Nothing in the platform calls anything else.** Four entry points exist and none of them reach `intelligence`, `outreach`, `discovery`'s Supabase sink, or the qualification repository. The platform is a set of correct modules with no spine.
- **Gate 1 is load-bearing, not bookkeeping.** A candidate that never leaves `pending_review` permanently shadows its domain through the partial unique index (`20261115212728:64-65`), so every later discovery of that domain reports `domain_staged_elsewhere`. Without Gate 1, Discovery poisons itself.

---

## 3. Target architecture

Five layers, dependencies pointing inward only (`contracts` imports nothing of ours):

```
contracts/        domain types, provider capability contract, environment guard   (604 LOC)
    ↑
integrations/     PostgREST client, auth, provider adapters, repositories        (1,442)
intelligence/     IPP engine, rubric, evidence bridge, research briefs           (2,191)
discovery/ outreach/   pipeline stages, each a port + service                    (2,696)
orchestration/    gates, idempotency, runtime contracts, task source             (238 today)
operations/       operator surfaces (CLI) — the only human entry point
```

Rules the boundary test enforces today (`tests/test_architecture_boundaries.py:19-24`) and must continue to: `contracts` may not import `integrations`/`intelligence`; `intelligence` may not import `agents`/`orchestration`. **Gap:** `agents/` and `orchestration/` are outside the rule, so an agent can import anything without a test failing.

---

## 4. Workstreams

Each workstream has an owner role, a current state, the genuine gap, its work items, and a definition of done. IDs are stable and are what the Progress Board references.

### WS-A — Architecture and contracts

**State:** architecture documented and mostly true; the runtime layer it promises does not exist in code.
**Gap:** `TaskSource`/`Runtime` are prose (`eryndex-target-architecture.md:71-75`); `orchestration/human_gates.py` is cited by three documents and has never existed; boundary tests exempt `agents/` and `orchestration/`.

| ID | Item | DoD |
|---|---|---|
| A1 | `orchestration/contracts.py` with `TaskSource` and `Runtime` protocols, plus a shared `TaskClaim`/`TaskOutcome` model | importable; boundary test proves no business logic inside |
| A2 | Boundary test extended to `agents/` and `orchestration/` | a deliberate violating import fails the suite |
| A3 | Documentation truth pass: remove/redirect every reference to `orchestration/human_gates.py`; reconcile lifecycle doc stages 4 and 7 with `intelligence/ipp` and `outreach/gate.py` | `docs/eryndex-business-lifecycle.md` matches code |
| A4 | One canonical schema story: mark `crm/schema.sql` design-only at the top and name the deployed migrations as authority | no doc cites the design file as deployed truth |

### WS-B — Database and CRM

**State:** 16 tables deployed with RLS and grants; five hardening fixes applied but unproven by execution.
**Gap:** no DSN so the six policy tests never run; audit coverage is three tables; H-06 FK defect open; no `UNIQUE (company_id, report_version)`; no partial unique for one primary domain per company; `company_domains` agent read omits the suppression filter.

| ID | Item | DoD |
|---|---|---|
| B1 | Make the DB suite runnable: a documented, environment-guarded way to supply a Development DSN (owner-provided or CLI-minted) | 6 tests execute; zero skips in `tests/db/` |
| B2 | H-06 composite FK `ON DELETE SET NULL` column list — proposal only | migration proposal + verification plan; **no application without approval** |
| B3 | Audit coverage for Gate 1 and company creation (`discovery_candidates` UPDATE, `companies` INSERT) | proposal; applied only after approval |
| B4 | Missing constraints/indexes: `research_reports(company_id, report_version)` unique; one primary domain per company; `tasks` RLS-shaped index | proposal with measured rationale |
| B5 | Resolve the two delivery expansion SQL files to one (defer the tables, keep one design) | `crm/` contains one delivery design; the other removed or archived |

### WS-C — Discovery

**State:** the staging write path is the most rigorously built part of the platform — per-candidate insert, ownership-scoped recovery, conflict isolation, idempotent evidence — and it has never run against a real PostgREST.
**Gap:** no production runner; no Gate 1; no CLI; `StagingUnavailable` points at a pending migration that no longer exists; agent dedupe input is caller-supplied and unreadable by an agent.

| ID | Item | DoD |
|---|---|---|
| C1 | `scripts/discovery_run.py`: load config → sign in as the discovery identity → build `PostgrestClient` → `SupabaseCandidateSink` → `run_discovery` | one real batch staged in Development with returned ids |
| C2 | Fix `StagingUnavailable` text to name the applied migration | message matches reality |
| C3 | Dedupe source: operator-supplied `existing_companies` loaded by the caller, or a `SECURITY DEFINER` lookup — pick one and document why | dedupe is fed by real CRM data in the live run |
| C4 | Gate 1: candidate review (`approve`/`reject`) with reviewer attribution, then company + primary domain creation, idempotent, duplicate-checked, candidate status + `converted_company_id` written in the same flow | one candidate travels `pending_review → approved → converted` with a real company id |
| C5 | Operator CLI surface: list candidates, show evidence, approve, reject, register | a human can complete the whole Gate 1 loop from the shell |
| C6 | Policy-level RLS verification with simulated claims in rolled-back transactions (agent insert own, cross-agent denial, operator review, anon denial) | probes recorded in the report; explicitly **not** called live JWT verification |

### WS-D — Research and provider infrastructure

**State:** three adapters, contract-tested against fake transports, none ever executed. No research execution exists.
**Gap:** no caller of `GPTResearcherProvider.execute()`; adapter refuses to emit findings, and nothing converts prose to `Finding`s; runner is hard-pinned to the dev project; Crawl4AI still `endpoint_verified=False`; OpenGTM is AGPL and unconstructible without a human-supplied discovery route.

| ID | Item | DoD |
|---|---|---|
| D1 | Crawl4AI live run (Docker or hosted) with a recorded request id | `endpoint_verified=True` with a stored response |
| D2 | Research executor: company → provider → normalised findings → `research_reports` + `research_findings` + task completion | one live or fixture-backed research run persisted end-to-end |
| D3 | GPT Researcher credential decision (LLM/search keys are an owner action) | either live run or an explicit, recorded deferral |
| D4 | OpenGTM decision: AGPL acceptance or replace with an owned discovery source | ADR; adapter either wired or removed from the default path |

### WS-E — Evidence and intelligence

**State:** five incompatible evidence shapes; no converter between them; excerpts are dropped between research and scoring.
**Gap:** `contracts/provider.py` `Evidence`, `contracts/models.py` `Finding` (dead code, zero importers), `intelligence.ipp.models.Finding`, the `bridge.py` dict contract, and `discovery/ports.py` evidence rows. `bridge.py:51` mints `evidence-{index}` ids instead of real `research_findings` UUIDs, which is exactly what `outreach/gate.py:864-873` rejects.

| ID | Item | DoD |
|---|---|---|
| E1 | One canonical evidence model + explicit converters at each hop | a research finding's UUID survives to `personalization_evidence_ids` |
| E2 | Preserve `evidence_excerpt` through the bridge into scoring input | dimension evidence cites an excerpt, not an index |
| E3 | Retire or wire `contracts/models.py` `Finding` | one model, not two |
| E4 | Enum reconciliation (`segment` 5 values in contracts vs 7 in IPP) | one vocabulary, or an explicit mapping with a test |

### WS-F — Qualification and Human Gate 2

**State:** the IPP engine is the strongest artefact in the repo; the seam around it is missing.
**Gap:** no findings→`QualificationInput` loader; no qualifier executor; no approval/rejection write path; `persist_draft` has never been called with a real scorecard.

| ID | Item | DoD |
|---|---|---|
| F1 | `qualification/loader.py`: select `research_findings` for a company → `QualificationInput` | a real company's persisted findings produce a scorecard |
| F2 | Live scorecard persistence through `QualificationRepository` | read-back verified row with fingerprint |
| F3 | Gate 2 write path: approve/reject with reviewer attribution and audit | scorecard leaves `draft` only through an operator action |

### WS-G — Outreach governance

**State:** the eligibility gate is complete and adversarially reviewed (2 critical, 6 high findings fixed); dispatch is unreachable in code and in the database.
**Gap:** no production adapters (`QualificationReader`, `SuppressionReader`, `DraftWriter` exist only as fakes in tests); no draft generator; no Gate 3/Gate 4 operator surface.

| ID | Item | DoD |
|---|---|---|
| G1 | Implement the three ports against Supabase | gate runs against real rows |
| G2 | Strategy + draft generator grounded in approved evidence only | a draft row cites real finding UUIDs |
| G3 | Gate 3 / Gate 4 operator commands; dispatch remains raising | approval recorded by an operator JWT; no send path |

### WS-H — Human operations

**State:** one CLI (`agents/researcher/operator_cli.py`) with four verbs: `register-company`, `create-task`, `inspect`, `list-companies`.
**Gap:** no candidate review, no qualification review, no draft review, no suppression inspection, no blocked-transition explanation; the CLI lives under an agent package.

| ID | Item | DoD |
|---|---|---|
| H1 | One `operations/` CLI covering every gate, replacing the ad-hoc script | operator completes G1–G3 from one surface |
| H2 | Every refusal states the reason and the next action | no bare exception reaches the operator |

### WS-I — Workflow orchestration and Hermes runtime

**State:** gates and idempotency exist and are tested; there is no runtime, no task source, no adapter.
**Gap:** task claiming/transition/complete lives inside each agent (`agents/researcher/agent.py:35-55,57,190`); nothing in `orchestration/` writes `tasks.status`.

| ID | Item | DoD |
|---|---|---|
| I1 | `Runtime`/`TaskSource` implementations over the `tasks` table | claim/lease/complete with idempotency keys |
| I2 | Hermes adapter implementing `Runtime` without business logic | an agent run driven by Hermes and by a manual trigger through the same contract |
| I3 | Retry/timeout/failure normalisation + escalation | a failing task reaches a terminal state with a reason |

### WS-J — Reliability and observability

**State:** absent. `trace_id` is a DTO field that is never persisted; discovery logs nothing; redaction exists in exactly one module.

| ID | Item | DoD |
|---|---|---|
| J1 | Run/trace identifiers on every stage entry point and persisted with the run record | a discovery batch is traceable from CLI to row ids |
| J2 | Structured logging with redaction shared from the outreach implementation | no secret can reach a log; one formatter |
| J3 | Operational summary per run (counts, retries, durations, failures) | `reports/`-free machine-readable run summary |

### WS-K — Security and access control

**State:** RLS denies anon entirely; agents are scoped to their own tasks; two over-permission policies were identified by audit (`agent_select_research_reports` branch 2, `agent_select_lead_qualifications`) and one missing suppression filter on `company_domains`.
**Gap:** RLS alone is the only barrier — the privilege layer grants UPDATE/DELETE on 12 tables, so one permissive policy is a full write.

| ID | Item | DoD |
|---|---|---|
| K1 | Tighten the two cross-agent read policies and the `company_domains` suppression filter | migration proposal, verified against policy intent |
| K2 | Adversarial RLS suite: cross-agent read, foreign candidate adoption, evidence onto a foreign candidate, rewind, forged approval | each attempt fails with the expected SQLSTATE |
| K3 | Secret hygiene audit: `agents/researcher/provision.py` requires a service-role key inside `agents/` | the key requirement moves out of the agent layer |

### WS-L — End-to-end testing

**State:** 402 tests, all unit/contract; six DB tests skipped.

| ID | Item | DoD |
|---|---|---|
| L1 | Lifecycle test: Discovery → Register → Research → Qualify → Strategize → Prepare → Approve, synthetic fixtures | one run through the whole loop with persisted evidence |
| L2 | Metrics where measurable (discovery precision, evidence coverage, persistence reliability, retry behaviour, latency) | numbers reported, or explicitly `unknown` |

### WS-M — Deployment and production readiness

| ID | Item | DoD |
|---|---|---|
| M1 | Environment isolation proof: every entry point refuses a Production ref | test + guard |
| M2 | Production readiness assessment (backups, migrations, secrets, RLS FORCE, audit) | written verdict with evidence, not a checklist |

### WS-N — Brand and portfolio

| ID | Item | DoD |
|---|---|---|
| N1 | Claim consistency between portfolio copy and approved business documents (`business/`, `delivery/`) | automated check over the tracked content |
| N2 | Build/deploy configuration present, publishing deferred to owner | config in repo; no DNS or deploy without authorization |

---

## 5. Dependencies and parallelisation

```
C4 (Gate 1) ──► C5 (CLI) ──────────────┐
A1 (contracts) ─► I1 ─► I2 ─► I3       ├──► L1 (lifecycle e2e) ─► M2 ─► verdict
E1 (evidence) ─► E2 ─► F1 ─► F2 ─► F3 ─┤
D2 (research) ─────────┘               │
G1 (ports) ─► G2 ─► G3 ────────────────┘
B1 (DSN) ─► K2 (adversarial RLS)
```

Independent and safe to run in parallel at any time: A3, A4, B2–B5 (proposals only), C2, E3/E4, H2, J1–J3, K3, N1.

## 6. Execution waves

- **Wave 1 (unblock and prove):** B1, C1, C2, C6 — get Discovery executing against a real database and make the security suite executable.
- **Wave 2 (make the loop real):** C4, C5, H1, E1, F1, F2.
- **Wave 3 (research and outreach coupling):** D1, D2, G1, G2, F3, G3.
- **Wave 4 (platform spine):** A1, A2, I1–I3.
- **Wave 5 (prove and judge):** K2, L1, L2, M1, M2, final audit.

## 7. Security requirements (every workstream)

- Fail closed on ownership and identity uncertainty; never infer ownership from an error string, an array position, or a counter.
- No `service_role` in any agent or pipeline path; the operator path uses the operator JWT.
- No RLS weakening to make a test pass; a policy change is a migration proposal with a verification plan.
- No secrets in logs, reports, prompts, subagent contexts or commits.
- Every integration test is environment-guarded and refuses a Production ref.

## 8. Human approval requirements

| Gate | Transition | Enforced by |
|---|---|---|
| G1 | candidate → registered company | operator JWT + candidate review pair (`chk_discovery_candidate_review_pair`) |
| G2 | research → qualified | approved scorecard, one per company (`uq_lead_qualifications_approved`) |
| G3 | qualified → draft approved | `fn_enforce_outreach_human_approval` |
| G4 | approved → dispatch authorization | separate action; dispatch remains unreachable |

Agents can never satisfy G1–G4: the policies test `is_human_operator()` on JWT claims.

## 9. Development integration requirements

A capability counts as integrated only when, in Development, with a real JWT and no `service_role`:

1. the write lands and is read back;
2. the wrong actor's attempt is refused by RLS with the expected error;
3. a retry does not duplicate;
4. the failure path leaves an explicit, actionable state.

Simulated-claims probes inside rolled-back transactions count as **policy verification**, never as live integration.

## 10. Test strategy

| Tier | What it proves | Where |
|---|---|---|
| Unit | pure logic, scoring, parsing | `tests/test_*.py` |
| Contract | adapter behaviour against a fake transport | `test_provider_contract.py`, `test_crawl4ai_adapter_contract.py` |
| Static SQL | the SQL text still contains the fix | `test_security_sql_static.py` |
| Policy (rolled back) | RLS predicates fire with simulated claims | new, WS-C6/K2 |
| Live integration | real PostgREST + real JWT | WS-C1/C4, needs identity |
| Lifecycle | the whole loop | WS-L1 |

Skipped ≠ passed. A tier with no executable test is reported as unverified.

## 11. Operational readiness criteria

`OPERATIONALLY READY — DEVELOPMENT` requires all of:

1. WS-C complete: a real batch staged, reviewed, registered, retried without duplication.
2. WS-B1 closed: the DB policy suite executes and passes.
3. WS-E1/E2 closed: evidence identity survives research → score → draft.
4. WS-F1/F2 closed: a real company's findings produce a persisted scorecard.
5. WS-H1 closed: one operator surface covers G1–G3.
6. WS-K2 closed: adversarial RLS attempts fail as designed.
7. WS-J1/J2 closed: runs are traceable and logs are redacted.
8. Every remaining gap named as blocked with its owner action.

## 12. Deferred (explicitly not v1)

- `crm/phase2_delivery_expansion.sql` and `crm/delivery-governance-expansion.sql` partnership/project/milestone tables (2,127 lines, zero code, zero deployed tables) — keep one design, deploy nothing.
- Revenue-share modelling, capacity planning, invoicing.
- Multi-tenant or multi-workspace CRM.
- A web dashboard; the operator CLI is the surface until the loop runs daily.
- OpenGTM as a mandatory dependency; scheduling until a runtime executes tasks.

## 13. External prerequisites (owner actions, cannot be self-served)

| ID | Action | Blocks |
|---|---|---|
| OW-1 | Authorize one Development Auth user with `app_metadata {"user_role":"agent","subagent_type":"discovery","agent_id":"hermes-discovery-v1"}` (created via dashboard/admin API; no service-role key exists locally) | live agent-path integration (C1, C6-live, K2-live) |
| OW-2 | Provide a Development Postgres DSN for `tests/db/`, or authorize a persistent DB role | B1, H-01…H-05 execution proof |
| OW-3 | Decide OpenGTM: accept AGPL-3.0 or drop it for an owned discovery source | D4 |
| OW-4 | Provide Crawl4AI deployment (Docker restart or hosted endpoint) | D1 |
| OW-5 | Decide GPT Researcher: provide LLM/search credentials or defer live research | D2/D3 |
| OW-6 | Approve any migration application (H-06, K1, B3/B4) | B2–B4, K1 |
| OW-7 | Resolve the GPT Researcher licence conflict before distribution | M2 |

## 14. Definition of done — index

| Workstream | Done means |
|---|---|
| A | runtime contracts importable, boundary enforced, docs match code |
| B | security suite executable and green; one schema truth; proposals filed |
| C | a real candidate goes discovery → review → company, twice, without duplication |
| D | one provider live with a recorded response; research persisted end-to-end |
| E | evidence identity preserved through every hop |
| F | real findings produce a persisted, human-approved scorecard |
| G | drafts built from real rows, gates 3/4 operable, dispatch still unreachable |
| H | one CLI, every gate, every refusal explained |
| I | a task can be claimed, executed and completed through a contract, by two runtimes |
| J | a run is traceable end to end; no secret in any log |
| K | adversarial RLS attempts fail; over-permissions tightened |
| L | one lifecycle end-to-end run with recorded artefacts |
| M | isolation proven; readiness verdict written |
| N | claims consistent; config in repo; nothing published |
