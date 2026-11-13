# Eryndex Target Architecture v1.0

**Status:** Accepted (Phase 1)
**Date:** 2026-11-13
**Supersedes:** the implicit "researcher-centric script" architecture described in `reports/phase-3.x`
**Related:** `integration-contracts.md`, `multi-agent-operating-model.md`, `architecture-decision-records/`

---

## 1. Purpose

Eryndex runs one business loop: **find agencies that buy engineering, understand them well enough to be useful, qualify them honestly, approach them in a way a human would approve, and operate the resulting partnership.**

The repository today implements one step of that loop (research) as a script bound to a single agent package. This document defines the architecture that lets all five workflows run on the same spine without the platform turning into a pile of one-off scripts.

Two properties matter more than any other:

1. **Business logic must not depend on a runtime.** Antigravity is where work happens today; Hermes is where it is meant to happen later. Neither may be baked into a workflow.
2. **Nothing enters the CRM without a recorded human decision.** The database is the business's asset; agents are guests with narrow, task-scoped credentials.

---

## 2. Layer model

```
┌──────────────────────────────────────────────────────────────┐
│ G. Human Operations      operator CLI · approval queues      │
├──────────────────────────────────────────────────────────────┤
│ A. Business Workflow     9 lifecycle stages, gates, states   │
├──────────────────────────────────────────────────────────────┤
│ B. Orchestration         task intake · dispatch · retry      │
│                          runtimes: Antigravity | Hermes      │
├──────────────────────────────────────────────────────────────┤
│ C. Agent Execution       discovery · researcher · qualifier  │
│                          outreach · partnership ops          │
├──────────────────────────────────────────────────────────────┤
│ D. Integration Adapters  OpenGTM · GPT Researcher · Crawl4AI │
│                          Supabase (persistence)              │
├──────────────────────────────────────────────────────────────┤
│ E. Intelligence          evidence normalization · IPP v2     │
├──────────────────────────────────────────────────────────────┤
│ F. Persistence & Gov.    Supabase PostgreSQL + RLS + audit   │
└──────────────────────────────────────────────────────────────┘
```

Dependencies point **inward**: agents depend on contracts and adapters; adapters depend on contracts; nothing depends on an agent. The workflow layer knows stage names and gates, not vendor APIs.

### A. Business workflow layer

Nine stages, each with an owner, a persisted state, and an exit condition:

| # | Stage | Persisted in | Exit gate |
|---|---|---|---|
| 1 | Discover | `discovery_candidates` (new) | Human Gate 1 — ingestion |
| 2 | Register | `companies`, `company_domains` | operator action |
| 3 | Research | `tasks`, `research_reports`, `research_findings` | persistence verification |
| 4 | Qualify | `lead_qualifications` | Human Gate 2 — qualification review |
| 5 | Strategize | `lead_qualifications.recommended_*`, activities | — |
| 6 | Prepare outreach | `outreach_campaigns`, `outreach_messages` (draft) | Human Gate 3 — draft approval |
| 7 | Approve & (future) dispatch | `outreach_messages.approval_status` | Human Gate 4 — dispatch authorization |
| 8 | Engage | `conversations`, `conversation_messages` | — |
| 9 | Manage partnership | `partnerships`, `projects`, … (undeployed) | human commercial decision |

Stage transitions are proposals from the orchestration layer and are only persisted by an authorized identity. No stage advances because a script finished.

### B. Orchestration layer

A single interface, two implementations:

```python
class TaskSource(Protocol):        # asks: what work is mine right now?
    def claim(self, agent_id: str, task_types: list[str]) -> Task | None: ...

class Runtime(Protocol):           # asks: how does an agent actually run here?
    def execute(self, agent: Agent, task: Task) -> ExecutionResult: ...
```

- `AntigravityRuntime` — the supervised, human-triggered path used today (`python -m agents.researcher.runner ...`). Not autonomous.
- `HermesRuntime` — the target: tasks claimed by Hermes, delegated to scoped subagents, results written back through the same repositories.

Orchestration owns **retry, idempotency, and escalation**. It never owns business rules: a retry re-runs the same contract with the same idempotency key.

### C. Agent execution layer

Each agent is a thin composition: `contracts` (what it may read/write) + `integrations` (where data comes from) + `intelligence` (how it reasons). Agents do not talk to each other; they hand off through tasks and persisted state. Agent contracts stay versioned (`agent-contracts.md`), and every agent identity is a Supabase Auth user with `app_metadata.user_role=agent`.

### D. Integration adapter layer

Every external tool sits behind one interface (§3) and is replaceable. Three properties are non-negotiable:

- **No provider writes to canonical tables.** Providers return data; persistence is the platform's job, under the agent's identity, through a repository.
- **Failures are normalized.** No provider-specific exception escapes the adapter.
- **Every call is attributable.** Request id, provider, version, latency, and outcome are recorded, so a report can be traced to the tool that produced it.

### E. Intelligence layer

Deterministic where it must be. The IPP v2 engine is pure: same evidence in, same scorecard out, every point traceable to finding IDs. Evidence normalization happens once, upstream of both scoring and drafting, so the scorecard and the outreach draft cite the same facts.

### F. Persistence and governance layer

Supabase remains the only system of record. Three rules survive from the existing design and are tightened here:

1. Agents use authenticated JWTs, never `service_role`.
2. Every write is scoped by RLS to an active task (existing pattern) or to a human operator identity.
3. Historical and delivered data is immutable from the platform's perspective: nothing rewrites a completed `research_report` or an approved qualification.

### G. Human operations layer

The operator CLI is the only surface that performs privileged transitions: company registration, task assignment, qualification approval, outreach approval, dispatch authorization. It grows into a review queue rather than staying a set of verbs.

---

## 3. Provider contract (summary)

Full definitions in `integration-contracts.md`. The shape, in one block:

```python
class ProviderRequest:  # provider-agnostic
    capability: Literal["discover", "crawl", "research"]
    payload: dict            # validated against the capability schema
    idempotency_key: str     # deterministic, content-addressed
    timeout_s: float
    trace_id: str

class ProviderResult:
    status: Literal["ok", "partial", "error"]
    data: dict               # normalized, never provider-shaped
    evidence: list[Evidence] # url, excerpt, retrieved_at, provider, version
    provider: ProviderInfo
    latency_ms: int
    warnings: list[str]

class ProviderError(Exception):
    code: Literal["timeout", "unavailable", "unauthorized", "rate_limited",
                  "invalid_response", "schema_violation", "budget_exceeded"]
    retryable: bool
```

`capability` is what makes adapters replaceable: the workflow asks for "crawl this URL", never for "Crawl4AI".

---

## 4. Package layout

Existing structure is preserved where it works; generic infrastructure is extracted out of `agents/researcher/` (ADR-0002).

```
contracts/            versioned data + provider contracts, environment guard, errors
  environment.py      env loading, fail-closed Development-only guard
  models.py           Company, Evidence, Finding, Candidate, Scorecard
  provider.py         ProviderRequest/Result/Error, HealthStatus
  errors.py

integrations/
  base.py             retry policy, timeouts, error normalization, tracing
  crawl4ai/           crawl capability adapter
  gpt_researcher/     research capability adapter
  opengtm/            discovery capability adapter
  supabase/           auth, PostgREST client, repositories (companies, tasks,
                      research, qualification, outreach, candidates)

intelligence/
  evidence.py         normalization + dedupe of evidence from any provider
  ipp/                deterministic IPP v2 scoring engine

orchestration/
  workflow.py         stage definitions, gate assertions
  idempotency.py      content-addressed keys
  runtime.py          Runtime protocol + Antigravity/Hermes implementations

agents/
  researcher/         existing vertical slice (kept working, re-exports preserved)
  discovery/          new
  qualification/      new
  outreach/           new

crm/                  SQL designs and deployable migrations (schema source)
supabase/migrations   applied migrations only
tests/                unit · contract · integration · security · regression
docs/                 architecture, lifecycle, ADRs
reports/eryndex/      baseline, progress, verification, evaluation, readiness
portfolio/            independently deployable eryndex.net site
```

Numbers are not the point; the boundaries are. `agents/` may import `contracts`, `integrations`, `intelligence`; the reverse is forbidden and is enforced by a test (`tests/test_architecture_boundaries.py`).

---

## 5. Cross-cutting rules

| Rule | Enforcement |
|---|---|
| Least privilege | agent JWT scope + RLS; no `service_role` in any agent path |
| Idempotency | content-addressed keys before writes; no duplicate reports/findings on retry |
| Determinism | pure intelligence functions; no clock/random in scoring |
| Traceability | every persisted finding carries an evidence URL + excerpt; every run carries a trace id |
| Uncertainty | `information_gap_tbd` is a first-class finding type; absence of evidence is never scored as evidence |
| Human gates | a gate is a state transition that only an operator identity can perform |
| Production safety | environment guard compares project ref against an allowlist before the first network call |
| Runtime independence | business code imports no runtime SDK |

---

## 6. Non-goals for v1.0

- Autonomous outbound messaging of any kind.
- A second database, cache, or vector store as a business record.
- Multi-tenant access for agencies.
- Pricing, capacity, or contractual commitments generated by an agent.
- Replacing Supabase with a provider's internal storage.

---

## 7. Known constraints carried into Phase 2+

1. Four designed tables (`partnerships`, `projects`, `project_milestones`, `project_change_requests`) are not deployed; Workflow 9 has no persistence yet (BLK-01).
2. No DDL credentials → new tables (discovery staging) cannot be applied, only authored.
3. No LLM/search credentials → research adapters are contract-tested with fakes until keys exist (BLK-03).
4. Docker daemon down → containerized providers cannot be health-checked locally (BLK-02).
5. Research persistence has no database-level uniqueness; idempotency is enforced in the platform until a migration can add it.

These are recorded as constraints, not as accepted design. Each one has an owner action in `reports/eryndex/implementation-progress.md`.
