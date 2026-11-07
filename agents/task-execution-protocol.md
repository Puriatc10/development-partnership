# Task Execution & Coordination Protocol

**Document Version:** 1.0.0  
**Phase:** 2.2 — Agent Operating Model & Workflow Design  
**Target Platform:** Hermes Multi-Agent Orchestration Engine & Supabase PostgreSQL CRM  
**Status:** Canonical Design Specification (Design & Documentation Only)  
**Date:** 2026-11-07  

---

## 1. Task Coordination Architecture

This document defines the execution protocol, lifecycle state machine, assignment rules, dependency management, failure handling, and handoff mechanics governing operational tasks executed across human operators and Hermes subagents.

### 1.1. The PostgreSQL `tasks` Table Foundation
The operational coordination backbone is the canonical `tasks` table defined in [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql):

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL,
    priority task_priority_type NOT NULL DEFAULT 'medium',
    status task_status_type NOT NULL DEFAULT 'pending',
    assigned_actor_type actor_type NOT NULL,
    assigned_actor_identity TEXT NOT NULL,
    campaign_id UUID REFERENCES outreach_campaigns(id) ON DELETE SET NULL,
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    contact_id UUID,
    due_date TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
```

### 1.2. Database-Enforced Invariants
The task coordination layer is fortified by database-level constraints and triggers established during Phase 2.1:
1. **Referential Integrity:** `fk_tasks_contact_company` guarantees that if a `contact_id` is assigned, it must belong to the designated `company_id`.
2. **Contact Prerequisite:** `chk_tasks_contact_requires_company` enforces that a task cannot reference a contact without referencing its parent company.
3. **Completion Synchronization:** Table constraint `chk_task_completed_at_sync` enforces:
   $$\text{status} = \text{'completed'} \iff \text{completed\_at IS NOT NULL}$$
4. **Agent Update Governance:** Trigger `fn_enforce_task_governance` enforces:
   - Agents can **never** modify task assignment (`assigned_actor_type`, `assigned_actor_identity`), scope (`company_id`, `contact_id`, `campaign_id`), or creation timestamps.
   - Agents cannot skip states: transitioning from `pending` directly to `completed` without executing in `in_progress` is blocked.
   - Agents cannot reopen or modify tasks that are already `completed` or `cancelled`.
   - Automated timestamp management: sets `completed_at = clock_timestamp()` upon completion and clears `completed_at = NULL` if reopened by a human operator.

---

## 2. Task Lifecycle State Machine

A task progresses through four lifecycle states:

```
                  ┌──────────────┐
                  │   pending    │
                  └──────┬───────┘
                         │
        (Agent or Human starts execution)
                         │
                         ▼
                  ┌──────────────┐
       ┌──────────┤ in_progress  ├──────────┐
       │          └──────┬───────┘          │
       │                 │                  │
(Operator cancels) (Deliverable produced) (Failure / timeout)
       │                 │                  │
       ▼                 ▼                  ▼
┌──────────────┐  ┌──────────────┐   ┌──────────────┐
│  cancelled   │  │  completed   │   │  cancelled   │
└──────────────┘  └──────┬───────┘   └──────────────┘
                         │
         (Human Operator reopens for rework)
                         │
                         ▼
                  ┌──────────────┐
                  │ in_progress  │
                  └──────────────┘
```

### 2.1. Permitted Transitions & Invariants

| From State | To State | Permitted Actor | Execution Requirements / Invariants | Automated Side Effects |
| :--- | :--- | :--- | :--- | :--- |
| `pending` | `in_progress` | Assigned Agent / Human | `assigned_actor_identity` must match caller identity. | Task lock acquired. |
| `in_progress` | `completed` | Assigned Agent / Human | Operational deliverable persisted in database. | `completed_at = clock_timestamp()` auto-populated. |
| `pending` | `completed` | Human Operator Only | **Blocked for Agents** by `fn_enforce_task_governance`. | `completed_at` populated. |
| `pending` / `in_progress` | `cancelled` | Human Operator / Orchestrator | Execution halted, obsolete, or unresolvable error. | Downstream tasks cancelled. |
| `completed` | `in_progress` | Human Operator Only | **Blocked for Agents**. Operator requests task rework. | `completed_at = NULL` cleared. |
| `completed` / `cancelled`| Any state | Agents BLOCKED | Terminated tasks cannot be mutated or reopened by agents. | Exception raised. |

---

## 3. Task Creation, Assignment & Ownership

### 3.1. Standard Task Types
To ensure consistency across workflows, `task_type` values are standardized:

| Task Type String | Assigned Subagent | Primary Scope | Primary Output Deliverable |
| :--- | :--- | :--- | :--- |
| `'company_research'` | `hermes-researcher-v1` | `company_id` | `research_reports` & `research_findings` |
| `'lead_qualification'` | `hermes-qualifier-v1` | `company_id` | `lead_qualifications` (draft scorecard) |
| `'outreach_drafting'` | `hermes-drafter-v1` | `company_id`, `contact_id`, `campaign_id` | `outreach_messages` (draft message) |
| `'qualification_review'`| Human Operator | `company_id` | Human approval/rejection endorsement |
| `'outreach_approval'` | Human Operator | `company_id`, `contact_id` | Human approval/revision endorsement |
| `'inbound_follow_up'` | Human Operator | `company_id`, `contact_id` | Commercial call, message response |

### 3.2. Assignment Rules & Ownership
1. **Explicit Identity Binding:** Every subagent task must explicitly declare:
   - `assigned_actor_type = 'agent'`
   - `assigned_actor_identity = 'hermes-<subagent>-v1'`
2. **Immutable Ownership:** Once created, an agent cannot reassign a task to another subagent, change its assignment to human, or reassign human tasks to itself.
3. **Database Write Path for Creation:**
   - Under current RLS policies, table INSERT on `tasks` is restricted to `is_human_operator() = TRUE`.
   - The Orchestrator generates tasks via an authorized Human Operator JWT session or a trusted backend service role.

---

## 4. Sequential Dependency & Handoff Protocol

Workflows execute as sequential dependency chains where the completion of one task triggers the generation or activation of the next:

$$\text{Stage 1: Ingestion} \xrightarrow{\text{creates}} \text{Task 1: Research} \xrightarrow{\text{completes}} \text{Task 2: Qualification} \xrightarrow{\text{approves}} \text{Task 3: Outreach}$$

### 4.1. Handoff Protocol via Database State
Handoffs between subagents do **not** rely on peer-to-peer inter-agent messaging. They are mediated deterministically through the database:

1. **Subagent Execution & Persistence:**
   - Subagent $A$ executes work, persists its structured output deliverable (e.g., `research_reports`) in the target database table, and updates its assigned task in `tasks` to `status = 'completed'`.
2. **Orchestrator Validation:**
   - The Orchestrator detects task completion (via polling or event trigger).
   - Validates that the expected database deliverable actually exists and satisfies quality/evidence criteria.
3. **Downstream Task Provisioning:**
   - If preconditions are satisfied, the Orchestrator creates the downstream task (e.g., `task_type = 'lead_qualification'`) bound to the same `company_id`.
   - If downstream execution requires human approval (e.g., after qualification), the Orchestrator creates a task assigned to `assigned_actor_type = 'human'`.

---

## 5. Current Database Task Limitations & Architectural Solutions

The current `tasks` table is an operational foundation, but exhibits specific schema limitations for advanced autonomous orchestration:

### 5.1. Identified Limitations Matrix

| Capability | Current Database State | Orchestration Consequence |
| :--- | :--- | :--- |
| **Structured Input Payload** | No `input_payload` JSONB column. | Subagent must infer parameters from relational foreign keys (`company_id`, `contact_id`, `campaign_id`). |
| **Structured Output Payload** | No `output_payload` JSONB column. | Subagent persists deliverables directly in domain tables (`research_reports`, etc.) rather than in the task record. |
| **Retry Counter** | No `retry_count` or `max_retries` integer. | Retries must be tracked in Hermes application memory or execution logs. |
| **Execution Leases & Timeouts** | No `lease_expires_at` timestamp. | Stalled agent workers cannot be automatically detected or reclaimed by database triggers alone. |
| **Idempotency Keys** | No `idempotency_key` unique column. | Orchestrator must prevent duplicate task creation via application-level deduplication. |
| **Execution Traces & Error Logs**| No `error_log` or `execution_trace` column. | Failure details, stack traces, and tool logs must be recorded in application telemetry. |

### 5.2. Practical Implementation Strategy (Three-Layer Architecture)

To resolve these limitations **without modifying the database schema in Phase 2.2**, the orchestration architecture partitions responsibilities across three operational layers:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. DATABASE-ENFORCED LAYER (Existing Supabase Schema)                  │
│    - Task identity, assignment (identity, type), lifecycle status      │
│    - Foreign key scope (company_id, contact_id, campaign_id)          │
│    - completed_at synchronization & agent update immutability guards   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2. APPLICATION EXECUTION LAYER (Hermes Runtime Memory & State Store)  │
│    - Idempotency key tracking (prevents duplicate task generation)     │
│    - In-flight execution leases (detects worker crashes after SLA)     │
│    - Retry counters & exponential backoff timers                       │
│    - Execution logs, subagent reasoning traces, and error payloads     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 3. PROPOSED FUTURE SCHEMA EXTENSION (Documented for Phase 2.3 / 3.0)   │
│    - Optional future columns on tasks:                                 │
│      input_payload JSONB, output_payload JSONB, retry_count INT,       │
│      idempotency_key TEXT UNIQUE, lease_expires_at TIMESTAMPTZ         │
└────────────────────────────────────────────────────────────────────────┘
```

1. **Existing Database Layer (Enforced Today):**
   - Stores authoritative task state, ownership, and core entity context.
   - Domain output records are stored in dedicated tables (`research_reports`, `lead_qualifications`, `outreach_messages`), eliminating the strict need for `output_payload`.
2. **Application Execution Layer (Hermes Runtime):**
   - Manages orchestration state machines, in-memory retry counters, and lease timeouts.
   - Logs tool execution errors and subagent traces to file-based or centralized application telemetry.
3. **Proposed Future Schema Extension (Non-Breaking):**
   - Documented as an optional future enhancement to store execution metadata natively in Supabase when high-scale distributed workers are deployed.

---

## 6. Failure Handling, Retries & Escalation Protocol

### 6.1. Failure Categorization & Recovery

| Failure Type | Example Scenario | Recovery Procedure | Max Retries |
| :--- | :--- | :--- | :---: |
| **Transient Network / Rate Limit** | Web search API timeout; Supabase connection reset. | Exponential backoff (2s, 8s, 30s); retry task in `in_progress`. | 2 |
| **Evidence Gap / Missing Data** | Target studio website offline; zero portfolio published. | Mark task `completed` with detailed `gaps_and_unknowns`; assign 0 points; route to Human Operator. | 0 |
| **Database Constraint Rejection** | Dimension score $> 0$ without evidence; invalid status jump. | Catch exception; abort transaction; record failure in log; escalate to Operator. | 0 |
| **Worker Process Crash** | Hermes worker crashes while task is `in_progress`. | Hermes Orchestrator monitors lease timeout (default: 15 min); reclaims stale task; resets to `pending`. | 1 |

### 6.2. Human Escalation Protocol
When an unresolvable failure or policy conflict occurs:
1. Orchestrator marks the task as `status = 'cancelled'` (or pauses workflow).
2. Generates an escalation alert for the Human Operator containing:
   - Target `company_id` and company name.
   - Failing `task_type` and assigned agent identity.
   - Exact error message, exception type, and execution context.
   - Recommended operator action (e.g., "Manually inspect website", "Resolve duplicate domain").
3. Pipeline progression for that specific prospect halts until the operator intervenes. Other prospects continue processing unhindered.
