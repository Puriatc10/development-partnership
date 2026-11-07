# Phase 2.2: Agent Operating Model & Workflow Design — Final Report

**Document Version:** 1.0.0  
**Phase:** 2.2 — Agent Operating Model & Workflow Design  
**Workspace:** `development-partnership/`  
**System Role:** Principal Multi-Agent Systems Architect, Database Security Auditor & Workflow Engineer  
**Target Platform:** Hermes Multi-Agent Orchestration Platform & Supabase PostgreSQL CRM  
**Status:** Complete — Fully Validated Locally (Design-Only Mode) — **PHASE 2.2 — READY FOR IMPLEMENTATION**  
**Date:** 2026-11-07  

---

## 1. Executive Summary & Operational Context

This report provides the definitive architectural blueprint and closure summary for **Phase 2.2: Agent Operating Model & Workflow Design** for the Managed Development Partnership business.

Phase 2.2 establishes the operational contracts, multi-agent workflows, data exchange schemas, and human oversight boundaries that will eventually connect the Hermes multi-agent orchestration runtime to the Supabase CRM database.

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

Phase 2.2 has produced four comprehensive operational specifications in the `agents/` directory:

| Document | File Path | Core Contents & Purpose |
| :--- | :--- | :--- |
| **Agent Operating Model** | [`agents/agent-operating-model.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/agent-operating-model.md) | Architectural topology, logical agent roles, database security & JWT identity mapping, complete Responsibility & Permission Matrix, human governance gates, and operational non-negotiables. |
| **Standard Agent Contracts** | [`agents/agent-contracts.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/agent-contracts.md) | Standardized 15-point contracts for Orchestrator, Discovery, Researcher, Qualifier, and Drafter agents, featuring illustrative, valid JSON input and output payloads using stable database primary keys. |
| **Workflow Specifications** | [`agents/workflow-specifications.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/workflow-specifications.md) | Step-by-step specifications for 5 core workflows (Prospect Discovery, Company Research, Lead Qualification, Outreach Preparation, and Inbound Conversation Handling) with flowcharts and human gates. |
| **Task Execution Protocol** | [`agents/task-execution-protocol.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/task-execution-protocol.md) | Task coordination protocol grounded in the existing PostgreSQL `tasks` table, lifecycle state machines, dependency management, analysis of database task limitations, and 3-layer architecture resolution. |

---

## 3. Architecture Decisions & Operational Design Rationale

### 3.1. Three-Tier Decoupled Topology
The architecture partitions responsibilities across three decoupled layers:
1. **Human Decision Tier:** Holds exclusive commercial, pricing, contractual, and communication authority.
2. **Application Orchestration Tier (Hermes):** Coordinates subagents, manages task queues, tracks in-memory leases and retries, and enforces prompt boundaries.
3. **Database Persistence Tier (Supabase):** The canonical source of truth. Enforces Row Level Security (RLS), referential integrity, immutability, and state transitions at the SQL engine level.

### 3.2. Grounding in Database Security Baselines
Agent permissions documented in this phase are strictly derived from the actual PostgreSQL RLS policies in [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql):
- **Implemented Subagent Types:** Only three subagent types are recognized by database RLS: `'researcher'`, `'qualifier'`, and `'drafter'`.
- **Scoped Task-Based Access Control (STAC):** Subagents can only read or write data associated with active tasks assigned specifically to their authenticated agent identity (`current_agent_identity()`).
- **Contact Read Isolation:** An agent assigned to a contact-specific task is restricted from viewing other contacts belonging to the same company.
- **Zero Delivery Access:** Subagents have zero access to commercial and delivery tables (`partnerships`, `projects`, `project_milestones`, `project_change_requests`). RLS defaults to **DENY**.

---

## 4. Capability Matrix: Implemented vs. Proposed vs. Future Dependencies

The matrix below transparently delineates current database capabilities, proposed application-level behavior, and future implementation requirements:

| Domain / Capability | Implemented Database Capability (Phase 2.1) | Proposed Operational Behavior (Phase 2.2) | Future Implementation Dependency (Phase 2.3+) |
| :--- | :--- | :--- | :--- |
| **Prospect Discovery** | Table `companies` exists; INSERT restricted to `is_human_operator()`. | Discovery Agent searches public directories; produces candidate JSON payloads. | Trusted ingestion pipeline or dedicated operator ingestion UI. |
| **Company Registration** | Handled via authenticated human operator session in Supabase. | Operator reviews discovery candidates and authorizes creation of `companies` and `domains`. | Ingestion API endpoint or webhook receiver. |
| **Company Research** | `research_reports` and `research_findings` tables with STAC RLS policies. | Researcher subagent conducts web research and persists findings with source URLs. | Hermes subagent tool bindings (Perplexity / Search API / Firecrawl). |
| **Lead Qualification** | `lead_qualifications` table with 7-dimension check constraints and human gate trigger. | Qualifier evaluates findings, computes 0–100 score, assigns tier, submits draft. | Hermes subagent execution script with strict scoring prompt templates. |
| **Outreach Drafting** | `outreach_messages` with human gate trigger, composite FKs, and rev-share block. | Drafter writes personalized copy referencing verified portfolio work; submits draft. | Hermes subagent drafting templates and personalization validation. |
| **Outreach Dispatch** | Triggers and check constraints strictly block dispatch without human sign-off. | Human Operator reviews, edits, and explicitly authorizes message queueing. | Integration with transactional email/InMail sending service. |
| **Task Management** | `tasks` table with status progression triggers, completed_at sync, and STAC RLS. | Orchestrator creates and sequences tasks, monitoring completion state. | Hermes task queue poller / scheduler runtime. |
| **Task Metadata (Input/Output)**| No dedicated JSONB payload columns on `tasks`. | Inputs mapped via foreign keys; outputs persisted in dedicated domain tables. | Optional future schema migration adding `input_payload`, `idempotency_key`. |
| **Conversation Management**| `conversations` & `conversation_messages` with human operator RLS policies. | Human Operator logs replies, conducts negotiations; Hermes assists with classification. | Inbound email webhook parser and sentiment classification prompt. |

---

## 5. Summary of End-to-End Operational Workflows

The operational design models five sequenced workflows:

1. **Workflow 1: Prospect Discovery & Registration:**
   `Directive → Discovery Agent → Normalized Domain / Name Deduplication → Human Review → Company Ingestion → Research Task Provisioning`
2. **Workflow 2: Company Research:**
   `Task Assignment → Research Agent → Multi-Vector Investigation → Reports & Findings Persistence → Task Completion`
3. **Workflow 3: Lead Qualification:**
   `Research Complete → Qualification Task → 7-Dimension 100-Point Scoring → Draft Scorecard → Human Operator Audit → Approved / Sync Company Stage`
4. **Workflow 4: Outreach Preparation:**
   `Approved Company → Outreach Task → Contact & Suppression Check → Evidence-Backed Personalization → Draft Message → Human Operator Approval Queue`
5. **Workflow 5: Inbound Conversation Handling:**
   `Inbound Reply → Message Persistence → Hermes Intent / Objection Classification → Human Direct Negotiation → Closed Lost / Closed Won`

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
- **Layer 1 (Database Enforced):** The existing PostgreSQL schema reliably enforces task ownership, assignment identity, status progression, and foreign key boundaries. Output deliverables are cleanly stored in their authoritative domain tables (`research_reports`, `lead_qualifications`, `outreach_messages`).
- **Layer 2 (Application Runtime):** The Hermes orchestrator manages idempotency, worker execution leases, retry counters, and transient error logs in application memory and runtime telemetry.
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

With the operational blueprints and agent contracts formally codified, the path to implementation in Phase 2.3 is clearly mapped:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        PHASE 2.3 ROADMAP                               │
│                                                                        │
│  Step 1: Hermes Runtime Setup & Subagent Definition                    │
│          - Define prompt templates matching Agent Contracts            │
│          - Configure JWT authentication with Supabase                  │
│                                                                        │
│  Step 2: Tooling Integration                                           │
│          - Bind Web Search / Scraping tools for Discovery & Researcher │
│          - Bind Supabase Client with STAC RLS claim propagation        │
│                                                                        │
│  Step 3: Orchestrator Workflow Engine                                  │
│          - Implement task dependency scheduling                        │
│          - Implement application-level leases and retry logic          │
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

All requirements for Phase 2.2 have been satisfied:
- Logical agent roles, responsibilities, and permissions are defined and grounded in SQL reality.
- Standardized 15-point agent contracts with valid JSON schemas are documented.
- Five end-to-end operational workflows are detailed with explicit human gates.
- Task coordination, limitations, and failure handling protocols are codified.
- Business foundations, commercial policies, and security baselines remain fully intact.

### **PHASE 2.2 — READY FOR IMPLEMENTATION**
