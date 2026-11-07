# Agent Operating Model — Architecture, Roles & Governance

**Document Version:** 1.0.0  
**Phase:** 2.2 — Agent Operating Model & Workflow Design  
**Target Platform:** Hermes Multi-Agent Orchestration Engine & Supabase PostgreSQL CRM  
**Status:** Canonical Design Specification (Design & Documentation Only)  
**Date:** 2026-11-07  

---

## 1. Architectural Overview & Context

This document establishes the canonical **Agent Operating Model** for the **Managed Development Partner** business. It codifies the operational boundaries, roles, access permissions, human oversight gates, and execution mechanics governing all autonomous agents operating under the Hermes multi-agent orchestration platform.

### 1.1. Business Mission & Pre-Operational Reality
- **Mission:** Serve as a dependable, behind-the-scenes wholesale engineering partner to digital agencies, Webflow studios, and Framer studios that possess active client relationships and recurring software development demand.
- **Operational Stage:** **Pre-Operational Foundation Building**.
  - The planned technical delivery team is **not yet onboarded and not currently available for client delivery**.
  - All automated systems operate in a **research, qualification, drafting, and operational support capacity only**.
  - Agents must **never** claim, imply, or represent that technical capacity is immediately available.
- **Commercial Alignment:** Initial business development focuses exclusively on **Project-Based Wholesale Development** as the low-friction entry model, transitioning to **Monthly Reserved Capacity** once synergy is established. **Strategic Revenue-Sharing** is strictly prohibited in cold outreach.

### 1.2. Three-Tier Architectural Topology

The operational system is strictly partitioned into three decoupled tiers to guarantee security, data integrity, and deterministic execution:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        1. HUMAN DECISION TIER                          │
│   Human Operator / Founder / Leadership                                │
│   - Strategy, Approvals, Contract Execution, Commercial Governance     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Human Review & Dispatch Gates)
┌───────────────────────────────────▼────────────────────────────────────┐
│                    2. APPLICATION ORCHESTRATION TIER                   │
│   Hermes Multi-Agent Orchestrator Runtime                              │
│   - Task Decomposition, State Management, Prompt Chaining, Tool Use    │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│   │  Discovery   │  │  Researcher  │  │  Qualifier   │  │  Drafter   │ │
│   │  (Ext Tools) │  │ (Read/Write) │  │ (Read/Write) │  │(Draft Only)│ │
│   └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘ │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Scoped JWT / RLS Policies)
┌───────────────────────────────────▼────────────────────────────────────┐
│                       3. DATABASE PERSISTENCE TIER                     │
│   Supabase PostgreSQL Engine                                           │
│   - Row Level Security (RLS), Table Constraints, Trigger State Machines│
│   - Immutable Technical Audit Ledger (audit_logs, suppression_registry)│
└────────────────────────────────────────────────────────────────────────┘
```

1. **Database Persistence Tier (Supabase / PostgreSQL):**
   - The authoritative source of truth.
   - Enforces relational integrity, state machine transitions, human approval gates, and column immutability at the engine level via triggers and Row Level Security (RLS).
   - Operates under strict least privilege: unknown roles receive zero access.
2. **Application Orchestration Tier (Hermes Runtime):**
   - The autonomous multi-agent coordination environment.
   - Executes specialized subagents within constrained, ephemeral tool contexts.
   - Connects to Supabase using role-scoped JWT credentials that enforce database-level policies.
3. **Human Decision Tier (Human Operator):**
   - The sole commercial and contractual authority.
   - Retains non-delegable approval gates over candidate company ingestion, qualification validation, outreach dispatch, conversation management, and partnership execution.

---

## 2. Logical Agent Roles & Responsibilities

The operating model defines six distinct actors: one primary orchestrator, four specialized subagents, and the human operator.

### 2.1. Orchestrator (Hermes Core)
- **Role Summary:** The central workflow coordinator responsible for translating business goals into discrete tasks, scheduling subagent execution, and tracking pipeline progression.
- **Core Responsibilities:**
  - Ingest strategic campaign directives from the Human Operator (e.g., "Identify 20 Webflow studios in DACH region").
  - Break directives into sequenced tasks assigned to specialized subagents.
  - Track task dependencies, monitor task status (`pending` → `in_progress` → `completed`), and enforce execution preconditions.
  - Route qualification scorecards and outreach drafts to the Human Operator review queue.
  - Handle retries, manage timeout exceptions, and escalate blocking failures to the Human Operator.
- **Operational Boundaries & Prohibitions:**
  - Cannot bypass human approval gates.
  - Cannot fabricate missing information or override `TBD` parameters.
  - Cannot directly dispatch external messages or make commercial, financial, or delivery commitments.
  - Does not possess direct database-level write policies in the current schema (operates via application logic or trusted backend services).

### 2.2. Discovery Agent
- **Role Summary:** Market identification specialist focused on surfacing candidate agencies and visual development studios matching the Ideal Partner Profile (IPP).
- **Core Responsibilities:**
  - Scan permitted, publicly accessible directories (e.g., Webflow Experts Directory, Framer Made In, Clutch, agency showcases).
  - Extract basic firmographic candidate profiles: company name, primary domain, alternate domains, market segment (Digital Agency, Webflow Studio, Framer Studio), source URL, and public portfolio links.
  - Perform preliminary duplicate checks against known domains and company names.
  - Output structured candidate records for review and ingestion.
- **Operational Boundaries & Prohibitions:**
  - **No Database Write Permissions:** Under the current database schema, the Discovery Agent cannot insert records into `companies` or `company_domains` (RLS policies restrict table INSERT to `is_human_operator()`).
  - Output is strictly emitted as structured candidate payloads to the Human Operator or a future trusted ingestion queue.
  - Must not scrape private, authenticated, or copyright-restricted directories.

### 2.3. Research Agent (`researcher`)
- **Role Summary:** Evidence investigator conducting deep-dive research into registered prospect companies to evaluate technical fit, client roster, and recurring demand.
- **Core Responsibilities:**
  - Investigate the target company assigned via an active task (`tasks.company_id`).
  - Analyze published portfolio projects, case studies, technology stacks, team structure, client profiles, and decision-makers.
  - Produce a structured `research_reports` container.
  - Populate granular `research_findings` with explicit statement categories, confidence levels (`high`, `medium`, `low`, `unverified`), and mandatory source URLs.
  - Explicitly identify and catalog `gaps_and_unknowns` (marked as `TBD`).
  - Identify prospective professional contacts (founders, technical directors, studio leads) for subsequent verification.
- **Operational Boundaries & Prohibitions:**
  - Can only read companies and contacts assigned via active, non-completed tasks (`status IN ('pending', 'in_progress')`).
  - Can only insert `research_reports` and `research_findings` mapped to its assigned company.
  - Cannot invent case studies, estimate unverified revenue, or guess technology stacks.

### 2.4. Qualification Agent (`qualifier`)
- **Role Summary:** Objective evaluation engine that scores researched prospects against the approved seven-dimension, 100-point Ideal Partner Profile (IPP) rubric.
- **Core Responsibilities:**
  - Evaluate researched data from `research_reports` and `research_findings` against the 7 IPP dimensions:
    1. Customer Flow (Max 20 pts)
    2. Development Demand (Max 20 pts)
    3. Repeatability of Orders (Max 15 pts)
    4. Technical Fit (Max 15 pts)
    5. Business Size (Max 10 pts)
    6. Decision-Maker Accessibility (Max 10 pts)
    7. Commercial Fit (Max 10 pts)
  - Verify evidence citations: any positive score (`> 0`) strictly requires verifiable evidence documented in `dimension_evidence`.
  - Calculate `overall_score` (sum of 7 dimensions, 0–100) and assign `qualification_tier` (`tier_1_priority`, `tier_2_prospective`, `tier_3_deferred`, `disqualified`).
  - Identify hard disqualifiers (e.g., zero-client startups, commodity shoppers, lack of scoping maturity).
  - Submit the scorecard as `review_status = 'draft'` for mandatory human operator review.
- **Operational Boundaries & Prohibitions:**
  - Unverified or unknown information receives strictly **zero positive points**.
  - Cannot approve its own qualification scorecards (`review_status = 'approved'` strictly requires `is_human_operator()` with `reviewed_by_human_id = auth.uid()`).
  - Cannot modify company lifecycle stage directly (stage synchronization is automated via database triggers upon human approval).

### 2.5. Outreach Drafter Agent (`drafter`)
- **Role Summary:** Personalization copywriter preparing bespoke, high-relevance outreach correspondence for qualified, human-approved prospects.
- **Core Responsibilities:**
  - Inspect approved qualification scorecards (`review_status = 'approved'`) and linked research findings for assigned companies and contacts.
  - Verify that the target contact is not opted out (`is_opted_out = FALSE`) and not suppressed in `suppression_registry`.
  - Draft highly personalized, professional outreach copy (email, LinkedIn InMail) referencing verifiable portfolio work and explicit technical bottlenecks.
  - Position the engagement strictly under **Project-Based Wholesale Development** as the low-friction entry vehicle.
  - Store draft in `outreach_messages` with `approval_status = 'draft'`, `sending_status = 'draft'`, linking `personalization_evidence_ids` and providing detailed `personalization_rationale`.
- **Operational Boundaries & Prohibitions:**
  - **Zero Sending Authority:** Cannot set `sending_status` to `queued`, `sending`, or `sent` (database constraints strictly block dispatch without human approval).
  - **No Revenue Sharing:** Prohibited from selecting `proposed_commercial_model = 'strategic_revenue_sharing'` (enforced by `chk_outreach_no_revenue_sharing`).
  - Cannot fabricate past client work, partner credentials, or delivery turnaround times.
  - Cannot imply that the planned technical delivery team is already onboarded or immediately available.

### 2.6. Human Operator (Founder / Operations Lead)
- **Role Summary:** The executive decision-maker and commercial owner who governs the pipeline, approves communications, and conducts client-facing interactions.
- **Core Responsibilities:**
  - **Company Ingestion Gate:** Review candidate companies surfaced by Discovery and authorize creation of `companies` and `company_domains` records.
  - **Qualification Review Gate:** Audit agent qualification scorecards; approve, adjust, or reject evaluations; confirm qualification tier.
  - **Outreach Approval Gate:** Review draft outreach copy; inspect evidence citations and commercial positioning; authorize (`approval_status = 'approved'`) or request revisions.
  - **Dispatch Control:** Authorize transmission queueing (`sending_status = 'queued'`) and manage outbound communication channels.
  - **Conversation Management:** Conduct direct commercial correspondence, handle partner objections, negotiate project terms, and record interaction logs in `conversations` and `activities`.
  - **Commercial & Contractual Commitments:** Authorize Scopes of Work (SOWs), quote prices, execute partnership agreements, confirm deposits, and approve project change requests.

---

## 3. Database Security, Auth & Identity Mapping

The database enforces role-based and task-scoped security natively in PostgreSQL using Supabase JWT claims.

### 3.1. Supabase JWT Claims Structure
When an actor authenticates with Supabase, the JWT payload contains identity metadata evaluated by SQL security helper functions:

```json
{
  "sub": "00000000-0000-0000-0000-000000000001",
  "aud": "authenticated",
  "role": "authenticated",
  "app_metadata": {
    "user_role": "human_operator",
    "agent_type": null,
    "agent_identity": null
  }
}
```

- **Human Operators:**
  - `user_role`: `'human_operator'`, `'admin'`, or `'founder'`.
  - Evaluated via `is_human_operator()` and `is_admin_operator()`.
- **Hermes Subagents:**
  - `user_role`: `'agent'`.
  - `agent_type`: `'researcher'`, `'qualifier'`, or `'drafter'`.
  - `agent_identity`: Unique subagent instance identifier (e.g., `'hermes-qualifier-v1'`).
  - Evaluated via `is_agent()`, `is_agent_type(p_type)`, and `current_agent_identity()`.

### 3.2. Scoped Task-Based Access Control (STAC)
Agents do not possess broad table-level read or write privileges. In accordance with Phase 2.1 Batch A hardening, **every agent operation is gated by an assigned, active task**:

$$\text{Access Granted} \iff \exists t \in \text{tasks} : \begin{cases} 
t.\text{assigned\_actor\_type} = \text{'agent'} \\
t.\text{assigned\_actor\_identity} = \text{current\_agent\_identity()} \\
t.\text{status} \in \{\text{'pending'}, \text{'in\_progress'}\} \\
t.\text{company\_id} = \text{target\_company\_id} \\
(t.\text{contact\_id} \text{ IS NULL} \lor t.\text{contact\_id} = \text{target\_contact\_id})
\end{cases}$$

- **Contact Read Isolation:** If a task specifies a `contact_id`, the agent can only read that single contact. Other contacts of the same company remain hidden (`agent_select_contacts`).
- **Campaign Scoping:** Drafter agents can only read campaigns linked to their active task (`agent_select_campaigns`).
- **Suppression Verification:** `is_identifier_suppressed()` verifies that the queried identifier is assigned to the agent's active task context, preventing arbitrary database probing.
- **Delivery Table Ring-Fencing:** Subagents have zero policies on delivery tables (`partnerships`, `projects`, `project_milestones`, `project_change_requests`). RLS defaults to **DENY**.

### 3.3. Identity Gap Analysis (Implemented vs. Required)

| Logical Role | Database `user_role` | Database `agent_type` | RLS Policy Status in `schema.sql` | Operational Reality |
| :--- | :---: | :---: | :---: | :--- |
| **Human Operator** | `human_operator` / `admin` | N/A | Fully implemented (`human_operator_all_*`) | Full operational and administrative authority. |
| **Research Agent** | `agent` | `researcher` | Fully implemented (`agent_*_research_*`) | Scoped to active company tasks. |
| **Qualification Agent** | `agent` | `qualifier` | Fully implemented (`agent_*_lead_qualifications`) | Scoped to active company tasks. |
| **Outreach Drafter** | `agent` | `drafter` | Fully implemented (`agent_*_outreach_messages`) | Scoped to active company, contact, and campaign tasks. |
| **Discovery Agent** | *Unmapped* | *None* | **Missing in Database** (RLS defaults to DENY) | Must operate in application tier or submit payloads via Human Operator. |
| **Orchestrator** | *Unmapped* | *None* | **Missing in Database** (RLS defaults to DENY) | Must execute task assignment via Human Operator JWT or trusted service backend. |

> [!IMPORTANT]
> To preserve the integrity of completed Phase 2.1 Batch A/B security baselines, **no SQL changes are introduced in this design phase**. The Discovery Agent and Orchestrator are treated as application-tier components that interact with the database through authorized Human Operator sessions or trusted ingestion pipelines, until formal schema extension is scheduled.

---

## 4. Comprehensive Agent Responsibility & Permission Matrix

The matrix below maps every actor against permitted database operations, application-level capabilities, prohibitions, and required human approvals:

| Actor | Database Reads (SELECT) | Database Inserts (INSERT) | Database Updates (UPDATE) | Prohibited Actions | Application / Orchestration Role | Required Human Approval | Output Artifacts |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Human Operator** | All tables (`companies`, `contacts`, `tasks`, `research_*`, `lead_qualifications`, `outreach_*`, `conversations`, `activities`, `audit_logs`, `suppression_registry`, delivery tables). | All tables (subject to trigger constraints). | All tables (subject to trigger state machines & immutability guards). | Bypassing audit logging; modifying immutable audit logs; confirming unauthenticated payments. | Executive pipeline director, commercial negotiator, final reviewer. | Self-authorizing for operational tasks; founder sign-off for binding contracts. | Validated companies, approved scorecards, authorized outreach, signed agreements. |
| **Orchestrator (Hermes Core)** | *Application-level:* Reads task queue and pipeline state via authorized session. | *Application-level:* Generates subagent tasks via authorized session. | *Application-level:* Updates task status and dependencies. | Fabricating missing parameters; bypassing human approval gates; making commercial commitments. | Workflow scheduling, task decomposition, dependency tracking, failure retry handling. | Operator must authorize campaign kickoff and task batch creation. | Structured task dependency trees, workflow status logs, escalation alerts. |
| **Discovery Agent** | *External only:* Public directories, websites, showcase registries. | **NONE** (No database INSERT permissions in current schema). | **NONE** (No database UPDATE permissions in current schema). | Direct database mutation; scraping private/authenticated data; making assumptions on missing data. | Domain lookup, firmographic filtering, preliminary duplicate detection. | Operator must review and authorize ingestion into `companies`. | Structured candidate JSON records (company name, domain, segment, evidence). |
| **Research Agent (`researcher`)** | `companies`, `company_domains`, `contacts` (active task-scoped); `research_reports`, `research_findings` (self-authored & task-scoped); `tasks` (assigned). | `research_reports`, `research_findings` (strictly mapped to assigned company). | `tasks` (status progression `pending` → `in_progress` → `completed` only). | Modifying `companies`, `contacts`, or `tasks` scope; fabricating evidence; evaluating qualification scores; delivery access. | Web search, tech stack inspection, portfolio analysis, decision-maker discovery. | None for research execution; report review required if confidence is low. | `research_reports` record, atomic `research_findings` records with evidence URLs. |
| **Qualification Agent (`qualifier`)** | `companies`, `company_domains`, `contacts` (task-scoped); `research_reports`, `research_findings` (task-scoped); `lead_qualifications` (self-authored); `tasks` (assigned). | `lead_qualifications` (strictly mapped to assigned company; `review_status = 'draft'`). | `tasks` (status progression `pending` → `in_progress` → `completed` only). | Setting `review_status = 'approved'`; mutating company lifecycle stage; assigning positive score without evidence; delivery access. | 7-dimension scoring calculation, evidence linking, disqualification identification. | **Mandatory:** Scorecard must be reviewed and approved by Human Operator. | Draft `lead_qualifications` record with detailed `dimension_evidence` JSONB. |
| **Outreach Drafter (`drafter`)** | `companies`, `company_domains`, `contacts` (task-scoped); approved `lead_qualifications`; `outreach_campaigns` (task-scoped); `outreach_messages` (assigned); `tasks` (assigned). | `outreach_messages` (strictly `approval_status = 'draft'`, `sending_status = 'draft'`). | `tasks` (status progression `pending` → `in_progress` → `completed` only). | Setting `sending_status` to dispatch states; proposing revenue sharing; inventing portfolio/pricing; contacting prospects directly. | Evidence-backed personalization copywriting, channel selection, rationale synthesis. | **Mandatory:** Message body, subject, and positioning must be approved by Human Operator. | Draft `outreach_messages` record with `personalization_evidence_ids`. |

---

## 5. Human-in-the-Loop (HITL) Governance Gates

Human oversight is an uncompromised architectural principle. The table below details every operational gate, the required human action, and the system response upon rejection:

| Governance Gate | Trigger / Context | Required Human Action | Database Enforcement Mechanism | Rejection / Revision Procedure |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1: Company Ingestion** | Discovery surfaces candidate prospect. | Review domain, business model, and IPP alignment; authorize database insertion. | Table INSERT on `companies` requires `is_human_operator() = TRUE`. | Candidate rejected; discarded or added to duplicate/negative registry. |
| **Gate 2: Lead Qualification Approval** | Qualifier submits scorecard (`review_status = 'draft'`). | Audit 7-dimension scores and evidence; confirm or adjust tier; endorse qualification. | Trigger `fn_enforce_lead_qualification_rules` requires `is_human_operator()` to set `approved`. Auto-syncs company stage. | Operator sets `review_status = 'rejected'` with reason; scorecard returned to agent for rework or company marked `disqualified`. |
| **Gate 3: Outreach Draft Approval** | Drafter submits personalized message (`approval_status = 'draft'`). | Review copy accuracy, tone, personalization rationale, and commercial positioning. | Trigger `fn_enforce_outreach_human_approval` strictly blocks sending states without `approved_by_human_id`. | Operator sets `revision_requested` (re-draft task assigned) or `rejected` (message cancelled). |
| **Gate 4: Outbound Message Dispatch** | Approved message ready for scheduled transmission. | Confirm channel availability, prospect status, and authorize queueing. | Check constraint `chk_outreach_mandatory_human_gate` blocks `queued`/`sending` unless approved. | Operator halts dispatch; message remains in `draft` or moves to `cancelled`. |
| **Gate 5: Inbound Conversation Handling** | Prospect replies to outreach. | Review inbound message; categorize intent; draft and conduct all negotiation. | `conversations` and `conversation_messages` updates restricted to `is_human_operator()`. | Operator marks conversation `closed_lost` or routes to future follow-up. |
| **Gate 6: Partnership Agreement Sign-Off** | Prospect expresses commercial partnership interest. | Negotiate wholesale terms; verify legal standing; execute written partnership agreement. | Trigger `fn_enforce_partnership_status_transitions` requires `signed_active` agreement to activate. | Relationship terminated or deferred to dormant status. |
| **Gate 7: Technical Feasibility Sign-Off** | Scoped wholesale project submitted by partner. | Technical Lead conducts formal feasibility analysis on scope, stack, and delivery mode. | Trigger `fn_enforce_feasibility_lead_approval` requires authenticated Technical Lead role. | Project marked `rejected_unfeasible`; scope renegotiated with partner. |
| **Gate 8: Project Delivery Authorization** | Project deposit confirmed; scope finalized. | Verify engineering capacity; authorize development commencement. | Trigger `fn_enforce_team_delivery_authorization` requires human sign-off & feasibility approval. | Project held in `deposit_pending` or `scoping`. |

---

## 6. Observability, Auditability & System Boundaries

### 6.1. Immutable Audit Trail
All sensitive state mutations across both CRM and Delivery tables are automatically captured in the database-level `audit_logs` table via `fn_audit_log_capture()`:
- Records table name, record ID, action (`INSERT`, `UPDATE`, `APPROVAL_GRANTED`, etc.), actor type (`human`, `agent`, `system`), actor identity (`auth.uid()` or `current_agent_identity()`), and complete old/new JSON value states.
- The `audit_logs` table is append-only and protected by trigger `trg_enforce_audit_log_immutability`. Manual updates and deletes are impossible.

### 6.2. Operational Non-Negotiables
1. **Zero External Contact:** Agents are strictly sandboxed from network communication with prospective partners or clients. Outbound communications are dispatched exclusively through human-controlled systems.
2. **Zero Financial & Pricing Authority:** Agents cannot calculate binding price quotes, offer discounts, or discuss retainer rates.
3. **Evidence-Based Grounding:** No positive score or outreach claim can exist without a verifiable source citation. If a fact cannot be verified, it must be labeled `TBD`.
4. **Partner Client Protection:** The partner owns the client relationship. Agents and operators must respect strict white-label boundaries and mutual non-circumvention at all times.
