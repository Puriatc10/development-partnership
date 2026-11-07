# End-to-End Operational Workflows — Pipeline Specifications

**Document Version:** 2.0.0  
**Phase:** 2.2 — Final Contract & Runtime Alignment  
**Target Platform:** Logical Multi-Agent Architecture (Antigravity Interactive Supervised Runtime [Temporary] / Hermes Autonomous Runtime [Future]) & Supabase PostgreSQL CRM  
**Status:** Canonical Design Specification (Design & Documentation Only)  
**Date:** 2026-11-07  

---

> [!NOTE]
> **Synthetic Data Disclosure:** All agency names (e.g., "Apex Visual Studio", "Horizon Digital"), personal identities (e.g., "Sarah Jenkins"), email addresses, web domains, and project portfolio references used in this document are strictly fictional, synthetic examples designed solely for workflow modeling, data-flow illustration, and schema validation.

---

## 1. Operational Workflow Architecture

This document defines the five core operational workflows governing business development, partner acquisition, and pipeline management. Each workflow establishes strict entry preconditions, deterministic agent execution sequences, mandatory human approval gates, database state synchronization, and exception handling paths.

### 1.1. Three-Tier Runtime Decoupled Architecture

To ensure operational viability under current constraints while preserving long-term architectural integrity, workflows are decoupled across three distinct tiers:

1. **Tier 1: Logical Agent Architecture:**
   - Defines the five standard pipeline workflows, execution sequences, state dependencies, and mandatory human review gates.
   - Remains completely invariant regardless of the underlying orchestration engine.
2. **Tier 2: Runtime Adapter Layer:**
   - **Temporary Supervised Runtime (Antigravity):** The current interactive runtime environment. Workflows execute interactively under direct Human Operator supervision. Subagents are triggered on-demand via slash commands or agent prompts. There are no background daemons, worker leases, or autonomous polling loops. Task generation, failure recovery, and stage synchronization are mediated by the operator.
   - **Target Autonomous Runtime (Hermes):** The future production runtime. An autonomous multi-agent orchestration engine that will execute task queues, manage in-memory worker leases, and trigger scheduled workflows when project API credentials and hosting infrastructure become available.
3. **Tier 3: Database Persistence & Authorization (Supabase PostgreSQL):**
   - The authoritative source of truth. Enforces Row Level Security (RLS), state machine integrity triggers, composite foreign keys, and role-based access controls at the SQL engine level.
   - Identical database rules, RLS policies, and tables apply whether driven interactively via Antigravity or autonomously via Hermes.

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                              PIPELINE PROGRESSION                                 │
│                                                                                   │
│  [Workflow 1]        [Workflow 2]         [Workflow 3]         [Workflow 4]       │
│  PROSPECT       ──►  COMPANY        ──►   LEAD           ──►   OUTREACH           │
│  DISCOVERY           RESEARCH             QUALIFICATION        PREPARATION        │
│  (Discovery Agent)   (Research Agent)     (Qualifier Agent)    (Drafter Agent)    │
│       │                   │                    │                    │             │
│  [Human Gate 1]           │               [Human Gate 2]       [Human Gate 3]     │
│  Company Ingestion        │               Scorecard Approval   Draft Approval     │
│                           │                    │                    │             │
│                           ▼                    ▼                    ▼             │
│                      companies:           companies:           companies:         │
│                      'researching'        'qualified'          'outreach_queued'  │
│                                                                     │             │
│                                                               [Human Gate 4]      │
│                                                               Dispatch Ready      │
│                                                                     │             │
│                                                                     ▼             │
│                                                               [Workflow 5]        │
│                                                               INBOUND CONVERSATION│
│                                                               (Human Operator)    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Workflow 1: Prospect Discovery & Registration

### 2.1. Objective & Flow
Discover and filter candidate agencies and studios matching the Ideal Partner Profile (IPP) across approved public sources, eliminate duplicate entities, and present verified candidates to the Human Operator for database registration.

```
[Campaign Directive]
       │
       ▼
[Discovery Agent: Search & Extract]
       │
       ▼
[Candidate Payload Generated]
       │
       ▼
[Duplicate Check (Domain & Name Matching)]
      ├── If Duplicate ───────────────► [Flag as Duplicate & Discard]
      └── If Unique
             │
             ▼
      [Human Operator Review]
            ├── If Rejected ──────────► [Log Rejection & Archive Candidate]
            └── If Approved
                   │
                   ▼
      [Insert into companies & company_domains]
                   │
                   ▼
      [Lifecycle Stage: 'identified' → 'researching']
                   │
                   ▼
      [Generate Task: 'company_research' assigned to Research Agent]
```

### 2.2. Candidate Sources & Discovery Guidelines
- **Approved Primary Sources:**
  - *Webflow Ecosystem:* Webflow Experts Directory (`experts.webflow.com`), Webflow Showcase Agency Profiles.
  - *Framer Ecosystem:* Framer Partner Directory (`framer.com/partners`), Framer Made In Gallery.
  - *Digital Agencies:* Clutch Directory (filtered for 5–50 employees, creative/marketing), Dribbble Studio Profiles, Behance Agency Teams.
- **Prohibited Sources:** Unofficial email scrapers, paid bulk lead lists, LinkedIn scraping tools that violate platform terms, private community directories.

### 2.3. Duplicate Detection Protocol
Before presenting a candidate to the Human Operator, a two-tier duplicate check is executed:
1. **Primary Domain Match:** Exact lookup against `company_domains.domain` and `companies.primary_domain`.
2. **Normalized Name Match:** Case-insensitive, punctuation-stripped comparison against `companies.name` (e.g., `"Apex Visual GmbH"` matches `"Apex Visual Studio"`).
3. **Resolution:** If a duplicate is detected, the candidate is discarded with a duplicate log reference.

### 2.4. Company Registration & Authorization
- **Write Permission Reality:** Discovery agents operate strictly in-memory or produce candidate output files; they have zero direct database write permissions. Subagents have zero INSERT privileges on `companies` or `company_domains`. 
- **Registration Execution:** Registration is performed exclusively by an authenticated **Human Operator** (JWT claim `user_role IN ('operator', 'admin', 'founder')`) or a trusted ingestion pipeline executing under operator claims.
- **Mandatory Ingestion Fields:**
  - `name`: Official commercial name.
  - `primary_domain`: Normalized domain (validated by regex `chk_companies_primary_domain_format`).
  - `market_segment`: `digital_agency`, `webflow_studio`, or `framer_studio`.
  - `lifecycle_stage`: Initialized to `identified`.
- **Handoff Trigger:** Upon registration, the company stage transitions to `researching`, and an active task is created:
  - `task_type`: `'company_research'`
  - `assigned_actor_type`: `'agent'`
  - `assigned_actor_identity`: `'hermes-researcher-v1'` (or runtime agent identifier)
  - `company_id`: UUID of the registered company.
  - **Database Authorization Note:** Because table INSERT on `tasks` is restricted by RLS to `is_human_operator() = TRUE`, task creation is executed via the authenticated Human Operator session (or trusted backend under operator claims). The Orchestrator has NO dedicated database agent identity and NO RLS write policies.

---

## 3. Workflow 2: Comprehensive Company Research

### 3.1. Objective & Flow
Conduct structured, evidence-backed research on a registered company to identify technology stacks, service offerings, client rosters, recurring development demand, and key decision-makers.

```
[Task Assigned: company_research]
       │
       ▼
[Research Agent Transitions Task to 'in_progress']
       │
       ▼
[Multi-Vector Investigation]
 ├── Services & Positioning (Website, Case Studies)
 ├── Technology Stack (Frontend, CMS, APIs, Builders)
 ├── Client Roster & Portfolio (Brand names, project complexity)
 ├── Team Scale & Key Decision-Makers (Proposed Contacts)
 └── Engineering Bottlenecks & Gaps
       │
       ▼
[Synthesize Research Report & Atomic Findings]
       │
       ▼
[Insert research_reports & research_findings]
       │
       ▼
[Log Activity in activities table]
       │
       ▼
[Update Task to 'completed']
       │
       ▼
[Orchestrator Evaluates Completion Criteria]
      ├── If Insufficient / Incomplete ──► [Escalate to Human Operator]
      └── If Validated ──────────────────► [Generate Lead Qualification Task]
```

### 3.2. Research Execution Checklist
Every research session must systematically investigate and answer five operational questions:
1. **Service Focus:** Does the agency build custom web applications, or are they strictly focused on brand design, visual identity, or content marketing?
2. **Technology Ecosystem:** Does the studio build primarily in Webflow, Framer, React, or full-stack code? What styling systems and visual tools are standard?
3. **Client Quality & Demand:** Are their clients legitimate corporate/B2B entities with ongoing development budgets, or early-stage startups seeking cheap MVPs?
4. **Engineering Bottlenecks:** Do their projects exhibit complex functional requirements (authentication, dynamic external databases, API integrations) that exceed visual builder capabilities?
5. **Decision-Maker Accessibility:** Who owns technical partnership decisions (Founder, Co-Founder, Technical Director, Studio Lead)? Are their professional profiles accessible?

### 3.3. Evidence Standards & PostgreSQL Enum Alignment
To maintain database integrity, all persisted research data must strictly adhere to database enum definitions:
- **Finding Categories (`finding_category_type`):** Every finding must be classified using the canonical PostgreSQL enum:
  - `'business_model'`
  - `'client_roster'`
  - `'technology_stack'`
  - `'scoping_maturity'`
  - `'growth_signals'`
  - `'pain_points'`
  - `'pricing_rates'`
  - `'team_structure'`
  - `'other'`
  *(Deprecated strings like `'client_portfolio'`, `'decision_maker'`, `'recurring_demand'`, or `'commercial_model'` are rejected by database enum constraints.)*
- **Finding Types (`research_finding_type`):**
  - `'verified_fact'`: Direct first-party claims (agency website, case studies, client reviews) with `high` confidence, accompanied by exact source URL and excerpt.
  - `'hypothesis'`: Inferred observations based on indirect signals, with `medium` or `low` confidence. (Strings such as `'inferred_signal'` or `'unverified_claim'` are invalid.)
  - `'information_gap_tbd'`: Crucial parameters that could not be determined from public evidence.
- **Handling Unknowns:** If an operational parameter (e.g., client budget floor, backend hosting provider) cannot be verified from public sources, it must be explicitly cataloged in `gaps_and_unknowns` as **`TBD`** or recorded as a finding of type `'information_gap_tbd'`. Guessing or filling assumptions is strictly forbidden.

### 3.4. Research Completion Criteria & Contact Handling Boundary
- **Database Permission Boundary on Contacts:** The `contacts` table enforces RLS policy `human_operator_all_contacts` (which grants INSERT/UPDATE/DELETE exclusively to human operators) and `agent_select_contacts` (read-only for agents). The Research Agent **CANNOT insert or update records in `contacts`**. Discovered contacts (e.g., founder/technical lead name, title, profile URL, role) are proposed output artifacts emitted within the research findings / report payload for Human Operator review and ingestion.
- A research task is considered complete when:
  - A `research_reports` record is persisted with non-null `summary`, `recommended_angle`, and cataloged `gaps_and_unknowns`.
  - At least three `research_findings` records are persisted with valid source citations and canonical PostgreSQL enums.
  - At least one proposed decision-maker contact is documented in the findings/report payload for operator ingestion.
  - An activity record is logged in `activities` table.
  - The task status is transitioned to `completed` in `tasks`.

---

## 4. Workflow 3: Objective Lead Qualification

### 4.1. Objective & Flow
Evaluate the researched prospect against the approved seven-dimension, 100-point Ideal Partner Profile (IPP) model, generate an objective scorecard, detect any disqualification triggers, and submit the draft scorecard for mandatory human operator audit.

```
[Research Task Completed]
       │
       ▼
[Orchestrator Generates Task: 'lead_qualification']
 (Executed via Human Operator session / trusted pipeline)
       │
       ▼
[Qualifier Agent Transitions Task to 'in_progress']
       │
       ▼
[Evaluate 7 Dimensions (0–100 Points Total)]
 ├── 1. Customer Flow (Max 20)
 ├── 2. Development Demand (Max 20)
 ├── 3. Repeatability of Orders (Max 15)
 ├── 4. Technical Fit (Max 15)
 ├── 5. Business Size (Max 10)
 ├── 6. Decision-Maker Accessibility (Max 10)
 └── 7. Commercial Fit (Max 10)
       │
       ▼
[Detect Disqualification Triggers]
      ├── If Hard Disqualifier Found ──► [Assign Tier: 'disqualified', overall_score < 40]
      └── If Qualified ────────────────► [Calculate Tier: Tier 1, 2, or 3]
             │
             ▼
[Insert lead_qualifications (review_status = 'draft')]
             │
             ▼
[Update Task to 'completed']
             │
             ▼
[Human Operator Audit Queue]
      ├── If Operator Rejects ────────► [review_status = 'rejected'; Return to Drafter/Archive]
      └── If Operator Approves ───────► [review_status = 'approved']
             │
             ▼
[Trigger fn_sync_company_qualification Auto-Syncs Company]
 ├── If Tier = 'disqualified' ────────► [companies.lifecycle_stage = 'disqualified']
 └── If Tier IN ('tier_1', 'tier_2') ──► [companies.lifecycle_stage = 'qualified']
```

### 4.2. Seven-Dimension Scoring Rubric & Weightings

| Dimension | Max Points | Evaluation Focus & Scoring Guidance | Zero-Point Condition |
| :--- | :---: | :--- | :--- |
| **1. Customer Flow** | **20** | Active roster of paying corporate/B2B clients; published quarterly case studies; established market reputation. | Zero active clients; speculative startup; unverified client flow. |
| **2. Development Demand** | **20** | Demonstrable recurring need for custom software, backend logic, external databases, API integrations beyond visual builder limits. | Pure brand/graphic design studio; static brochure websites only. |
| **3. Repeatability of Orders** | **15** | Evidence of multi-month client retainers, continuous platform maintenance, recurring campaign rollouts. | Strictly one-off transactional project gigs; no retainer history. |
| **4. Technical Fit** | **15** | Clean architectural standards (e.g., Webflow Client-First, componentized Framer builds, structured Figma handoffs). | Chaotic legacy builds; non-standard proprietary systems. |
| **5. Business Size** | **10** | Studio team scale between 5–50 employees; sustainable operational infrastructure. | Solo freelancer with zero leverage; enterprise agency with massive internal engineering teams. |
| **6. Decision-Maker Accessibility** | **10** | Direct accessibility to Founders, Co-Founders, Managing Directors, or Creative Tech Leads. | Bureaucratic enterprise procurement layers; hidden ownership. |
| **7. Commercial Fit** | **10** | Verifiable premium market positioning; alignment with wholesale behind-the-scenes delivery model. | Bargain commodity shopper; demanding unpaid specs. |

> [!IMPORTANT]
> **The Zero-Evidence Rule:** Any dimension with unverified or unknown parameters must receive **zero positive points**. Guessing or assigning default points is prohibited by database trigger `fn_enforce_lead_qualification_rules`.

### 4.3. Disqualification Handling
The Qualifier must immediately disqualify any prospect exhibiting:
- Zero active clients or demand for speculative/unpaid development builds.
- Commodity price shopping behavior (demanding lowest offshore rates).
- Refusal to operate under written Scopes of Work (SOWs) or change request procedures.
- Questionable commercial integrity or refusal to agree to mutual non-circumvention.
- Disqualified prospects are tagged with `qualification_tier = 'disqualified'` and specific `disqualification_reasons`. Upon operator endorsement, database triggers transition the company to `lifecycle_stage = 'disqualified'`, permanently freezing downstream outreach.

### 4.4. Human Review & Lifecycle Synchronization
- **Database Gate:** Agents are blocked from setting `review_status = 'approved'`. Only an authenticated Human Operator can approve a scorecard (`is_human_operator() = TRUE`).
- **Automated Lifecycle Synchronization:** When the Human Operator approves a scorecard, database trigger `fn_sync_company_qualification()` updates `companies`:
  - `overall_score >= 60` and no disqualifiers $\implies$ `companies.qualification_tier` set and `lifecycle_stage = 'qualified'`.
  - Advanced lifecycle stages (`active_conversation`, `negotiating`, `partner_active`) are preserved and protected from regression.

---

## 5. Workflow 4: Evidence-Based Outreach Preparation

### 5.1. Objective & Flow
Prepare highly personalized, evidence-grounded draft outreach messages for qualified prospects, submit them to the human approval queue, and hold until the Human Operator explicitly authorizes dispatch.

```
[Company in 'qualified' Stage]
       │
       ▼
[Human Operator Assigns Task: 'outreach_drafting']
       │
       ▼
[Drafter Agent Transitions Task to 'in_progress']
       │
       ▼
[Recipient & Suppression Verification]
 ├── Verify contact is active & not opted out (is_opted_out = FALSE)
 └── Verify email/LinkedIn not in suppression_registry
       │
       ▼
[Craft Personalized Message Copy]
 ├── Hook: Direct reference to verified portfolio project
 ├── Problem: Webflow/Framer visual limits vs backend engineering
 ├── Solution: Behind-the-scenes wholesale engineering partner
 └── Low-Friction CTA: 15-minute sync on pilot project
       │
       ▼
[Insert outreach_messages (approval_status = 'draft', sending_status = 'draft')]
       │
       ▼
[Update Task to 'completed']
       │
       ▼
[Human Operator Approval Queue]
      ├── If Operator Rejects ───────► [approval_status = 'rejected', sending_status = 'cancelled']
      ├── If Revision Requested ────► [approval_status = 'revision_requested'; Re-draft Task]
      └── If Operator Approves ──────► [approval_status = 'approved']
             │
             ▼
[Human Operator Schedules / Authorizes Dispatch]
 ├── sending_status set to 'queued'
 └── Handed off to human-controlled email/InMail delivery system
```

### 5.2. Personalization & Grounding Standards
Every draft outreach message must satisfy four structural criteria:
1. **Specific Project Reference:** Must cite an actual, publicly verifiable portfolio project completed by the agency within the last 12 months.
2. **Technical Bottleneck Framing:** Must articulate the specific technical challenge associated with that project type (e.g., member portals, database connectivity, third-party API syncing).
3. **Wholesale Model Positioning:** Must explicitly present our service as behind-the-scenes wholesale engineering capacity working under their brand.
4. **Project-Based Low-Risk CTA:** Must propose a fixed-scope pilot project to prove reliability. Proposing monthly capacity retainers or revenue sharing during cold outreach is strictly prohibited.

### 5.3. Suppression & Opt-Out Enforcement
Before generating a message draft:
- The Drafter queries `is_identifier_suppressed(target_type, identifier)`.
- If suppressed or opted out, task execution halts immediately, and the task is marked `completed` with a suppression note (or operator cancels it).
- Database table constraints and composite foreign keys (`fk_outreach_contact_company`) prevent drafting messages for contacts belonging to mismatched companies.

### 5.4. Human Approval Gate
- **Strict Human Dispatch Gate:** Database check constraint `chk_outreach_mandatory_human_gate` and trigger `fn_enforce_outreach_human_approval` strictly prevent `sending_status` from entering `queued`, `sending`, or `sent` unless `approval_status = 'approved'` and `approved_by_human_id = auth.uid()`.
- **Actual Transmission:** Actual message transmission is executed outside the agent tier by human-controlled sending systems or supervised dispatch workers.

---

## 6. Workflow 5: Inbound Conversation & Objection Handling

### 6.1. Objective & Flow
Capture inbound partner replies, record communication history in the CRM, categorize partner intent and objections, generate recommended follow-up actions, and support the Human Operator in commercial discussions.

```
[Inbound Partner Reply Received]
       │
       ▼
[Human Operator Records Inbound Message in conversation_messages]
       │
       ▼
[System / Trigger Updates conversations Table]
 ├── Set status = 'active'
 └── Record latest message timestamp
       │
       ▼
[Hermes Assistant Evaluates Inbound Reply]
 ├── Classify Partner Intent (Positive Interest, Technical Question, Not Interested, Timing Objection)
 ├── Identify Stated Objections (Pricing concerns, Past bad freelance experience, Current vendor)
 └── Synthesize Recommended Next Action & Objection Handling Notes
       │
       ▼
[Human Operator Conducts Direct Commercial Negotiation]
 ├── Operator answers technical questions
 ├── Operator conducts discovery call / Zoom meeting
 └── Operator logs activity in activities table
       │
       ▼
[Conversation Outcome Reached]
      ├── If Closed Lost ─────────────► [Operator sets conversations.status = 'closed_lost']
      └── If Commercial Agreement ────► [Operator sets conversations.status = 'closed_won']
             │
             ▼
[Transition to Partnership Onboarding (Delivery Governance Tier)]
```

### 6.2. Intent & Objection Classification Taxonomy
When an inbound message is analyzed, the system classifies partner sentiment across standard categories:
1. **Active Commercial Interest:** Partner requests discovery call, shares upcoming project scope, or asks for rate card.
2. **Technical Verification:** Partner asks specific architectural questions (e.g., "Do you work with Supabase?", "Can you build custom React components for Framer?").
3. **Timing / Deferred Opportunity:** Partner acknowledges interest but indicates no current client overflow; requests check-in next quarter.
4. **Past Experience Friction:** Partner expresses skepticism based on previous unreliable freelance engagements; requires reassurance regarding engineering oversight and SLAs.
5. **Direct Disqualification / Opt-Out:** Partner requests removal from outreach list or expresses zero interest. Immediately routed to `suppression_registry`.

### 6.3. Strict Human Negotiation Boundary
- **Zero Autonomous Negotiation:** No autonomous agent is permitted to conduct live messaging, reply to emails, or negotiate terms with a prospective partner.
- **Conversation State Authority:** Database trigger `fn_enforce_conversation_status_transitions` restricts all modifications to `conversations.status` to `is_human_operator() = TRUE`.
- **Commercial Commitments:** Scopes of Work (SOWs), price quotes, delivery timelines, and partnership terms are exclusively authored and executed by authorized human leadership.
