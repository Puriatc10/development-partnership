# Agent Operating Model — Architecture, Roles & Governance

**Document Version:** 2.0.0  
**Phase:** 2.2 — Agent Operating Model & Workflow Design (Contract & Runtime Alignment)  
**Target Platform:** Runtime-Independent (Current Temporary: Antigravity | Future: Hermes) & Supabase PostgreSQL CRM  
**Status:** Canonical Design Specification (Design & Documentation Only)  
**Date:** 2026-11-07  

---

## 1. Architectural Overview & Context

This document establishes the canonical **Agent Operating Model** for the **Managed Development Partner** business. It codifies the operational boundaries, roles, access permissions, human oversight gates, and execution mechanics governing all autonomous and semi-autonomous agent operations.

### 1.1. Business Mission & Pre-Operational Reality
- **Mission:** Serve as a dependable, behind-the-scenes wholesale engineering partner to digital agencies, Webflow studios, and Framer studios that possess active client relationships and recurring software development demand.
- **Operational Stage:** **Pre-Operational Foundation Building**.
  - The planned technical delivery team is **not yet onboarded and not currently available for client delivery**.
  - All automated systems operate in a **research, qualification, drafting, and operational support capacity only**.
  - Agents must **never** claim, imply, or represent that technical capacity is immediately available.
- **Commercial Alignment:** Initial business development focuses exclusively on **Project-Based Wholesale Development** as the low-friction entry model, transitioning to **Monthly Reserved Capacity** once synergy is established. **Strategic Revenue-Sharing** is strictly prohibited in cold outreach.

### 1.2. Three-Part Runtime-Decoupled Topology

To prevent architectural lock-in, the system strictly separates logical business contracts from the underlying execution runtime and database persistence:

```
┌────────────────────────────────────────────────────────────────────────┐
│                    1. LOGICAL AGENT ARCHITECTURE                       │
│   - Runtime-Independent Role Definitions & Responsibility Matrix       │
│   - Deterministic 15-Point Agent Contracts (JSON In/Out Payloads)      │
│   - 5 End-to-End Business Workflows & Mandatory Human Approval Gates   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Pluggable Runtime Interface)
┌───────────────────────────────────▼────────────────────────────────────┐
│                    2. RUNTIME ADAPTER LAYER                            │
│   ┌──────────────────────────────────┐ ┌─────────────────────────────┐ │
│   │ CURRENT TEMPORARY RUNTIME:       │ │ FUTURE TARGET RUNTIME:      │ │
│   │ Antigravity (Interactive)        │ │ Hermes (Autonomous Engine)  │ │
│   │ - Supervised, operator-triggered │ │ - Dedicated background daemon││
│   │ - Ephemeral session execution    │ │ - Project API credentials   │ │
│   │ - No persistent worker daemons   │ │ - Distributed worker leases │ │
│   └──────────────────────────────────┘ └─────────────────────────────┘ │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Scoped JWT / RLS Policies)
┌───────────────────────────────────▼────────────────────────────────────┐
│                 3. DATABASE PERSISTENCE & AUTHORIZATION                │
│   Supabase PostgreSQL Engine                                           │
│   - Row Level Security (RLS), Table Constraints, Trigger State Machines│
│   - Immutable Technical Audit Ledger (audit_logs, suppression_registry)│
└────────────────────────────────────────────────────────────────────────┘
```

1. **Logical Agent Architecture:**
   - Defines agent capabilities, input/output schemas, evidence standards, and workflow progressions.
   - Completely agnostic to whether an agent is executed interactively via Antigravity or autonomously via Hermes.
2. **Runtime Adapter Layer:**
   - **Current Temporary Runtime (Antigravity):** Execution is interactive, operator-initiated, and supervised. It operates within bounded tool sessions without background worker daemons, autonomous cron schedulers, or durable execution leases. The Human Operator directly coordinates task progression.
   - **Future Planned Runtime (Hermes):** When project-specific API credentials and dedicated infrastructure are provisioned, Hermes will serve as the dedicated autonomous orchestration runtime, executing the exact same logical contracts without requiring any database schema or business logic redesign.
3. **Database Persistence & Authorization Tier (Supabase / PostgreSQL):**
   - The authoritative source of truth.
   - Enforces relational integrity, state machine transitions, human approval gates, and column immutability at the engine level via triggers and Row Level Security (RLS).
   - Operates under strict least privilege: unknown roles receive zero access.

---

## 2. Logical Agent Roles & Responsibilities

The operating model defines six distinct actors: one primary orchestrator, four specialized subagents, and the human operator.

### 2.1. Orchestrator
- **Role Summary:** The workflow coordinator responsible for translating business goals into discrete tasks, scheduling subagent execution, and tracking pipeline progression.
- **Core Responsibilities:**
  - Ingest strategic campaign directives from the Human Operator (e.g., "Identify 20 Webflow studios in DACH region").
  - Break directives into sequenced tasks assigned to specialized subagents.
  - Track task dependencies, monitor task status (`pending` → `in_progress` → `completed`), and enforce execution preconditions.
  - Route qualification scorecards and outreach drafts to the Human Operator review queue.
  - Handle retries, manage timeout exceptions, and escalate blocking failures to the Human Operator.
- **Operational Boundaries & Prohibitions:**
  - **Zero Database Authority:** The Orchestrator has **no dedicated database-level agent identity and no independent RLS policies** in `crm/schema.sql`. It cannot directly query or mutate CRM records on its own.
  - Task creation and candidate ingestion must execute through an authenticated **Human Operator session** (`user_role IN ('operator', 'admin', 'founder')`) or a future trusted backend. Service role bypass is strictly prohibited.
  - Cannot bypass human approval gates.
  - Cannot fabricate missing information or override `TBD` parameters.
  - Cannot directly dispatch external messages or make commercial, financial, or delivery commitments.

### 2.2. Discovery Agent
- **Role Summary:** Market identification specialist focused on surfacing candidate agencies and visual development studios matching the Ideal Partner Profile (IPP).
- **Core Responsibilities:**
  - Scan permitted, publicly accessible directories (e.g., Webflow Experts Directory, Framer Made In, Clutch, agency showcases).
  - Extract basic firmographic candidate profiles: company name, primary domain, alternate domains, market segment (`digital_agency`, `webflow_studio`, `framer_studio`), source URL, and public portfolio links.
  - Perform preliminary duplicate checks against known domains and company names.
  - Output structured candidate records for review and ingestion.
- **Operational Boundaries & Prohibitions:**
  - **No Database Write Permissions:** Under `crm/schema.sql`, the Discovery Agent has zero database permissions (RLS defaults to DENY). It cannot write to `companies` or `company_domains`.
  - Output is strictly emitted as structured candidate payloads for Human Operator review and manual/supervised ingestion.
  - Must not scrape private, authenticated, or copyright-restricted directories.

### 2.3. Research Agent (`researcher`)
- **Role Summary:** Evidence investigator conducting deep-dive research into registered prospect companies to evaluate technical fit, client roster, and recurring demand.
- **Core Responsibilities:**
  - Investigate the target company assigned via an active task (`tasks.company_id`).
  - Analyze published portfolio projects, case studies, technology stacks, team structure, client profiles, and decision-makers.
  - Produce a structured `research_reports` container.
  - Populate granular `research_findings` with explicit statement categories matching SQL enums (`business_model`, `client_roster`, `technology_stack`, `scoping_maturity`, `growth_signals`, `pain_points`, `pricing_rates`, `team_structure`, `other`), confidence levels (`high`, `medium`, `low`, `unverified`), and mandatory source URLs.
  - Explicitly identify and catalog `gaps_and_unknowns` (marked as `TBD`).
  - Identify prospective professional contacts (founders, technical directors, studio leads) as **proposed output artifacts** for Human Operator review.
- **Operational Boundaries & Prohibitions:**
  - **Cannot Insert Contacts:** The Researcher has **zero INSERT permissions on `contacts`** (in `crm/schema.sql`, table policy `human_operator_all_contacts` restricts contact creation to human operators). Discovered contacts must be submitted as proposed output data in the research report for human operator review and ingestion.
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
  - **Contact Ingestion Gate:** Review proposed contacts surfaced by Research and create verified `contacts` records in the database.
  - **Task Provisioning:** Create and assign tasks in `tasks` for subagents.
  - **Qualification Review Gate:** Audit agent qualification scorecards; approve, adjust, or reject evaluations; confirm qualification tier.
  - **Outreach Approval Gate:** Review draft outreach copy; inspect evidence citations and commercial positioning; authorize (`approval_status = 'approved'`) or request revisions.
  - **Dispatch Control:** Authorize transmission queueing (`sending_status = 'queued'`) and manage outbound communication channels.
  - **Conversation Management:** Conduct direct commercial correspondence, handle partner objections, negotiate project terms, and record interaction logs in `conversations` and `activities`.
  - **Commercial & Contractual Commitments:** Authorize Scopes of Work (SOWs), quote prices, execute partnership agreements, confirm deposits, and approve project change requests.

---

## 3. Database Security, Auth & Identity Mapping

The database enforces role-based and task-scoped security natively in PostgreSQL using Supabase JWT claims, strictly aligned with the helper functions in [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql).

### 3.1. Supabase JWT Claims Structure (Exact SQL Alignment)
When an actor authenticates with Supabase, the JWT payload contains identity metadata evaluated by SQL security helper functions:

```json
{
  "sub": "00000000-0000-0000-0000-000000000001",
  "aud": "authenticated",
  "role": "authenticated",
  "app_metadata": {
    "user_role": "operator",
    "subagent_type": null,
    "agent_id": null
  }
}
```

- **Human Operators:**
  - `user_role`: must be `'operator'`, `'admin'`, or `'founder'` (evaluated by `is_human_operator()`: `app_metadata ->> 'user_role' IN ('operator', 'admin', 'founder')`).
  - Admin/Founder evaluation: `is_admin_operator()`: `app_metadata ->> 'user_role' IN ('admin', 'founder')`.
  - *(Note: Do NOT use `human_operator` as the claim value; the SQL helper explicitly requires `'operator'`, `'admin'`, or `'founder'`.)*
- **Subagents:**
  - `user_role`: strictly `'agent'` (evaluated by `is_agent()`: `app_metadata ->> 'user_role' = 'agent'`).
  - `subagent_type`: strictly `'researcher'`, `'qualifier'`, or `'drafter'` (evaluated by `is_agent_type(p_type)`: `app_metadata ->> 'subagent_type' = p_type`).
  - `agent_id`: string identifier of the subagent instance (evaluated by `current_agent_identity()`: `COALESCE(app_metadata ->> 'agent_id', auth.jwt() ->> 'sub')`).
  - *(Note: Do NOT use `agent_type` or `agent_identity`; the SQL helper explicitly reads `subagent_type` and `agent_id`.)*

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

| Logical Role | Database `user_role` | Database `subagent_type` | Database `agent_id` | RLS Policy Status in `schema.sql` | Operational Reality |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Human Operator** | `operator` / `admin` / `founder` | N/A | N/A | Fully implemented (`human_operator_all_*`) | Full operational and administrative authority. |
| **Research Agent** | `agent` | `researcher` | `hermes-researcher-v1` | Fully implemented (`agent_*_research_*`) | Scoped to active company tasks. Cannot insert contacts. |
| **Qualification Agent** | `agent` | `qualifier` | `hermes-qualifier-v1` | Fully implemented (`agent_*_lead_qualifications`) | Scoped to active company tasks. |
| **Outreach Drafter** | `agent` | `drafter` | `hermes-drafter-v1` | Fully implemented (`agent_*_outreach_messages`) | Scoped to active company, contact, and campaign tasks. |
| **Discovery Agent** | *Unmapped* | *None* | *None* | **Missing in Database** (RLS defaults to DENY) | Operates strictly in runtime; produces candidate payloads for Operator. |
| **Orchestrator** | *Unmapped* | *None* | *None* | **Missing in Database** (RLS defaults to DENY) | Operates in runtime; task creation executes via Human Operator JWT. |

---

## 4. Comprehensive Agent Responsibility & Permission Matrix

The matrix below maps every actor against permitted database operations, application-level capabilities, prohibitions, and required human approvals:

| Actor | Database Reads (SELECT) | Database Inserts (INSERT) | Database Updates (UPDATE) | Prohibited Actions | Application / Orchestration Role | Required Human Approval | Output Artifacts |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Human Operator** | All tables (`companies`, `contacts`, `tasks`, `research_*`, `lead_qualifications`, `outreach_*`, `conversations`, `activities`, `audit_logs`, `suppression_registry`, delivery tables). | All tables (subject to trigger constraints). | All tables (subject to trigger state machines & immutability guards). | Bypassing audit logging; modifying immutable audit logs; confirming unauthenticated payments. | Executive pipeline director, commercial negotiator, final reviewer. | Self-authorizing for operational tasks; founder sign-off for binding contracts. | Validated companies, approved scorecards, authorized outreach, signed agreements. |
| **Orchestrator** | *Via Human Operator session only.* No independent database identity. | *Via Human Operator session only.* Generates tasks on behalf of human operator. | *Via Human Operator session only.* Manages task progression. | Direct autonomous database mutation without operator session; bypassing human review; making commercial commitments. | Workflow scheduling, task decomposition, dependency tracking, failure escalation. | Operator must authorize campaign kickoff and task batch creation. | Structured task dependency trees, workflow status logs, escalation alerts. |
| **Discovery Agent** | *External only:* Public directories, websites, showcase registries. | **NONE** (No database INSERT permissions in current schema). | **NONE** (No database UPDATE permissions in current schema). | Direct database mutation; scraping private/authenticated data; making assumptions on missing data. | Domain lookup, firmographic filtering, preliminary duplicate detection. | Operator must review and authorize ingestion into `companies`. | Structured candidate JSON records (company name, domain, segment, evidence). |
| **Research Agent (`researcher`)** | `companies`, `company_domains`, `contacts` (active task-scoped); `research_reports`, `research_findings` (self-authored & task-scoped); `tasks` (assigned). | `research_reports`, `research_findings` (strictly mapped to assigned company). | `tasks` (status progression `pending` → `in_progress` → `completed` only). | Inserting into `contacts` (blocked by RLS); modifying company/contact/task scope; fabricating evidence; delivery access. | Web search, tech stack inspection, portfolio analysis, proposed contact discovery. | None for research execution; report review required if confidence is low. | `research_reports` record, atomic `research_findings` records, proposed contact payloads. |
| **Qualification Agent (`qualifier`)** | `companies`, `company_domains`, `contacts` (task-scoped); `research_reports`, `research_findings` (task-scoped); `lead_qualifications` (self-authored); `tasks` (assigned). | `lead_qualifications` (strictly mapped to assigned company; `review_status = 'draft'`). | `tasks` (status progression `pending` → `in_progress` → `completed` only). | Setting `review_status = 'approved'`; mutating company lifecycle stage; assigning positive score without evidence; delivery access. | 7-dimension scoring calculation, evidence linking, disqualification identification. | **Mandatory:** Scorecard must be reviewed and approved by Human Operator. | Draft `lead_qualifications` record with detailed `dimension_evidence` JSONB. |
| **Outreach Drafter (`drafter`)** | `companies`, `company_domains`, `contacts` (task-scoped); approved `lead_qualifications`; `outreach_campaigns` (task-scoped); `outreach_messages` (assigned); `tasks` (assigned). | `outreach_messages` (strictly `approval_status = 'draft'`, `sending_status = 'draft'`). | `tasks` (status progression `pending` → `in_progress` → `completed` only). | Setting `sending_status` to dispatch states; proposing revenue sharing; inventing portfolio/pricing; contacting prospects directly. | Evidence-backed personalization copywriting, channel selection, rationale synthesis. | **Mandatory:** Message body, subject, and positioning must be approved by Human Operator. | Draft `outreach_messages` record with `personalization_evidence_ids`. |

---

## 5. Human-in-the-Loop (HITL) Governance Gates

Human oversight is an uncompromised architectural principle. The table below details every operational gate, the required human action, and the system response upon rejection:

| Governance Gate | Trigger / Context | Required Human Action | Database Enforcement Mechanism | Rejection / Revision Procedure |
| :--- | :--- | :--- | :--- | :--- |
| **Gate 1: Company Ingestion** | Discovery surfaces candidate prospect. | Review domain, business model, and IPP alignment; authorize database insertion. | Table INSERT on `companies` requires `is_human_operator() = TRUE`. | Candidate rejected; discarded or added to duplicate/negative registry. |
| **Gate 2: Contact Ingestion** | Research proposes discovered decision-maker. | Verify identity, role type, and communication channels; insert into `contacts`. | Table INSERT on `contacts` requires `is_human_operator() = TRUE`. | Contact rejected or flagged as invalid; unverified contacts not stored. |
| **Gate 3: Lead Qualification Approval** | Qualifier submits scorecard (`review_status = 'draft'`). | Audit 7-dimension scores and evidence; confirm or adjust tier; endorse qualification. | Trigger `fn_enforce_lead_qualification_rules` requires `is_human_operator()` to set `approved`. Auto-syncs company stage. | Operator sets `review_status = 'rejected'` with reason; scorecard returned to agent for rework or company marked `disqualified`. |
| **Gate 4: Outreach Draft Approval** | Drafter submits personalized message (`approval_status = 'draft'`). | Review copy accuracy, tone, personalization rationale, and commercial positioning. | Trigger `fn_enforce_outreach_human_approval` strictly blocks sending states without `approved_by_human_id`. | Operator sets `revision_requested` (re-draft task assigned) or `rejected` (message cancelled). |
| **Gate 5: Outbound Message Dispatch** | Approved message ready for scheduled transmission. | Confirm channel availability, prospect status, and authorize queueing. | Check constraint `chk_outreach_mandatory_human_gate` blocks `queued`/`sending` unless approved. | Operator halts dispatch; message remains in `draft` or moves to `cancelled`. |
| **Gate 6: Inbound Conversation Handling** | Prospect replies to outreach. | Review inbound message; categorize intent; draft and conduct all negotiation. | `conversations` and `conversation_messages` updates restricted to `is_human_operator()`. | Operator marks conversation `closed_lost` or routes to future follow-up. |
| **Gate 7: Partnership Agreement Sign-Off** | Prospect expresses commercial partnership interest. | Negotiate wholesale terms; verify legal standing; execute written partnership agreement. | Trigger `fn_enforce_partnership_status_transitions` requires `signed_active` agreement to activate. | Relationship terminated or deferred to dormant status. |
| **Gate 8: Technical Feasibility Sign-Off** | Scoped wholesale project submitted by partner. | Technical Lead conducts formal feasibility analysis on scope, stack, and delivery mode. | Trigger `fn_enforce_feasibility_lead_approval` requires authenticated Technical Lead role. | Project marked `rejected_unfeasible`; scope renegotiated with partner. |
| **Gate 9: Project Delivery Authorization** | Project deposit confirmed; scope finalized. | Verify engineering capacity; authorize development commencement. | Trigger `fn_enforce_team_delivery_authorization` requires human sign-off & feasibility approval. | Project held in `deposit_pending` or `scoping`. |

---

## 6. Observability, Auditability & Synthetic Data Standards

### 6.1. Immutable Audit Trail
All sensitive state mutations across both CRM and Delivery tables are automatically captured in the database-level `audit_logs` table via `fn_audit_log_capture()`:
- Records table name, record ID, action (`INSERT`, `UPDATE`, `APPROVAL_GRANTED`, etc.), actor type (`human`, `agent`, `system`), actor identity (`auth.uid()` or `current_agent_identity()`), and complete old/new JSON value states.
- The `audit_logs` table is append-only and protected by trigger `trg_enforce_audit_log_immutability`. Manual updates and deletes are impossible.

### 6.2. Operational Non-Negotiables
1. **Zero External Contact:** Agents are strictly sandboxed from network communication with prospective partners or clients. Outbound communications are dispatched exclusively through human-controlled systems.
2. **Zero Financial & Pricing Authority:** Agents cannot calculate binding price quotes, offer discounts, or discuss retainer rates.
3. **Evidence-Based Grounding:** No positive score or outreach claim can exist without a verifiable source citation. If a fact cannot be verified, it must be labeled `information_gap_tbd`.
4. **Partner Client Protection:** The partner owns the client relationship. Agents and operators must respect strict white-label boundaries and mutual non-circumvention at all times.

### 6.3. Mandatory Synthetic Data Disclosure
> [!NOTE]
> All company names (e.g., *"Apex Visual Studio"*), individual names (e.g., *"Maximilian Weber"*), domains (`apexvisual.de`), URLs, UUIDs, and portfolio project descriptions appearing in this document, in agent contracts, and across workflow specifications are **purely synthetic illustrative examples** designed to demonstrate data structures and schema compatibility. They must **never** be mistaken for actual prospective partners, verified case studies, or live CRM evidence.
