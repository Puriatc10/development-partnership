# Eryndex Multi-Agent Operating Model v1.0

**Status:** Active
**Date:** 2026-11-13
**Supersedes:** `agents/agent-operating-model.md` where they conflict (that document remains the source for per-agent contract detail until it is revised in place)

---

## 1. Why this document exists

The mission requires real specialised agents, explicit handoffs, and one owner of cross-agent decisions. It also requires the business logic not to live inside one runtime's prompts. This document defines the team, the handoff artefacts, and the rules that keep concurrent agents from corrupting each other's work.

## 2. Runtime reality (stated honestly)

| Runtime | Role | Status |
|---|---|---|
| **Hermes** | target orchestrator: task intake, delegation, retries, escalation | used for this transformation; delegation is real (isolated subagents, disjoint file scopes) |
| **Antigravity** | supervised interactive environment the project was built in | not available in the current session; its execution pattern is preserved as `AntigravityRuntime` (manual, human-triggered) |

**Deviation recorded:** the mission asks for Antigravity multi-agent execution. This session runs on Hermes, which the same mission names as the long-term orchestrator. Work is therefore executed as Hermes-delegated subagents with explicit contracts, and the Antigravity path is kept working (`python -m agents.researcher.runner`) so either runtime can drive the same code. No claim of autonomous operation is made — every runtime path today is human-triggered.

## 3. The team

| # | Role | Owns | May write |
|---|---|---|---|
| 1 | **Orchestrator** (Chief Architect) | cross-agent decisions, integration of work, acceptance | `docs/architecture/**`, `reports/eryndex/**`, shared contracts |
| 2 | **Repository Auditor** | factual baseline, gap register | `reports/eryndex/**` (audit sections) — **no code** |
| 3 | **Integration Engineer** | provider adapters, health, retries | `integrations/**`, `tests/test_provider_contract.py` |
| 4 | **CRM & Security Engineer** | schema, migrations, RLS, identity | `crm/**`, `supabase/migrations/**`, `integrations/supabase/**` |
| 5 | **Research & Intelligence Engineer** | evidence model, IPP v2 engine | `intelligence/**`, `tests/test_ipp_*` |
| 6 | **QA & Reliability Engineer** | test strategy, failure injection, acceptance evidence | `tests/**` (except contract tests owned by 3) |
| 7 | **Brand & Portfolio Engineer** | portfolio, positioning, claim safety | `portfolio/**`, `docs/brand/**` |

Two agents never write the same file. When a change crosses a boundary (e.g. a provider adapter needs a new column), the Orchestrator writes the interface, and the owning agent implements behind it.

## 4. Handoff artefacts (the only legitimate channel)

| Artefact | Location | Produced by | Consumed by |
|---|---|---|---|
| Task | `tasks` table (or board item) | Orchestrator | agent |
| Contract | `contracts/**` | Orchestrator | all |
| Evidence pack | `reports/eryndex/evidence/**` | auditor / QA | Orchestrator |
| Execution result | `research_reports`, `lead_qualifications`, adapter results | agent | Orchestrator, human |
| Progress entry | `reports/eryndex/implementation-progress.md` | Orchestrator | everyone |

Agents do not message each other. A handoff that is not persisted did not happen.

## 5. Lifecycle of a unit of work

```
NOT_STARTED → IN_PROGRESS → (BLOCKED | IN_REVIEW) → COMPLETED
```

Rules:

1. `IN_PROGRESS` requires a named owner and a declared file scope.
2. `BLOCKED` requires the missing thing and who can unblock it — never "waiting".
3. `IN_REVIEW` requires evidence: test output, a DB read-back, or a provider response id.
4. `COMPLETED` requires the verification artefact to exist. Writing code is not completion; a passing test that does not exercise the integration is not verification.

## 6. Authority boundaries

Agents (all of them, including the Orchestrator) **may not**:

- send outreach or contact anyone outside the business;
- commit to price, capacity, timeline, contract, or revenue share;
- register a company in the CRM without Human Gate 1;
- approve a qualification or an outreach draft;
- use `service_role` or bypass RLS;
- modify Production, or rerun/replace the Refokus research records;
- claim a capability the business does not have (the delivery team is not onboarded).

Only a human operator performs gates G1–G4 (ADR-0005). Escalate instead of guessing when a decision needs credentials, money, legal judgement, or an irreversible production action.

## 7. Concurrency and conflict rules

- One branch (`feat/eryndex-platform-transform`); no agent commits to `main`.
- Disjoint file scopes per parallel agent, declared before work starts.
- Shared files (`contracts/**`, `AGENTS.md`, `docs/architecture/**`) are Orchestrator-only during parallel work.
- Database writes are serialised: only one agent executes against Development at a time, and every execution names its task id.
- No agent reruns a completed task; a re-run is a new task id (or an idempotent no-op per ADR-0004).

## 8. Escalation ladder

| Situation | Action |
|---|---|
| Missing credential / access | `BLOCKED` + named requirement; continue independent work |
| Contract ambiguity | Orchestrator decides, records an ADR |
| Business-judgement call (pricing, positioning, capacity) | stop; ask the operator |
| Security boundary in the way | stop; report; do not widen the grant |
| Provider failure | adapter returns `ProviderError`; workflow degrades, never fabricates |
| Conflicting instructions | operator's latest instruction wins over any document |

## 9. Agent roster mapped to workflows

| Workflow | Primary agent(s) | Runtime path |
|---|---|---|
| 1 Discover | Discovery agent (new) + OpenGTM adapter | human-triggered campaign |
| 2 Research | Researcher (`agents/researcher`, existing) + GPT Researcher/Crawl4AI adapters | `python -m agents.researcher.runner` today; Hermes later |
| 3 Qualify | Qualification agent (new) + IPP engine | operator-triggered |
| 4 Outreach prep | Outreach agent (new) | operator-triggered |
| 5 Engage / partnership | Partnership ops agent (new) | operator-driven |
| Cross-cutting | Orchestrator, Auditor, QA | Hermes |

## 10. What is verified about this model today

- The existing researcher executes end to end under a scoped identity (Phase 3.1, re-verified in Phase 0).
- Independent Hermes subagents with disjoint scopes produced the Phase 0 audit and the IPP engine without file conflicts.
- Nothing in this document claims autonomous operation; every path requires a human trigger or an operator gate.

Revision of `agents/agent-contracts.md` and `agents/agent-operating-model.md` to match this model is tracked as work item W-1.4 in the progress board.
