# Phase 2.2: Agent Operating Model & Workflow Design — Final Report

**Document Version:** 2.0.0  
**Phase:** 2.2 — Final Contract & Runtime Alignment  
**Workspace:** `development-partnership/`  
**System Role:** Principal Multi-Agent Systems Architect, Database Security Auditor & Workflow Engineer  
**Target Platform:** Logical Multi-Agent Architecture (Antigravity Interactive Supervised Runtime [Temporary] / Hermes Autonomous Runtime [Future]) & Supabase PostgreSQL CRM  
**Status:** Complete — Canonical Alignment Pass Concluded — **PHASE 2.2 — CLOSED AND READY FOR IMPLEMENTATION**  
**Date:** 2026-11-07  

---

> [!NOTE]
> **Synthetic Data Disclosure:** All agency names, personal identities, email addresses, web domains, and sample UUIDs referenced in this report and associated specifications are strictly synthetic, illustrative examples designed for workflow modeling, data-flow illustration, and schema validation.

---

## 1. Executive Summary & Operational Context

This report provides the definitive architectural blueprint, alignment record, and closure summary for **Phase 2.2: Agent Operating Model & Workflow Design** for the Managed Development Partnership business.

Phase 2.2 establishes the operational contracts, multi-agent workflows, data exchange schemas, and human oversight boundaries connecting multi-agent orchestration to the Supabase CRM database. Following the initial architectural draft, a rigorous final alignment pass was conducted to guarantee that all agent contracts, payload schemas, and workflow specifications are strictly executable against the actual PostgreSQL schema definitions and compatible with our selected temporary interactive runtime (**Antigravity**) and future autonomous runtime (**Hermes**).

### 1.1. Pre-Operational Stage Adherence
In strict accordance with organizational governance codified in [`AGENTS.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/AGENTS.md) and [`business/business-overview.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/business/business-overview.md):
- **Pre-Operational Foundation:** The planned technical engineering team is **not yet onboarded and not currently available for client delivery**.
- **Scope Boundary:** **Design and Documentation Only**.
  - Zero modifications to `crm/schema.sql` or `crm/delivery-governance-expansion.sql`.
  - Zero live connections to remote or local Supabase databases.
  - Zero migration commands executed.
  - Zero implementation of live Hermes code or external communication scripts.
- **Completed Baselines Untouched:** Phase 1 (Business Foundation) and Phase 2.1 (Batches A, A.1, A.2, A.3, B, and B-Closure) remain fully intact as the established architectural foundation.

---

## 2. Deliverable Documentation Artifacts

Phase 2.2 encompasses four comprehensive operational specifications in the `agents/` directory, all updated and verified at Version 2.0.0:

| Document | File Path | Core Contents & Purpose |
| :--- | :--- | :--- |
| **Agent Operating Model** | [`agents/agent-operating-model.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/agent-operating-model.md) | Architectural topology, runtime decoupling, logical agent roles, database security & exact JWT identity mapping, complete Responsibility & Permission Matrix, human governance gates, and operational non-negotiables. |
| **Standard Agent Contracts** | [`agents/agent-contracts.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/agent-contracts.md) | Standardized 15-point contracts for Orchestrator, Discovery, Researcher, Qualifier, and Drafter agents, featuring illustrative, syntactically valid JSON input and output payloads using stable database primary keys and canonical PostgreSQL enums. |
| **Workflow Specifications** | [`agents/workflow-specifications.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/workflow-specifications.md) | Step-by-step specifications for 5 core workflows (Prospect Discovery, Company Research, Lead Qualification, Outreach Preparation, and Inbound Conversation Handling) with flowcharts, human gates, and contact boundary governance. |
| **Task Execution Protocol** | [`agents/task-execution-protocol.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/task-execution-protocol.md) | Task coordination protocol grounded in the existing PostgreSQL `tasks` table, lifecycle state machines, dependency management, analysis of database task limitations, runtime adapter boundaries, and failure handling. |

---

## 3. Final Contract & Runtime Alignment Record

During the final alignment pass, the operational specifications were brought into 100% executable conformity with the canonical SQL schema and our runtime strategy:

### 3.1. JWT Claims & Auth Alignment
All documentation and contract examples have been aligned with the exact SQL helper functions in `crm/schema.sql`:
- **Human Operator:** SQL helper `is_human_operator()` requires `auth.jwt() -> 'app_metadata' ->> 'user_role' IN ('operator', 'admin', 'founder')`. The string `'human_operator'` was removed.
- **Agent Role:** Authenticated subagents present `auth.jwt() -> 'app_metadata' ->> 'user_role' = 'agent'`.
- **Subagent Type:** SQL helper `is_agent_type(p_type)` explicitly inspects `auth.jwt() -> 'app_metadata' ->> 'subagent_type' = p_type` (accepting `'researcher'`, `'qualifier'`, or `'drafter'`). The claim key `'agent_type'` was eliminated.
- **Agent Identity:** SQL helper `current_agent_identity()` checks `COALESCE(auth.jwt() -> 'app_metadata' ->> 'agent_id', auth.jwt() ->> 'sub')`. The claim key `'agent_identity'` was replaced with `'agent_id'`.

### 3.2. PostgreSQL Enum Compatibility
All JSON payload schemas, research categorization guidelines, and validation rules now strictly reference canonical database enums:
- `research_finding_type`: Restricted to `'verified_fact'`, `'hypothesis'`, and `'information_gap_tbd'` (replacing deprecated strings like `'inferred_signal'` or `'unverified_claim'`).
- `finding_category_type`: Strictly mapped to `'business_model'`, `'client_roster'`, `'technology_stack'`, `'scoping_maturity'`, `'growth_signals'`, `'pain_points'`, `'pricing_rates'`, `'team_structure'`, and `'other'` (replacing invalid strings like `'client_portfolio'`, `'decision_maker'`, `'recurring_demand'`, or `'commercial_model'`).
- `contact_role_type`: Restricted to `'founder_executive'`, `'technical_lead'`, `'operations_lead'`, `'account_manager'`, `'designer_developer'`, and `'other'` (replacing `'founder'`).

### 3.3. Database Permission Boundaries & Task Governance
- **Discovery Agent:** Operates strictly in-memory or produces output candidate files; has zero direct database write permissions.
- **Researcher & Contacts:** The Research Agent CANNOT insert or update records in `contacts` (the RLS policy on `contacts` restricts mutations exclusively to authenticated human operators). Discovered decision-makers are emitted as proposed output artifacts within the research payload for Human Operator review and ingestion.
- **Orchestrator Role:** The Orchestrator has NO dedicated database agent identity and NO RLS write policies. Task provisioning and task cancellation are executed exclusively via authenticated Human Operator JWT sessions (`user_role IN ('operator', 'admin', 'founder')`) or trusted backend service pipelines.
- **Task Immutability:** Agents cannot reset stale tasks from `in_progress` to `pending`. Orchestrator cannot cancel tasks through agent RLS. Leases, retries, and worker recovery are NOT implemented database capabilities.

### 3.4. Runtime Decoupling (Antigravity Temporary vs. Hermes Future)
The operational architecture is decoupled into three explicit tiers:
1. **Tier 1: Logical Agent Architecture:** The 5 core workflows, task state transitions, and human review gates remain constant.
2. **Tier 2: Runtime Adapter Layer:**
   - **Temporary Supervised Runtime (Antigravity):** Interactive, human-supervised execution. Subagents execute on-demand; errors are inspected interactively by the operator; there are no background daemons, worker leases, or autonomous schedulers.
   - **Target Autonomous Runtime (Hermes):** Multi-agent orchestration engine with application-level leases and retries, deployed when project credentials and hosting infrastructure are provisioned.
3. **Tier 3: Database Persistence & Authorization (Supabase PostgreSQL):** The canonical source of truth enforcing RLS, foreign keys, and triggers identically across any runtime. Switching runtimes requires zero database refactoring.

---

## 4. Capability Matrix: Implemented vs. Proposed vs. Future Dependencies

The matrix below delineates current database capabilities, proposed operational behavior, and future implementation requirements:

| Domain / Capability | Implemented Database Capability (Phase 2.1) | Proposed Operational Behavior (Phase 2.2) | Future Implementation Dependency (Phase 2.3+) |
| :--- | :--- | :--- | :--- |
| **Prospect Discovery** | Table `companies` exists; INSERT restricted to `is_human_operator()`. | Discovery Agent searches public directories; produces candidate JSON payloads. | Ingestion script or operator ingestion UI. |
| **Company Registration** | Handled via authenticated human operator session in Supabase. | Operator reviews discovery candidates and authorizes creation of `companies` and `domains`. | Ingestion API endpoint or webhook receiver. |
| **Company Research** | `research_reports` and `research_findings` tables with STAC RLS policies. | Researcher subagent conducts web research and persists findings with source URLs. | Search API tool bindings (Firecrawl / Google Search). |
| **Contact Discovery** | `contacts` table with RLS restricting INSERT/UPDATE to human operators. | Researcher extracts contacts into research findings payload for operator review. | Operator contact ingestion workflow. |
| **Lead Qualification** | `lead_qualifications` table with 7-dimension check constraints and human gate trigger. | Qualifier evaluates findings, computes 0–100 score, assigns tier, submits draft. | Qualifier prompt templates with zero-evidence rules. |
| **Outreach Drafting** | `outreach_messages` with human gate trigger, composite FKs, and rev-share block. | Drafter writes personalized copy referencing verified portfolio work; submits draft. | Drafter prompt templates with grounding validation. |
| **Outreach Dispatch** | Triggers and check constraints strictly block dispatch without human sign-off. | Human Operator reviews, edits, and explicitly authorizes message queueing. | Transactional email / LinkedIn sending integration. |
| **Task Management** | `tasks` table with status progression triggers, completed_at sync, and STAC RLS. | Tasks created via Operator session; subagents update assigned tasks `in_progress` $\to$ `completed`. | Runtime task runner (interactive Antigravity or autonomous Hermes). |
| **Task Recovery & Leases** | Not implemented in database. State machine enforces forward-only transitions for agents. | Errors escalated to Human Operator; operator resets or cancels task. | In-memory lease tracking (Hermes runtime). |
| **Conversation Management**| `conversations` & `conversation_messages` with human operator RLS policies. | Human Operator logs replies, conducts negotiations; runtime assists with classification. | Inbound webhook parser and intent prompt. |

---

## 5. Summary of End-to-End Operational Workflows

The operational design models five sequenced workflows:

1. **Workflow 1: Prospect Discovery & Registration:**
   `Directive → Discovery Agent → Normalized Domain / Name Deduplication → Human Review → Company Ingestion → Research Task Provisioning`
2. **Workflow 2: Company Research:**
   `Task Assignment → Research Agent → Multi-Vector Investigation → Reports & Findings Persistence (Proposed Contacts Emitted) → Task Completion`
3. **Workflow 3: Lead Qualification:**
   `Research Complete → Qualification Task → 7-Dimension 100-Point Scoring → Draft Scorecard → Human Operator Audit → Approved / Sync Company Stage`
4. **Workflow 4: Outreach Preparation:**
   `Approved Company → Outreach Task → Contact & Suppression Check → Evidence-Backed Personalization → Draft Message → Human Operator Approval Queue`
5. **Workflow 5: Inbound Conversation Handling:**
   `Inbound Reply → Message Persistence → Runtime Intent / Objection Classification → Human Direct Negotiation → Closed Lost / Closed Won`

---

## 6. Analysis of Database Task Limitations & Three-Layer Architecture

During the design of the task execution protocol, six specific schema limitations of the existing `tasks` table were analyzed:
1. Absence of `input_payload` JSONB
2. Absence of `output_payload` JSONB
3. Absence of `retry_count` integer
4. Absence of `lease_expires_at` timestamp
5. Absence of `idempotency_key` unique text
6. Absence of `error_log` / execution trace text

### The Three-Layer Resolution Strategy
Rather than prematurely refactoring the database schema, Phase 2.2 adopts a pragmatic three-layer architecture:
- **Layer 1 (Database Enforced):** The existing PostgreSQL schema reliably enforces task ownership, assignment identity, status progression, and foreign key boundaries. Output deliverables are cleanly stored in their authoritative domain tables (`research_reports`, `lead_qualifications`, `outreach_messages`). Worker leases and retries are explicitly documented as NOT implemented at the database level.
- **Layer 2 (Runtime Adapter Layer):** Under Antigravity, the interactive human operator supervises execution and handles failures. Under Hermes, the runtime manages idempotency, worker execution leases, retry counters, and transient error logs in application memory.
- **Layer 3 (Future Schema Extension):** A clean, non-breaking schema migration is documented as a future option for Phase 2.3 or high-scale production, adding optional execution metadata columns without disrupting existing logic.

---

## 7. Registry of Unresolved Decisions & TBD Items

In strict adherence to truthfulness guidelines, all business parameters that remain unverified or awaiting leadership determination are explicitly logged:

| Domain | Unresolved Parameter / Item | Current Status | Operational Implication for Agents |
| :--- | :--- | :---: | :--- |
| **Commercial Policy** | Wholesale Hourly / Blended Rate Cards | `TBD` | Agents must never quote prices or suggest hourly rates. |
| **Commercial Policy** | Monthly Reserved Capacity Retainer Tiers | `TBD` | Capacity retainers cannot be offered during cold outreach. |
| **Commercial Policy** | Minimum Project Budget Floor | `TBD` | Qualifier scores commercial fit conservatively based on inferred agency positioning. |
| **Commercial Policy** | Standard Deposit Percentage | `TBD` | Required deposit must be confirmed by human operations prior to project kickoff. |
| **Commercial Policy** | Warranty & Bug-Fix Period Duration | `TBD` | Default warranty timeline subject to human contract execution. |
| **Targeting** | Geographic Targeting Boundaries | `TBD` | Orchestrator applies operator-specified geographic filters dynamically per campaign. |
| **Delivery Model** | Technical Delivery Team Onboarding Date | `TBD` | Agents must never claim the technical team is active or available for immediate delivery. |

---

## 8. Implementation Roadmap for Phase 2.3

With the operational blueprints and agent contracts formally aligned and codified, the path to implementation in Phase 2.3 is clearly mapped:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        PHASE 2.3 ROADMAP                               │
│                                                                        │
│  Step 1: Runtime Setup & Subagent Definition                           │
│          - Define prompt templates matching Agent Contracts            │
│          - Configure JWT authentication with Supabase                  │
│                                                                        │
│  Step 2: Tooling Integration                                           │
│          - Bind Web Search / Scraping tools for Discovery & Researcher │
│          - Bind Supabase Client with STAC RLS claim propagation        │
│                                                                        │
│  Step 3: Workflow Execution Engine                                     │
│          - Implement task dependency scheduling                        │
│          - Implement runtime adapter error handling                    │
│                                                                        │
│  Step 4: Human-in-the-Loop Review Interface                            │
│          - Build or configure operator review queues for scorecards    │
│            and outreach message drafts                                 │
│                                                                        │
│  Step 5: End-to-End Staging Validation                                 │
│          - Execute automated test suite across all 5 workflows in      │
│            a sandboxed staging environment                             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Final Disposition

All requirements for Phase 2.2 have been completely satisfied and verified:
- Logical agent roles, responsibilities, and permissions are defined and grounded in SQL reality.
- Standardized 15-point agent contracts with syntactically valid JSON schemas and aligned enums are documented.
- Five end-to-end operational workflows are detailed with explicit human gates and permission boundaries.
- Task coordination, limitations, runtime decoupling, and failure handling protocols are codified.
- Business foundations, commercial policies, and database security baselines remain fully intact.
- Zero modifications have been made to SQL schemas.

### **PHASE 2.2 — CLOSED AND READY FOR IMPLEMENTATION**
