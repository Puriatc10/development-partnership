# Standard Agent Contracts — Specifications & Payloads

**Document Version:** 2.0.0  
**Phase:** 2.2 — Agent Operating Model & Workflow Design (Contract & Runtime Alignment)  
**Target Platform:** Runtime-Independent (Current Temporary: Antigravity | Future: Hermes) & Supabase PostgreSQL CRM  
**Status:** Canonical Design Specification (Design & Documentation Only)  
**Date:** 2026-11-07  

---

## 1. Standardized Contract Architecture

This document establishes the formal, deterministic input and output contracts for each logical agent operating within the multi-agent orchestration architecture.

### 1.1. Contract Design Principles
1. **Runtime Decoupling:** Contracts define pure business logic, input/output schemas, and evidence requirements. They are completely decoupled from runtime execution mechanics and apply equally to the current temporary interactive runtime (**Antigravity**) and the planned future autonomous runtime (**Hermes**).
2. **Entity Grounding:** Every contract strictly references canonical database primary keys (`company_id`, `contact_id`, `task_id`, `research_report_id`, `qualification_id`, `outreach_message_id`). Where an entity does not yet exist in the database (e.g., candidate discovery or proposed contacts), the contract explicitly tags the field as `proposed_artifact_for_operator_ingestion`.
3. **Database Enum & Constraint Compatibility:** All contract payloads strictly adhere to the exact PostgreSQL enum types defined in [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql), specifically `research_finding_type`, `finding_category_type`, `contact_role_type`, and `commercial_model_type`.
4. **Evidence Auditability:** Every factual finding, dimension score, or outreach claim must carry explicit source URLs, excerpt quotations, and confidence classifications.

> [!NOTE]
> **Mandatory Synthetic Data Notice:** All company names (e.g., *"Apex Visual Studio"*), individual identities (e.g., *"Maximilian Weber"*), domains (`apexvisual.de`), URLs, UUIDs, and portfolio project descriptions appearing in this document are **purely synthetic illustrative examples** designed to validate schema compatibility. They must **never** be mistaken for actual prospective partners, verified case studies, or live CRM data.

---

## 2. Contract 1: Orchestrator Agent (`hermes-orchestrator`)

### 2.1. Agent Identity & Purpose
- **Name:** `hermes-orchestrator`
- **Identity Claim:** Application-level workflow controller (no dedicated database agent identity; executes on behalf of Human Operator).
- **Purpose:** Ingests campaign objectives from human management, constructs sequenced task dependency trees, schedules subagent execution, tracks operational status, and routes approval requests to the human operator.

### 2.2. Trigger Conditions
- Operator initiates a new prospecting campaign directive via the orchestration control interface.
- A subagent marks an assigned task as `completed` in `tasks`, triggering downstream dependency evaluation.
- An asynchronous event occurs (e.g., human approval granted, task execution failure, review timeout).

### 2.3. Input & Preconditions
- **Required Input:**
  - `directive_id`: Unique identifier of the human operator's directive.
  - `campaign_objective`: Target market segment (`digital_agency`, `webflow_studio`, `framer_studio`), geographic filter, and target candidate volume.
  - `human_operator_id`: UUID of the authorizing human operator (`auth.uid()`).
- **Optional Input:**
  - `custom_due_date`: Target completion deadline.
  - `priority_override`: Operational priority (`low`, `medium`, `high`, `urgent`).
- **Preconditions:**
  - Authenticated session with authorized `operator`, `admin`, or `founder` role (`is_human_operator() = TRUE`).
  - Target outreach campaign exists in `outreach_campaigns` if workflow extends to outreach drafting.

```json
{
  "directive_id": "dir_20261107_wf_expansion",
  "human_operator_id": "e0a12345-6789-4abc-def0-123456789abc",
  "campaign_objective": {
    "target_segment": "webflow_studio",
    "target_geography": "DACH_region",
    "target_volume": 10,
    "focus_service": "webflow_backend_extensions"
  },
  "priority": "high",
  "due_date": "2026-11-14T18:00:00Z"
}
```

### 2.4. Allowed Tools & Execution Procedure
1. **Tool Invocation:**
   - Ingests human directive payload.
   - Decomposes objective into discrete workflow stages (Discovery → Ingestion → Research → Qualification → Outreach Drafting).
   - Generates candidate discovery query parameters for the Discovery Agent.
   - Upon company registration by Human Operator, generates task record in `tasks` with `task_type = 'company_research'`.
   - In Antigravity interactive mode: surfaces next recommended action to operator. In future Hermes mode: schedules autonomous worker.
   - Evaluates task completion outputs against quality and evidence standards before scheduling next stage.
2. **Database Operations:**
   - Evaluates active tasks (`SELECT FROM tasks` via Human Operator session).
   - Routes operator review notifications for completed qualification scorecards and outreach drafts.

### 2.5. Expected Output Contract
```json
{
  "workflow_id": "wf_20261107_001",
  "directive_id": "dir_20261107_wf_expansion",
  "status": "in_progress",
  "active_stage": "prospect_discovery",
  "tasks_summary": {
    "total_tasks_planned": 15,
    "completed_tasks": 0,
    "in_progress_tasks": 1,
    "pending_tasks": 14,
    "failed_tasks": 0
  },
  "active_subagent_tasks": [
    {
      "task_id": "application_context_task_001",
      "assigned_agent": "hermes-discovery",
      "task_type": "candidate_discovery",
      "target_segment": "webflow_studio",
      "status": "in_progress"
    }
  ],
  "requires_human_attention": false,
  "execution_notes": "Discovery phase initiated for 10 candidate Webflow studios in DACH region (Synthetic Example)."
}
```

### 2.6. Governance, Failures & Prohibitions
- **Evidence & Confidence Rules:** Must not synthesize or invent task completion data; task progression strictly depends on verifiable database records.
- **Failure Conditions:** Subagent task failure; timeout exceeding SLA; unresolvable validation errors.
- **Retry Behavior:** In interactive Antigravity runtime, failure prompts the operator. In future Hermes runtime, maximum 2 automated retries before operator escalation.
- **Handoff Destination:** Routes tasks to specialized subagents; routes approval items to Human Operator queue.
- **Prohibited Actions:** Cannot modify database schemas, cannot bypass human review gates, cannot authorize external messaging.

---

## 3. Contract 2: Discovery Agent (`hermes-discovery`)

### 3.1. Agent Identity & Purpose
- **Name:** `hermes-discovery`
- **Identity Claim:** Application-level search and candidate extraction subagent.
- **Purpose:** Searches approved public showcases, directories, and web sources to identify candidate design agencies and visual development studios matching the IPP criteria.

### 3.2. Trigger Conditions
- Scheduled or invoked by Orchestrator with specific segment and discovery criteria.

### 3.3. Input & Preconditions
- **Required Input:**
  - `orchestrator_task_id`: Identifier of the orchestration task.
  - `target_segment`: Commercial market segment (`digital_agency`, `webflow_studio`, `framer_studio`).
  - `search_sources`: Permitted public discovery channels (e.g., `webflow_experts_directory`, `framer_partner_directory`, `clutch_co`).
  - `candidate_count_requested`: Desired candidate batch size (integer, 1–25).
- **Optional Input:**
  - `geographic_filter`: Country or regional boundary (e.g., `"DACH"`, `"United_Kingdom"`, `"United_States"`).
  - `minimum_team_size`: Inferred studio size floor.
- **Preconditions:**
  - Search targets must be permitted, publicly accessible sources.

```json
{
  "orchestrator_task_id": "orch_task_disc_001",
  "target_segment": "webflow_studio",
  "search_sources": [
    "webflow_experts_directory",
    "clutch_co"
  ],
  "candidate_count_requested": 5,
  "geographic_filter": "DACH"
}
```

### 3.4. Execution Procedure
1. Execute web search across specified directory sources using query keywords aligned with the target segment.
2. Filter surfaced entities against negative keywords (e.g., "freelance solo", "recruiting agency", "hardware").
3. Extract core firmographic candidate metadata: public agency name, primary website domain, public contact page, portfolio links, and published studio focus.
4. Normalize primary domain format (lowercase, strip `https://`, `www.`, and trailing slashes).
5. Assemble candidate evidence records with source URLs and extraction timestamps.
6. Return structured candidate payload to Orchestrator for duplicate inspection and Human Operator review.

### 3.5. Expected Output Contract
```json
{
  "orchestrator_task_id": "orch_task_disc_001",
  "discovery_session_id": "disc_session_20261107_01",
  "candidates_discovered_count": 1,
  "candidates": [
    {
      "candidate_name": "Apex Visual Studio (Synthetic)",
      "primary_domain": "apexvisual.de",
      "alternate_domains": [
        "apexvisual.com"
      ],
      "market_segment": "webflow_studio",
      "inferred_location": "Berlin, Germany",
      "inferred_team_size_range": "5-15",
      "source_name": "webflow_experts_directory",
      "source_url": "https://experts.webflow.com/profile/apex-visual",
      "public_portfolio_sample_urls": [
        "https://apexvisual.de/work/fintech-dashboard",
        "https://apexvisual.de/work/mobility-portal"
      ],
      "discovery_evidence_snippet": "Certified Webflow Enterprise Partner specializing in complex visual builds and interactive web experiences.",
      "identified_at": "2026-11-07T19:45:00Z"
    }
  ]
}
```

### 3.6. Governance, Failures & Prohibitions
- **Evidence Rules:** Every candidate must include a valid, publicly verifiable `source_url`.
- **Database Restrictions:** **Zero direct database write permissions.** The Discovery Agent cannot write to `companies` or `company_domains` in PostgreSQL. Output is strictly emitted in application memory.
- **Failure Conditions:** Source unavailability; zero candidates matching firmographic criteria; IP or rate limit blocks.
- **Retry Behavior:** Backoff and retry with alternative approved directory source; maximum 2 attempts.
- **Handoff Destination:** Emits payload to Orchestrator, which prompts Human Operator for ingestion authorization.
- **Prohibited Actions:** No database mutations, no scraping behind logins, no autonomous contact outreach.

---

## 4. Contract 3: Research Agent (`hermes-researcher`)

### 4.1. Agent Identity & Purpose
- **Name:** `hermes-researcher`
- **Identity Claim:** JWT `app_metadata.user_role = 'agent'`, `app_metadata.subagent_type = 'researcher'`, `app_metadata.agent_id = 'hermes-researcher-v1'`.
- **Purpose:** Conducts deep-dive investigations into registered prospective partner companies to identify technical capabilities, engineering bottlenecks, recurring client demand, and key decision-makers.

### 4.2. Trigger Conditions
- Assigned an active task in `tasks` with `task_type = 'company_research'` and `status = 'pending'`.

### 4.3. Input & Preconditions
- **Required Input:**
  - `task_id`: UUID of assigned task in `tasks`.
  - `company_id`: UUID of target company in `companies`.
  - `company_name`: Official legal or commercial name.
  - `primary_domain`: Verified web domain.
- **Optional Input:**
  - `target_segment`: Inferred IPP market segment.
  - `campaign_id`: UUID of associated outreach campaign (if applicable).
- **Preconditions:**
  - An active task exists in `tasks` assigned to `current_agent_identity()`.
  - Target company lifecycle stage is `researching` or `identified`.
  - Company is not listed in `suppression_registry`.

```json
{
  "task_id": "77777777-7777-7777-7777-777777777771",
  "company_id": "11111111-1111-1111-1111-111111111111",
  "company_name": "Apex Visual Studio (Synthetic)",
  "primary_domain": "apexvisual.de",
  "target_segment": "webflow_studio",
  "campaign_id": "33333333-3333-3333-3333-333333333333"
}
```

### 4.4. Allowed Tools & Execution Procedure
1. **Transition Task Status:**
   - UPDATE `tasks` SET `status = 'in_progress'` WHERE `id = task_id`.
2. **Deep-Dive Investigation:**
   - Web search & inspect `apexvisual.de`: services offered, published case studies, team page, client testimonials, tech stack indicators.
   - Investigate technical limitations: look for dynamic portals, external auth, external database integrations, API references.
   - Discover key decision-makers (Founders, Managing Directors, Head of Visual Development).
3. **Database Persistence:**
   - INSERT into `research_reports`: store `summary`, `recommended_angle`, `overall_confidence`, `gaps_and_unknowns`.
   - INSERT into `research_findings`: populate atomic findings with exact SQL enum categories:
     - `finding_type`: `'verified_fact'`, `'hypothesis'`, or `'information_gap_tbd'`.
     - `category`: `'business_model'`, `'client_roster'`, `'technology_stack'`, `'scoping_maturity'`, `'growth_signals'`, `'pain_points'`, `'pricing_rates'`, `'team_structure'`, or `'other'`.
     - `confidence`: `'high'`, `'medium'`, `'low'`, or `'unverified'`.
   - Record activity log: INSERT into `activities` (`activity_type = 'note'`, summary of research completion).
   - *(Note: Researcher CANNOT insert into `contacts`; discovered contacts are output as proposed data in the return payload for Human Operator review and ingestion).*
4. **Complete Task:**
   - UPDATE `tasks` SET `status = 'completed'` WHERE `id = task_id`.

### 4.5. Expected Output Contract
```json
{
  "task_id": "77777777-7777-7777-7777-777777777771",
  "company_id": "11111111-1111-1111-1111-111111111111",
  "research_report_id": "44444444-4444-4444-4444-444444444441",
  "research_summary": "Apex Visual Studio is a premium 12-person Webflow agency in Berlin. They serve enterprise mobility and fintech clients. Recent case studies show client demand for user authentication and dynamic relational databases that exceed Webflow native capabilities (Synthetic Example).",
  "recommended_angle": "Position as behind-the-scenes wholesale backend engineering partner for Webflow API, Supabase integrations, and custom user portals.",
  "overall_confidence": "high",
  "gaps_and_unknowns": "Exact average client project budget is unstated (marked TBD). Current backend development vendor unconfirmed.",
  "findings_count": 3,
  "findings": [
    {
      "finding_id": "55555555-5555-5555-5555-555555555551",
      "finding_type": "verified_fact",
      "category": "technology_stack",
      "statement": "Primary visual build tool is Webflow; integrates Client-First styling and headless components.",
      "evidence_url": "https://apexvisual.de/about",
      "evidence_excerpt": "We build exclusively on Webflow, leveraging Client-First system and advanced headless integrations.",
      "confidence": "high"
    },
    {
      "finding_id": "55555555-5555-5555-5555-555555555552",
      "finding_type": "verified_fact",
      "category": "client_roster",
      "statement": "Built customer portal interface for Berlin fintech client requiring custom authentication and PostgreSQL database.",
      "evidence_url": "https://apexvisual.de/work/fintech-dashboard",
      "evidence_excerpt": "Designed complete visual interface in Webflow, connected via webhooks to custom backend architecture.",
      "confidence": "high"
    },
    {
      "finding_id": "55555555-5555-5555-5555-555555555553",
      "finding_type": "verified_fact",
      "category": "team_structure",
      "statement": "Maximilian Weber is Co-Founder & Technical Creative Director leading digital execution.",
      "evidence_url": "https://apexvisual.de/team",
      "evidence_excerpt": "Maximilian Weber — Co-Founder & Creative Technology Lead.",
      "confidence": "high"
    }
  ],
  "proposed_contacts_for_operator_review": [
    {
      "first_name": "Maximilian",
      "last_name": "Weber",
      "job_title": "Co-Founder & Creative Technology Lead",
      "role_type": "founder_executive",
      "is_decision_maker": true,
      "linkedin_url": "https://linkedin.com/in/maximilian-weber-synthetic",
      "verification_source_url": "https://apexvisual.de/team"
    }
  ]
}
```

### 4.6. Governance, Failures & Prohibitions
- **Evidence Standards:** Factual claims strictly require `evidence_url` and `evidence_excerpt`. If an indicator is circumstantial, `finding_type` must be `'hypothesis'`. Missing facts must be logged as `'information_gap_tbd'`.
- **Database Enforcement:** Trigger `chk_verified_fact_requires_evidence` enforces that `finding_type = 'verified_fact'` requires non-null `evidence_url` and confidence in `('high', 'medium')`.
- **Prohibited Actions:** Cannot insert into `contacts` (blocked by RLS), cannot modify company record, cannot score qualifications, cannot insert outreach drafts.

---

## 5. Contract 4: Qualification Agent (`hermes-qualifier`)

### 5.1. Agent Identity & Purpose
- **Name:** `hermes-qualifier`
- **Identity Claim:** JWT `app_metadata.user_role = 'agent'`, `app_metadata.subagent_type = 'qualifier'`, `app_metadata.agent_id = 'hermes-qualifier-v1'`.
- **Purpose:** Evaluates researched prospect data against the approved 7-dimension, 100-point IPP model, calculates objective qualification tiers, detects disqualifiers, and generates a draft scorecard for human operator review.

### 5.2. Trigger Conditions
- Assigned an active task in `tasks` with `task_type = 'lead_qualification'` and `status = 'pending'`.

### 5.3. Input & Preconditions
- **Required Input:**
  - `task_id`: UUID of assigned task in `tasks`.
  - `company_id`: UUID of target company in `companies`.
  - `research_report_id`: UUID of completed `research_reports` record.
- **Preconditions:**
  - Research report exists with at least 1 verified finding.
  - Active task exists assigned to `current_agent_identity()`.

```json
{
  "task_id": "77777777-7777-7777-7777-777777777772",
  "company_id": "11111111-1111-1111-1111-111111111111",
  "research_report_id": "44444444-4444-4444-4444-444444444441"
}
```

### 5.4. Allowed Tools & Execution Procedure
1. **Transition Task Status:**
   - UPDATE `tasks` SET `status = 'in_progress'` WHERE `id = task_id`.
2. **Evaluate 7 Dimensions (Mathematical Verification):**
   - Read all `research_findings` for `company_id`.
   - Score each dimension strictly according to verified evidence:
     - Customer Flow (0–20 pts)
     - Development Demand (0–20 pts)
     - Repeatability of Orders (0–15 pts)
     - Technical Fit (0–15 pts)
     - Business Size (0–10 pts)
     - Decision-Maker Accessibility (0–10 pts)
     - Commercial Fit (0–10 pts)
   - Sum dimensions to compute `overall_score = sum(7_dimensions)`.
   - Determine `qualification_tier`:
     - $\ge 80 \implies \text{'tier\_1\_priority'}$
     - $60 \text{ to } 79 \implies \text{'tier\_2\_prospective'}$
     - $40 \text{ to } 59 \implies \text{'tier\_3\_deferred'}$
     - $< 40 \lor \text{disqualified} \implies \text{'disqualified'}$
3. **Database Persistence:**
   - INSERT into `lead_qualifications`:
     - Set `review_status = 'draft'`.
     - Populate `dimension_evidence` JSONB mapping dimension keys to score, reasoning, and evidence findings.
     - Document `recommended_next_action`.
4. **Complete Task:**
   - UPDATE `tasks` SET `status = 'completed'` WHERE `id = task_id`.

### 5.5. Expected Output Contract
```json
{
  "task_id": "77777777-7777-7777-7777-777777777772",
  "company_id": "11111111-1111-1111-1111-111111111111",
  "qualification_id": "66666666-6666-6666-6666-666666666661",
  "evaluator_identity": "hermes-qualifier-v1",
  "overall_score": 85.00,
  "scores": {
    "customer_flow_score": 18.00,
    "development_demand_score": 18.00,
    "repeatability_orders_score": 13.00,
    "technical_fit_score": 14.00,
    "business_size_score": 8.00,
    "decision_maker_accessibility_score": 8.00,
    "commercial_fit_score": 6.00
  },
  "qualification_tier": "tier_1_priority",
  "disqualification_reasons": [],
  "disqualification_notes": null,
  "recommended_next_action": "Proceed to personalized outreach drafting focusing on Webflow backend extensions and database integrations.",
  "review_status": "draft",
  "dimension_evidence": {
    "customer_flow": {
      "score": 18.00,
      "max": 20,
      "evidence": "Roster of corporate clients, verified enterprise mobility accounts, active marketing case studies published quarterly.",
      "finding_ids": ["55555555-5555-5555-5555-555555555552"]
    },
    "development_demand": {
      "score": 18.00,
      "max": 20,
      "evidence": "Case studies explicitly cite custom portal builds and external database integrations beyond native Webflow CMS limits.",
      "finding_ids": ["55555555-5555-5555-5555-555555555552"]
    },
    "repeatability_orders": {
      "score": 13.00,
      "max": 15,
      "evidence": "Retainer engagements documented on Clutch reviews; ongoing maintenance and seasonal campaign rollouts.",
      "finding_ids": ["55555555-5555-5555-5555-555555555551"]
    },
    "technical_fit": {
      "score": 14.00,
      "max": 15,
      "evidence": "Specialized Webflow studio with Client-First build standards needing backend Supabase/API support.",
      "finding_ids": ["55555555-5555-5555-5555-555555555551"]
    },
    "business_size": {
      "score": 8.00,
      "max": 10,
      "evidence": "Team of 12 employees listed on website; established creative studio founded in 2021.",
      "finding_ids": ["55555555-5555-5555-5555-555555555553"]
    },
    "decision_maker_accessibility": {
      "score": 8.00,
      "max": 10,
      "evidence": "Identified Co-Founder & Technical Creative Director Maximilian Weber with verified direct LinkedIn profile.",
      "finding_ids": ["55555555-5555-5555-5555-555555555553"]
    },
    "commercial_fit": {
      "score": 6.00,
      "max": 10,
      "evidence": "Premium studio rates on Clutch; exact client budget floor unconfirmed (scored conservatively).",
      "finding_ids": []
    }
  }
}
```

### 5.6. Governance, Failures & Prohibitions
- **Evidence Mandatory:** Database trigger `fn_enforce_lead_qualification_rules` enforces that any dimension score `> 0` requires non-null evidence in `dimension_evidence`.
- **Review Gate:** Trigger enforces that `review_status = 'approved'` strictly requires `is_human_operator() = TRUE`. Agents can **only** submit `review_status = 'draft'`.
- **Prohibited Actions:** Cannot approve scorecards, cannot overwrite company lifecycle stage, cannot alter score calculations.

---

## 6. Contract 5: Outreach Drafter Agent (`hermes-drafter`)

### 6.1. Agent Identity & Purpose
- **Name:** `hermes-drafter`
- **Identity Claim:** JWT `app_metadata.user_role = 'agent'`, `app_metadata.subagent_type = 'drafter'`, `app_metadata.agent_id = 'hermes-drafter-v1'`.
- **Purpose:** Generates personalized, professional draft outreach correspondence for human-approved prospective partner contacts, referencing verified portfolio evidence.

### 6.2. Trigger Conditions
- Assigned an active task in `tasks` with `task_type = 'outreach_drafting'` and `status = 'pending'`.

### 6.3. Input & Preconditions
- **Required Input:**
  - `task_id`: UUID of assigned task in `tasks`.
  - `company_id`: UUID of target company in `companies`.
  - `contact_id`: UUID of target contact in `contacts`.
  - `campaign_id`: UUID of target campaign in `outreach_campaigns`.
- **Preconditions:**
  - Company has an approved lead qualification (`review_status = 'approved'`).
  - Target contact is verified, active, and not opted out (`is_opted_out = FALSE`).
  - Target contact email/LinkedIn is not listed in `suppression_registry`.
  - Drafter has an active task assigned to `current_agent_identity()` matching company, contact, and campaign.

```json
{
  "task_id": "77777777-7777-7777-7777-777777777773",
  "company_id": "11111111-1111-1111-1111-111111111111",
  "contact_id": "22222222-2222-2222-2222-222222222221",
  "campaign_id": "33333333-3333-3333-3333-333333333333",
  "channel": "email"
}
```

### 6.4. Allowed Tools & Execution Procedure
1. **Transition Task Status:**
   - UPDATE `tasks` SET `status = 'in_progress'` WHERE `id = task_id`.
2. **Retrieve Context:**
   - Query approved qualification scorecard (`lead_qualifications`).
   - Query verified findings (`research_findings`).
   - Confirm recipient identity (`contacts`).
3. **Draft Message Copy:**
   - Personalize opening with direct reference to specific portfolio work (`fintech-dashboard`).
   - Highlight the technical hurdle (Webflow visual limits vs backend database requirements).
   - Position our collaboration model: behind-the-scenes wholesale development capacity.
   - Propose Project-Based Wholesale engagement (pilot project) to demonstrate execution reliability.
   - Attach supporting `personalization_evidence_ids` and articulate `personalization_rationale`.
4. **Database Persistence:**
   - INSERT into `outreach_messages`:
     - `approval_status = 'draft'`
     - `sending_status = 'draft'`
     - `proposed_commercial_model = 'project_based_wholesale'`
5. **Complete Task:**
   - UPDATE `tasks` SET `status = 'completed'` WHERE `id = task_id`.

### 6.5. Expected Output Contract
```json
{
  "task_id": "77777777-7777-7777-7777-777777777773",
  "company_id": "11111111-1111-1111-1111-111111111111",
  "contact_id": "22222222-2222-2222-2222-222222222221",
  "campaign_id": "33333333-3333-3333-3333-333333333333",
  "outreach_message_id": "88888888-8888-8888-8888-888888888881",
  "channel": "email",
  "subject": "Extending Apex's Webflow builds with dedicated backend engineering",
  "body": "Hi Maximilian,\n\nI was reviewing Apex Visual's recent fintech client portal and was really impressed by how cleanly you structured the Client-First design system within Webflow.\n\nWe partner with visual development studios as a behind-the-scenes wholesale engineering partner—handling custom backend logic, Supabase database architecture, user authentication, and API integrations that exceed Webflow's native limits.\n\nOur approach is simple: we work strictly white-label under your brand on fixed-scope project milestones, allowing your design team to take on complex web application contracts without hiring internal backend engineers.\n\nWould you be open to a brief 15-minute conversation next week to see if we might be a fit for your upcoming client pipeline?\n\nBest regards,\n[Human Founder Name]\nManaged Development Partnership",
  "personalization_evidence_ids": [
    "55555555-5555-5555-5555-555555555551",
    "55555555-5555-5555-5555-555555555552"
  ],
  "personalization_rationale": "Referenced their verified fintech portal case study and their Client-First build methodology. Addressed their specific pain point (external database and authentication connectivity beyond Webflow) while offering a low-friction wholesale project engagement.",
  "proposed_commercial_model": "project_based_wholesale",
  "approval_status": "draft",
  "sending_status": "draft",
  "sequence_step": 1
}
```

### 6.6. Governance, Failures & Prohibitions
- **Commercial Guardrails:** Prohibited from selecting `strategic_revenue_sharing` (enforced by `chk_outreach_no_revenue_sharing`).
- **Dispatch Gate:** Check constraint `chk_outreach_mandatory_human_gate` prevents any setting of `sending_status` to dispatch states (`queued`, `sending`, etc.) while `approval_status != 'approved'`.
- **Prohibited Actions:** Never send messages; never schedule messages; never invent case studies, pricing, or turnaround promises.
