# Eryndex Business Lifecycle v1.0

**Status:** Active
**Date:** 2026-11-13
**Companion:** `docs/architecture/eryndex-target-architecture.md`, `agents/workflow-specifications.md`

This document is the operational definition of the business loop. Every stage names its persisted state, its owner, its exit condition, and its true status today. If a stage is not implemented, this document says so.

---

## The loop

```
Discover → Register → Research → Qualify → Strategize → Prepare Outreach
        → Approve → (Dispatch) → Engage → Manage Partnership
```

| # | Stage | Workflow | Persisted state | Agent / actor | Exit condition | Status |
|---|---|---|---|---|---|---|
| 1 | Discover | W1 | `discovery_candidates` (designed, not deployed) | Discovery agent + OpenGTM | candidate list with provenance | **not implemented** |
| 2 | Register | W1 / G1 | `companies`, `company_domains` | **human operator** | company row + primary domain | implemented (operator CLI) |
| 3 | Research | W2 | `tasks`, `research_reports`, `research_findings`, `activities` | Researcher | 1 report + N evidence-backed findings, persistence verified | **implemented and executed** (Refokus) |
| 4 | Qualify | W3 / G2 | `lead_qualifications` | Qualification agent + IPP engine | scorecard proposed, human approves/rejects | engine not implemented; table ready |
| 5 | Strategize | W4 | `lead_qualifications` angle fields, `activities` | Qualification / Outreach agent | recommended angle + offering grounded in findings | not implemented |
| 6 | Prepare outreach | W4 / G3 | `outreach_campaigns`, `outreach_messages` (draft) | Outreach agent | draft message + evidence links, human approves | not implemented |
| 7 | Approve & dispatch | W4 / G4 | `outreach_messages.approval_status`, `sending_status` | **human operator only** | explicit dispatch authorization | not implemented (and must never be automatic) |
| 8 | Engage | W5 | `conversations`, `conversation_messages` | Partnership ops agent (assistive) | response recorded, objections/requirements captured | not implemented |
| 9 | Manage partnership | W5 | `partnerships`, `projects`, `project_milestones`, … | human + delivery ops | commercial decision recorded, project tracked | tables **undeployed** (BLK-01) |

---

## Stage detail

### 1. Discover (W1)

- **Input:** a campaign directive — target segments (digital agency / Webflow studio / Framer studio), approved sources, region, limit.
- **Sources (approved only):** Webflow Experts Directory, Webflow Showcase agency profiles, Framer Partner Directory, Framer Made In Gallery, Clutch, Dribbble studio profiles, Behance agency teams.
- **Prohibited:** unofficial email scraping, paid bulk lead lists, anything violating platform terms, bypassing robots/access restrictions.
- **Output:** normalized candidate records (name, domain, source URL, source name, discovered_at, signals) written to staging — **never** directly to `companies`.
- **Duplicate rule:** domain-normalized (strip scheme/`www.`/trailing slash, lowercase) and name-normalized (lowercase, punctuation-stripped) comparison against existing `companies` and against the staging batch.
- **Exit:** Human Gate 1.

### 2. Register (G1)

Only an operator identity may insert a company. Discovery agents must not hold INSERT on `companies` or `company_domains`. Registration records the discovery source and the approving human, so a later audit can answer "why is this company in the CRM?".

### 3. Research (W2)

- Investigates: services and positioning, technology stack, client portfolio, project complexity, team structure, growth signals, engineering bottlenecks, wholesale-development opportunities, decision-makers, commercial unknowns.
- **Output contract:** human-readable report + atomic findings (`verified_fact` / `hypothesis` / `information_gap_tbd`), each with category from the schema enum, confidence, evidence URL, and excerpt. `verified_fact` with confidence high/medium **requires** an evidence URL (DB check constraint).
- **Never:** invent facts to complete a payload; promote a hypothesis to a verified fact to satisfy a count.
- **Exit:** persistence verified by read-back (report id + finding count match), task completed under governance triggers.
- **Idempotency:** a re-run must not duplicate the report (ADR-0004).

### 4. Qualify (W3 / G2)

- Canonical model: IPP v2 — 7 dimensions, 100 points (Customer Flow 20, Development Demand 20, Repeatability of Orders 15, Technical Fit 15, Business Size 10, Decision-Maker Accessibility 10, Commercial Fit 10).
- Every point must cite the finding IDs that justify it. Missing evidence lowers confidence and adds an explicit gap — it never becomes a midpoint guess.
- Output: structured scorecard (dimension scores + rationale + confidence + gaps + total + tier + recommended action) submitted for human review.
- **Human decision is the only thing that makes a qualification real.** The engine's output is a proposal.

### 5. Strategize (W4 part 1)

Turns research + scorecard into a commercial angle: what to offer (project-based wholesale development first), which evidence supports the angle, which decision-maker to approach, and what must not be claimed (capacity, team size, delivery history, timelines).

### 6. Prepare outreach (W4 part 2 / G3)

- Draft only. No sending, no scheduling, no third-party dispatch integration enabled.
- Suppression registry is checked before drafting (and again before dispatch): a suppressed domain or contact produces no draft.
- Drafts carry the evidence they rely on so a reviewer can check them in seconds.

### 7. Dispatch authorization (G4)

Separate, explicit, per-message human authorization. There is no code path that sends after approval; approval and dispatch are distinct decisions (ADR-0005).

### 8. Engage (W5)

Record inbound replies, objections, requirements, follow-ups, next actions, and human decisions. Conversations are append-only history; no agent rewrites a prior message record.

### 9. Manage partnership (W5)

Partnership stages, commercial opportunities, project tracking, milestone acceptance, change requests. Requires the delivery tables (`partnerships`, `projects`, `project_milestones`, `project_change_requests`), currently designed but undeployed.

---

## Gates (single source of truth)

| Gate | Between | Who may act | Enforced by |
|---|---|---|---|
| G1 Ingestion | Discover → Register | operator/admin/founder JWT | RLS + `orchestration.workflow.assert_gate` |
| G2 Qualification | Qualify → Strategize | operator | RLS + gate assertion |
| G3 Draft approval | Prepare → Approve | operator | gate assertion + `outreach_messages.approval_status` |
| G4 Dispatch | Approve → Send | operator, separate action | gate assertion |

---

## Truthfulness rules that apply at every stage

1. The delivery team is **not onboarded**. No artifact may imply current delivery capacity, staffed team, client roster, or testimonials.
2. Unknown parameters are `TBD`, never plausible defaults.
3. Revenue-sharing is **not** offered in cold outreach.
4. Partner's end clients are never contacted, solicited, or referenced without written authorization.
5. A claim that cannot be cited to evidence does not appear in a report, draft, or portfolio page.

---

## Success measures (per stage)

| Stage | Measure | How computed | Current |
|---|---|---|---|
| Discover | precision of candidates entering G1 | approved / proposed | unknown (no data yet) |
| Research | findings per company, % with usable evidence | count + evidence URL presence | 13 findings, 100% URL coverage (Refokus) |
| Research | rework rate | reports needing correction / total | unknown |
| Qualify | inter-run consistency | same evidence → same scorecard | to be measured on the engine |
| Outreach | draft approval rate | approved / drafted | unknown |
| Ops | human minutes per company | operator time log | unknown |

Where a measure cannot be computed from real data, it is reported as **unknown** rather than estimated.
