# Phase 2.1 — Batch A, A.1 & A.2: Security & Authorization Hardening Report

**Document Version:** 2.1.0  
**Phase:** 2.1 — Batch A, Batch A.1 & Batch A.2: Residual Authorization Closure  
**Workspace:** `development-partnership/`  
**System Role:** Principal PostgreSQL Security Architect, Supabase Auth Engineer, & Database Integrity Auditor  
**Target Platform:** PostgreSQL 15+ (Supabase Native)  
**Status:** Residual Authorization Closure Complete — Fully Validated Locally (Design-Only Mode)  
**Date:** 2026-11-07  

---

## 1. Executive Summary & Operational Context

This report documents the architectural design, security analysis, and database schema hardening executed across **Phase 2.1 — Batch A**, **Batch A.1 (Authorization Closure)**, and **Batch A.2 (Residual Authorization Closure)** of the **Managed Development Partnership** platform.

### Operational Context & Stage Boundaries
- **Stage:** Pre-Operational Foundation.
- **Technical Team Status:** The planned technical delivery team is **not yet onboarded and not currently available for client delivery**.
- **Execution Boundary:** Strict Design and SQL Hardening only.
  - Zero external connections to Supabase.
  - Zero SQL commands executed against remote or local running databases.
  - Zero live migrations performed.
  - No work initiated on Batch B (State Machines & Lifecycle Enums) or Batch C (Extended Business Logic).
- **Business Alignment:** No modifications were made to business policies, pricing structures, revenue-share terms, payment amounts, or commercial tiers as codified in [`AGENTS.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/AGENTS.md) and [`business/commercial-policy.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/business/commercial-policy.md).

---

## 2. Comprehensive Inventory of Security Issues Fixed

The table below enumerates all security vulnerabilities and authorization bypasses resolved across Batch A, Batch A.1, and Batch A.2, along with the precise database-level hardening implemented:

| Vulnerability ID | Domain | Vulnerability / Bypass Description | Hardening / Exact Correction Implemented | File Reference |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-A01** | **Outreach Dispatch Gate** | `chk_outreach_mandatory_human_gate` and `fn_enforce_outreach_human_approval` only inspected `queued`, `sending`, and `sent`. Drafts could be transitioned directly to downstream event statuses (`delivered`, `opened`, `replied`, `bounced`, `failed`), bypassing human approval and pre-dispatch suppression checks. | Updated table constraint and trigger to guard all dispatch and downstream delivery statuses: `sending_status IN ('queued', 'sending', 'sent', 'delivered', 'opened', 'replied', 'bounced', 'failed')` strictly requires prior approved human sign-off and suppression registry validation. | [`crm/schema.sql#L630`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L630), [`#L1155`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1155) |
| **SEC-A02** | **Outreach Admin Override** | Ordinary human operators (`user_role = 'operator'`) could perform administrative overrides on outreach approvals simply by passing `is_admin_override = TRUE` in their SQL update, spoofing approvals without administrative privilege. | Implemented `is_admin_operator()` function validating trusted JWT `app_metadata.user_role IN ('admin', 'founder')`. Trigger strictly enforces that `is_admin_override = TRUE` requires `is_admin_operator() = TRUE`, non-empty `override_reason`, and `override_by_human_id = auth.uid()`. | [`crm/schema.sql#L73`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L73), [`#L1100`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1100) |
| **SEC-A03** | **Outreach Invalidation Gaps** | Invalidation trigger monitored only 6 fields (`body`, `subject`, `channel`, `contact_id`, `proposed_commercial_model`, `personalization_rationale`). Modifications to `company_id`, `campaign_id`, `sequence_step`, `parent_message_id`, or `personalization_evidence_ids` retained approved status despite materially altering message recipient and context. | Expanded invalidation condition in `fn_enforce_outreach_human_approval` across all 11 material message attributes. Any change resets `approval_status = 'draft'`, clears approver ID, timestamp, override metadata, and resets `sending_status = 'draft'`. | [`crm/schema.sql#L1060-L1085`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1060-L1085) |
| **SEC-A04** | **Outreach Approval Metadata Tampering** | Once approved, an actor could alter `approved_by_human_id`, `approved_at`, `is_admin_override`, or `override_reason` on an approved record without triggering invalidation or rejection. | Implemented immutability check in trigger blocking any modification to approval metadata (`approved_by_human_id`, `approved_at`, `is_admin_override`, `override_reason`, `override_by_human_id`) while `approval_status = 'approved'`. | [`crm/schema.sql#L1085-L1095`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1085-L1095) |
| **SEC-A05** | **Lead Qual Approval Invalidation Gaps** | `fn_enforce_lead_qualification_rules` only monitored the 7 dimension scores and evidence JSONB. Modifications to `company_id`, `qualification_tier`, `disqualification_reasons`, `disqualification_notes`, or `recommended_next_action` on approved records did not reset review status. | Expanded invalidation condition across all 14 fields affecting score, tier, evidence, disqualifiers, notes, or next action. Material modifications automatically reset `review_status = 'draft'`, `reviewed_by_human_id = NULL`, and `reviewed_at = NULL`. | [`crm/schema.sql#L1240-L1265`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1240-L1265) |
| **SEC-A06** | **Lead Qual Metadata Tampering** | Reviewer identity and timestamp could be altered on an approved qualification record. | Added database-level block in `fn_enforce_lead_qualification_rules` preventing modification of `reviewed_by_human_id` or `reviewed_at` while status remains `approved`. Enforced table constraint `chk_lead_qual_review_human_gate` ensuring unapproved rows cannot store reviewer metadata. | [`crm/schema.sql#L552`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L552), [`#L1270-L1275`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1270-L1275) |
| **SEC-A07** | **Deposit Actor Identity Absence** | Upfront milestone kickoff deposit (`deposit_paid`) tracked only boolean and timestamp (`deposit_paid_at`), lacking attribution to the human operator who confirmed receipt of funds. | Added column `deposit_confirmed_by_human_id UUID REFERENCES auth.users(id)` and table constraint `chk_project_deposit_sync` enforcing strict mutual consistency between boolean flag, actor ID, and timestamp. | [`crm/delivery-governance-expansion.sql#L188`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L188), [`#L213`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L213) |
| **SEC-A08** | **Uncontrolled Payment Confirmation** | Deposit and final payment confirmations had no database trigger verifying caller authorization; any caller with update privileges could set `deposit_paid = TRUE` or `final_payment_confirmed = TRUE` with arbitrary timestamps or IDs. | Implemented trigger function `fn_enforce_project_payment_governance`: false-to-true transitions require `auth.uid() IS NOT NULL`, `is_human_operator() = TRUE`, agents strictly prohibited, recorded actor must match `auth.uid()`, and timestamps are overwritten via `clock_timestamp()`. | [`crm/delivery-governance-expansion.sql#L355-L485`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L355-L485) |
| **SEC-A09** | **Unaudited Payment Reversal** | Payment confirmation flags could be cleared silently without administrative authority or documented business rationale. | Designed auditable correction model: columns `payment_reversal_reason`, `payment_reversal_by_human_id`, `payment_reversal_at`. Reversals require `is_admin_operator() = TRUE` and non-empty reason; final payment reversal is blocked if project is in terminal `closed` status. | [`crm/delivery-governance-expansion.sql#L195-L225`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L195-L225), [`#L425-L450`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L425-L450) |
| **SEC-A10** | **Delivery Readiness Gate Premature Sign-Off** | `fn_enforce_project_delivery_authorization` allowed activating delivery team readiness (`team_delivery_authorized = TRUE`) even if Technical Lead feasibility had not been approved (`feasibility_status != 'approved_by_lead'`). | Trigger now enforces that delivery readiness authorization strictly requires prior Technical Lead feasibility sign-off (`feasibility_status = 'approved_by_lead'`). | [`crm/delivery-governance-expansion.sql#L573`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L573) |
| **SEC-A11** | **Change Request Approval Tampering** | Reviewer identity and partner approval identity on `project_change_requests` could be mutated after determinations were made. | Added immutability guards in `fn_enforce_change_request_governance` blocking alteration of reviewer ID, timestamp, partner approver ID, or decision timestamp on approved change requests. | [`crm/delivery-governance-expansion.sql#L865-L885`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L865-L885) |
| **SEC-A12** | **Agent Delivery Table Access** | `crm/delivery-governance-expansion.sql` contained four broad policies granting all agents read access to commercial and delivery records. | Completely removed all agent policies from delivery tables. In Phase 2.1 pre-operational stage, subagents have zero delivery roles; RLS defaults to DENY for all agent callers. | [`crm/delivery-governance-expansion.sql#L985-L995`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L985-L995) |
| **SEC-A13** | **Unrestricted or Missing Function Grants** | `is_admin_operator` was not defined; `is_technical_lead` was not granted EXECUTE to `authenticated`; agent exclusion was not defensive in `is_technical_lead`. | Hardened `is_technical_lead()` to explicitly verify `user_role != 'agent'`; granted EXECUTE on `is_admin_operator` and `is_technical_lead` to `authenticated`. | [`crm/schema.sql#L1700`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1700), [`crm/delivery-governance-expansion.sql#L25-L35`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L25-L35) |
| **SEC-A15** | **Agent Task Scope Manipulation** | Agents had UPDATE privileges on their assigned tasks. An agent could update `company_id`, `contact_id`, or `assigned_actor_identity`, effectively reassigning tasks or broadening its own access scope to unrelated companies across all child RLS policies. | Implemented trigger `fn_enforce_task_governance` on `tasks`. Any attempt by an agent to modify `company_id`, `contact_id`, `campaign_id`, `assigned_actor_type`, `assigned_actor_identity`, `task_type`, `title`, `priority`, `due_date`, or `created_at` raises an immediate security exception. Agents may only update operational fields (`status`, `description`), and `completed_at` is database-stamped. | [`crm/schema.sql#L1360-L1415`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1360-L1415) |
| **SEC-A16** | **Unscoped Agent INSERT Permissions** | Agent INSERT policies for research reports, findings, qualifications, outreach messages, and activities did not verify whether the agent held an active task for the target company/contact. An agent could create unsolicited drafts or findings for any company. | Hardened all 5 agent INSERT policies (`agent_insert_research_reports`, `agent_insert_research_findings`, `agent_insert_lead_qualifications`, `agent_insert_outreach_messages`, `agent_insert_activities`) to require an active task (`status IN ('pending', 'in_progress')`) assigned to the agent's exact identity (`current_agent_identity()`) matching the target `company_id`. | [`crm/schema.sql#L1675-L1845`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1675-L1845) |
| **SEC-A17** | **Child-Parent Entity Authorization Mismatch** | `agent_insert_research_findings` only checked that the finding belonged to a report authored by the agent, but did not check company assignment. Similarly, outreach messages and activities could target contacts unrelated to assigned tasks. | Hardened `agent_insert_research_findings` to verify parent report `company_id` matches the finding `company_id` and active task. Hardened `agent_insert_outreach_messages` to require task contact matching (`t.contact_id IS NULL OR t.contact_id = target.contact_id`). | [`crm/schema.sql#L1700-L1720`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1700-L1720), [`#L1800-L1815`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1800-L1815) |
| **SEC-A18** | **Payment Reversal Metadata Manipulation** | Payment reversal metadata (`payment_reversal_reason`, `payment_reversal_by_human_id`, `payment_reversal_at`) could potentially be updated or cleared independently of an actual payment reversal (`deposit_paid` or `final_payment_confirmed` transitioning `TRUE -> FALSE`). Initial projects on INSERT could also inject fake reversal metadata. | Overhauled `fn_enforce_project_payment_governance`: on INSERT, reversal metadata cannot be initialized; on UPDATE, any alteration or clearance of `payment_reversal_*` outside a genuine payment reversal transition raises `'Security Policy Violation: Payment reversal metadata cannot be independently inserted, modified, or cleared outside of an authorized payment reversal.'` | [`crm/delivery-governance-expansion.sql#L370-L475`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L370-L475) |
| **SEC-A19** | **Broad Agent Directory & Domain Dumping** | `agent_select_companies` and `agent_select_company_domains` allowed any agent to read all non-suppressed companies and all domains in the database, even companies the agent was never assigned to research or contact. | Scoped `agent_select_companies` and `agent_select_company_domains` to companies actively assigned to the agent in `tasks` (`status IN ('pending', 'in_progress')`). Unassigned companies and domains return zero rows to agents. | [`crm/schema.sql#L1615-L1650`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1615-L1650) |
| **SEC-A20** | **Suppression Registry Table Exposure** | `agent_select_suppression` allowed all agents to query `suppression_registry` directly, exposing all suppressed email addresses, domains, LinkedIn handles, reasons, and source history. | Completely deleted `agent_select_suppression` RLS policy (agents receive zero direct SELECT on the table). Implemented trusted security-definer helper [`is_identifier_suppressed()`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L905-L930) returning a single boolean without exposing the ledger. | [`crm/schema.sql#L905-L930`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L905-L930) |
| **SEC-A21** | **Permissive Contact-Level Activity Logging** | `agent_insert_activities` permitted an agent to insert activities with `contact_id = NULL` even when the authorizing active Task was explicitly restricted to a specific Contact. | Removed permissive `activities.contact_id IS NULL` clause. Enforced: `AND (t.contact_id IS NULL OR t.contact_id = activities.contact_id)`. If Task has a Contact, Activity must reference exactly that Contact. | [`crm/schema.sql#L1830-L1845`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1830-L1845) |
| **SEC-A22** | **Unrestricted Drafter Campaign Access** | `agent_select_campaigns` allowed all Drafter subagents to read all active campaigns regardless of assignment, creating broad information leakage across outbound prospecting initiatives. | Added `campaign_id` FK on `tasks` (immutable to agents in `fn_enforce_task_governance`). Replaced RLS policy with explicit task requirement: Drafter can only read campaigns referenced by an active task assigned to their exact identity. | [`crm/schema.sql#L740`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L740), [`#L1665-L1675`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1665-L1675) |
| **SEC-A23** | **Unbounded Suppression Lookup Probing** | `is_identifier_suppressed()` was exposed to authenticated users without agent task validation, enabling subagents to probe arbitrary email addresses, domains, and LinkedIn handles to infer suppression status. | Hardened `is_identifier_suppressed()`: unauthenticated calls raise exceptions; human operator operational access preserved; agent callers require active task and queried identifier must demonstrably belong to assigned company/contact scope (out-of-scope queries raise exceptions). Table trigger checks `suppression_registry` directly. | [`crm/schema.sql#L905-L1015`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L905-L1015), [`#L1170`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1170) |
| **SEC-A24** | **Task Company/Contact Referential Disconnect** | `tasks` table used independent foreign keys for `company_id` and `contact_id`, permitting tasks to associate Contact B with Company A, or reference a contact without a company. | Added composite foreign key `fk_tasks_contact_company FOREIGN KEY (contact_id, company_id) REFERENCES contacts(id, company_id) ON DELETE CASCADE` and check constraint `chk_tasks_contact_requires_company CHECK (contact_id IS NULL OR company_id IS NOT NULL)`. | [`crm/schema.sql#L750-L760`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L750-L760) |

---

## 3. Detailed Architectural & Schema Modifications

### 3.1. Task Governance & Scope Immutability ([`crm/schema.sql#L1360`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1360))

To prevent agents from manipulating task assignments or expanding their authorization scope, a dedicated `BEFORE UPDATE` trigger on `tasks` blocks mutation of:
- `assigned_actor_type` and `assigned_actor_identity` (prevent re-assignment)
- `company_id`, `contact_id`, and `campaign_id` (prevent scope expansion)
- `task_type`, `title`, `priority`, `due_date`, and `created_at` (protect task governance)

Agents may only update operational fields (`status`, `description`), and completion timestamps are database-generated.

### 3.2. Task-Scoped Agent INSERT Governance ([`crm/schema.sql#L1675-L1845`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1675-L1845))

Every agent INSERT policy validates:
1. Exact subagent role match (`is_agent_type('researcher' | 'qualifier' | 'drafter')`).
2. Exact actor identity match (`current_agent_identity()`).
3. An active task in `tasks` with `assigned_actor_identity = current_agent_identity()` and `status IN ('pending', 'in_progress')` matching the target `company_id`.
4. Where applicable, contact-level alignment (`t.contact_id IS NULL OR t.contact_id = target.contact_id`).
5. For outreach messages, campaign alignment (`t.campaign_id IS NULL OR t.campaign_id = target.campaign_id`).
6. For findings, parent report ownership (`r.research_agent_identity = current_agent_identity()`).

### 3.3. Payment Reversal & Confirmation Hardening ([`crm/delivery-governance-expansion.sql#L355-L485`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L355-L485))

`fn_enforce_project_payment_governance` explicitly tracks status transitions:
- **`v_is_deposit_confirming` / `v_is_final_confirming`**: False-to-true requires human operator, matching `auth.uid()`, and database-stamped timestamp.
- **`v_is_deposit_reversing` / `v_is_final_reversing`**: True-to-false requires `is_admin_operator() = TRUE`, non-empty `payment_reversal_reason`, and database-stamped reversal actor and timestamp. Prohibited on closed projects.
- **No-Transition Protection**: If no reversal is taking place, any attempt to insert, update, or clear `payment_reversal_reason`, `payment_reversal_by_human_id`, or `payment_reversal_at` throws an exception.
- **INSERT Behavior**: Initial projects cannot set reversal metadata or confirmed final payment.

### 3.4. Strict Contact-Level Activity Scoping (SEC-A21) ([`crm/schema.sql#L1830-L1845`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1830-L1845))

Previously, `agent_insert_activities` allowed inserting activities with `contact_id = NULL` even when the assigned task was scoped to a specific contact. The policy now requires:
```sql
CREATE POLICY agent_insert_activities ON activities FOR INSERT TO authenticated 
    WITH CHECK (
        is_agent() AND 
        performed_by_actor_type = 'agent' AND 
        performed_by_actor_identity = current_agent_identity() AND
        EXISTS (
            SELECT 1 FROM tasks t 
            WHERE t.assigned_actor_type = 'agent' 
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.company_id = activities.company_id
              AND (t.contact_id IS NULL OR t.contact_id = activities.contact_id)
              AND t.status IN ('pending', 'in_progress')
        )
    );
```
- When `t.contact_id` is set, `activities.contact_id` cannot be NULL and must match `t.contact_id`.
- When `t.contact_id` is NULL, company-level activity logging is permitted.

### 3.5. Campaign Read Isolation & Relational Task Association (SEC-A22) ([`crm/schema.sql#L740`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L740), [`#L1665`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1665))

- Added `campaign_id UUID REFERENCES outreach_campaigns(id) ON DELETE SET NULL` to `tasks`.
- Created partial index `idx_tasks_campaign ON tasks(campaign_id) WHERE campaign_id IS NOT NULL`.
- Enforced campaign immutability for agents in `fn_enforce_task_governance()`.
- Hardened `agent_select_campaigns`:
```sql
CREATE POLICY agent_select_campaigns ON outreach_campaigns FOR SELECT TO authenticated 
    USING (
        is_agent() AND 
        is_agent_type('drafter') AND 
        is_active = TRUE AND
        EXISTS (
            SELECT 1 FROM tasks t
            WHERE t.assigned_actor_type = 'agent'
              AND t.assigned_actor_identity = current_agent_identity()
              AND t.campaign_id = outreach_campaigns.id
              AND t.status IN ('pending', 'in_progress')
        )
    );
```
- Human operator campaign access is preserved via `human_operator_all_outreach_campaigns`.

### 3.6. Suppression Lookup Authorization & Task Scoping (SEC-A23) ([`crm/schema.sql#L905-L1015`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L905-L1015))

`is_identifier_suppressed()` was hardened against enumeration:
- **Authentication Guard:** Callers without `auth.uid()` immediately raise a security exception.
- **Human Operators:** Preserves legitimate verification across all identifiers.
- **Agent Callers:** Requires an active task for `current_agent_identity()`. The queried identifier must demonstrably belong to the assigned company/contact (email/linkedin/phone on contact, or primary domain/domain alias on company).
- **Probing Block:** Queries for out-of-scope identifiers raise exceptions rather than returning `FALSE`, preventing boolean inference attacks.
- **Normalization:** Evaluates `LOWER(TRIM(COALESCE(p_identifier, '')))`.
- **Search Path:** Fixed safe `SET search_path = pg_catalog, public`.
- **Privilege Lockdown:** `REVOKE ALL ON FUNCTION is_identifier_suppressed FROM PUBLIC, anon; GRANT EXECUTE TO authenticated;`.
- **Database Dispatch Gate:** `fn_enforce_outreach_human_approval` verifies suppression directly against `suppression_registry sr`, guaranteeing dispatch protection independent of caller session context.

### 3.7. Task Company/Contact Composite Referential Integrity (SEC-A24) ([`crm/schema.sql#L750-L760`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L750-L760))

To guarantee that tasks cannot reference contacts belonging to other companies or reference a contact without a company:
```sql
CONSTRAINT fk_tasks_contact_company FOREIGN KEY (contact_id, company_id) 
    REFERENCES contacts(id, company_id) ON DELETE CASCADE,

CONSTRAINT chk_tasks_contact_requires_company CHECK (
    contact_id IS NULL OR company_id IS NOT NULL
)
```
- Leveraging `contacts` unique constraint `uq_contacts_id_company UNIQUE (id, company_id)`.
- If both are set, contact must belong to company.
- If `contact_id` is set and `company_id` is NULL, check constraint rejects the insert/update.
- If both are NULL, permitted for company-agnostic internal system tasks.

---

## 4. Complete Agent Access & Permission Matrix

| Database Entity | Anonymous (`anon`) | Human Operator | Researcher Agent (`researcher`) | Qualifier Agent (`qualifier`) | Drafter Agent (`drafter`) | Unknown Roles |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`companies`** | Denied | Full CRUD | SELECT (Assigned Tasks & Active) | SELECT (Assigned Tasks & Active) | SELECT (Assigned Tasks & Active) | Denied |
| **`company_domains`** | Denied | Full CRUD | SELECT (Assigned Tasks Only) | SELECT (Assigned Tasks Only) | SELECT (Assigned Tasks Only) | Denied |
| **`contacts`** | Denied | Full CRUD | SELECT (Assigned Tasks & Non-opted-out) | SELECT (Assigned Tasks & Non-opted-out) | SELECT (Assigned Tasks & Non-opted-out) | Denied |
| **`suppression_registry`**| Denied | Full CRUD | **DENIED** (Use scoped helper) | **DENIED** (Use scoped helper) | **DENIED** (Use scoped helper) | Denied |
| **`research_reports`** | Denied | Full CRUD | INSERT & SELECT (Assigned Tasks & Own ID) | SELECT (Assigned Tasks Only) | SELECT (Assigned Tasks Only) | Denied |
| **`research_findings`** | Denied | Full CRUD | INSERT & SELECT (Assigned Tasks & Parent Author) | SELECT (Assigned Tasks Only) | SELECT (Assigned Tasks Only) | Denied |
| **`lead_qualifications`** | Denied | Full CRUD | **DENIED** | INSERT (Draft & Assigned) / SELECT (Assigned) | SELECT (Approved & Assigned Only) | Denied |
| **`outreach_campaigns`** | Denied | Full CRUD | **DENIED** | **DENIED** | SELECT (Active & Assigned Tasks Only) | Denied |
| **`outreach_messages`** | Denied | Full CRUD | **DENIED** | **DENIED** | INSERT (Draft & Assigned) / SELECT (Assigned) | Denied |
| **`conversations`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`conversation_messages`**| Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`activities`** | Denied | Full CRUD | INSERT & SELECT (Assigned Tasks, Own ID, Strict Contact Match) | INSERT & SELECT (Assigned Tasks, Own ID, Strict Contact Match) | INSERT & SELECT (Assigned Tasks, Own ID, Strict Contact Match) | Denied |
| **`tasks`** | Denied | Full CRUD | SELECT & UPDATE (Assigned Only; Scope Immutable) | SELECT & UPDATE (Assigned Only; Scope Immutable) | SELECT & UPDATE (Assigned Only; Scope Immutable) | Denied |
| **`audit_logs`** | Denied | SELECT Only | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`partnerships`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`projects`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`project_milestones`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`project_change_requests`**| Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |

---

## 5. Security Test Matrix

The following test matrix classifies every security control with expected outcomes and truth-based verification status (distinguishing static design analysis from runtime execution):

| Test ID | Test Scenario | Actor Role | Target Entity & Operation | Test Vectors / Injected Payload | Expected Behavior | Static Verification Result | Runtime Execution Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TSK-01** | Agent Task Progress Update | Drafter Agent | `UPDATE tasks SET status = 'completed'` | Updating `status` on assigned task from `in_progress` to `completed`. | **EXPECTED PASS** | Statically verified via trigger and RLS policy | **NOT TESTED** |
| **TSK-02** | Agent Task Scope Manipulation (Company) | Researcher Agent | `UPDATE tasks SET company_id = Unassigned_Company_UUID` | Attempting to alter `company_id` on assigned task to expand scope. | **EXPECTED BLOCK** | Abort with Exception `'Agents cannot alter task company_id scope'` | **NOT TESTED** |
| **TSK-03** | Agent Task Scope Manipulation (Contact) | Drafter Agent | `UPDATE tasks SET contact_id = Other_Contact_UUID` | Attempting to alter `contact_id` on assigned task. | **EXPECTED BLOCK** | Abort with Exception `'Agents cannot alter task contact_id scope'` | **NOT TESTED** |
| **TSK-04** | Agent Identity Manipulation | Qualifier Agent | `UPDATE tasks SET assigned_actor_identity = Other_Agent_ID` | Attempting to reassign task to another agent. | **EXPECTED BLOCK** | Abort with Exception `'Agents cannot reassign tasks or alter task actor assignment'` | **NOT TESTED** |
| **TSK-05** | Agent Task Campaign Scope Manipulation | Drafter Agent | `UPDATE tasks SET campaign_id = Other_Campaign_UUID` | Attempting to alter `campaign_id` on assigned task. | **EXPECTED BLOCK** | Abort with Exception `'Agents cannot alter task campaign_id scope'` | **NOT TESTED** |
| **TSK-06** | Agent Task Title/Priority Manipulation | Any Agent | `UPDATE tasks SET title = 'New Title', priority = 'urgent'` | Attempting to alter non-operational task attributes. | **EXPECTED BLOCK** | Abort with Exception `'Agents cannot alter task title'` | **NOT TESTED** |
| **INS-01** | Researcher Report Insertion (Assigned) | Researcher Agent | `INSERT INTO research_reports` | Inserting report for company actively assigned to agent in `tasks`. | **EXPECTED PASS** | Statically verified via `agent_insert_research_reports` | **NOT TESTED** |
| **INS-02** | Researcher Report Insertion (Unassigned) | Researcher Agent | `INSERT INTO research_reports` | Inserting report for company with no active task assigned to agent. | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (0 rows inserted) | **NOT TESTED** |
| **INS-03** | Qualifier Scorecard (Unassigned) | Qualifier Agent | `INSERT INTO lead_qualifications` | Inserting draft scorecard for company without active assigned task. | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (0 rows inserted) | **NOT TESTED** |
| **INS-04** | Drafter Outreach Insertion (Unassigned) | Drafter Agent | `INSERT INTO outreach_messages` | Inserting draft message for company without active assigned task. | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (0 rows inserted) | **NOT TESTED** |
| **INS-05** | Agent Activity Logging (Unassigned) | Any Agent | `INSERT INTO activities` | Inserting activity log for company without active assigned task. | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (0 rows inserted) | **NOT TESTED** |
| **INS-06** | Child Record Under Foreign Parent | Researcher Agent | `INSERT INTO research_findings` | Inserting finding linked to report authored by a different agent identity. | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (parent author mismatch) | **NOT TESTED** |
| **ACT-01** | Exact Contact Match Activity Logging | Any Agent | `INSERT INTO activities (company_id, contact_id)` | Task has Contact A, activity has Contact A. | **EXPECTED PASS** | Statically verified via `agent_insert_activities` | **NOT TESTED** |
| **ACT-02** | Contact Mismatch Activity Logging | Any Agent | `INSERT INTO activities (company_id, contact_id)` | Task has Contact A, activity specifies Contact B. | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (`t.contact_id = activities.contact_id` fails) | **NOT TESTED** |
| **ACT-03** | NULL Contact with Restricted Task | Any Agent | `INSERT INTO activities (company_id, contact_id)` | Task restricted to Contact A, activity omits contact (`contact_id = NULL`). | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (`t.contact_id = NULL` evaluates to false) | **NOT TESTED** |
| **ACT-04** | Company-Level Task with NULL Contact | Any Agent | `INSERT INTO activities (company_id, contact_id)` | Task has `contact_id = NULL`, activity omits contact (`contact_id = NULL`). | **EXPECTED PASS** | Statically verified (`t.contact_id IS NULL` is true) | **NOT TESTED** |
| **ACT-05** | Activity Logging Under Other Agent Task | Any Agent | `INSERT INTO activities` | Task belongs to Agent X, Agent Y attempts activity insert. | **EXPECTED BLOCK** | Blocked by RLS `WITH CHECK` (actor identity mismatch) | **NOT TESTED** |
| **CMP-01** | Assigned Drafter Campaign Access | Drafter Agent | `SELECT * FROM outreach_campaigns WHERE id = Campaign_A` | Drafter holding active task for Campaign A reads campaign. | **EXPECTED PASS** | Statically verified via `agent_select_campaigns` | **NOT TESTED** |
| **CMP-02** | Unassigned Drafter Campaign Access | Drafter Agent | `SELECT * FROM outreach_campaigns WHERE id = Campaign_B` | Drafter holding task for Campaign A queries unassigned Campaign B. | **EXPECTED BLOCK** | Returns 0 rows (no active task linking Drafter to Campaign B) | **NOT TESTED** |
| **CMP-03** | Non-Drafter Agent Campaign Access | Researcher Agent | `SELECT * FROM outreach_campaigns` | Researcher subagent attempts to query campaigns. | **EXPECTED BLOCK** | Returns 0 rows (`is_agent_type('drafter')` is false) | **NOT TESTED** |
| **CMP-04** | Human Operator Campaign Access | Human Operator | `SELECT * FROM outreach_campaigns` | Operator browsing all campaigns. | **EXPECTED PASS** | Handled by `human_operator_all_outreach_campaigns` | **NOT TESTED** |
| **SUP-01** | Agent Checks Assigned Contact Identifier | Drafter Agent | `SELECT is_identifier_suppressed('email', 'contact_a@example.com')` | Agent queries email of contact assigned in its active task. | **EXPECTED PASS** | Returns boolean (`TRUE` or `FALSE`) | **NOT TESTED** |
| **SUP-02** | Agent Checks Assigned Company Domain | Researcher Agent | `SELECT is_identifier_suppressed('domain', 'company-a.com')` | Agent queries primary domain of company assigned in active task. | **EXPECTED PASS** | Returns boolean (`TRUE` or `FALSE`) | **NOT TESTED** |
| **SUP-03** | Agent Checks Unrelated Identifier | Any Agent | `SELECT is_identifier_suppressed('email', 'unrelated@competitor.com')` | Agent probes arbitrary external email outside task scope. | **EXPECTED BLOCK** | Abort with Exception `'outside the authorized scope of active tasks'` | **NOT TESTED** |
| **SUP-04** | Agent Without Active Task Checks Lookup | Any Agent | `SELECT is_identifier_suppressed('domain', 'example.com')` | Agent with zero active tasks attempts suppression lookup. | **EXPECTED BLOCK** | Abort with Exception `'outside the authorized scope of active tasks'` | **NOT TESTED** |
| **SUP-05** | Human Operator Suppression Check | Human Operator | `SELECT is_identifier_suppressed('email', 'any@target.com')` | Human operator verifies suppression for an unassigned prospect. | **EXPECTED PASS** | Operational access granted; returns boolean | **NOT TESTED** |
| **SUP-06** | Unauthenticated Suppression Check | Anon / Public | `SELECT is_identifier_suppressed('email', 'target@test.com')` | Unauthenticated session invokes lookup helper. | **EXPECTED BLOCK** | Abort with Exception `'Authentication required for suppression lookup'` | **NOT TESTED** |
| **INT-01** | Valid Task Company & Contact Association | Human Operator | `INSERT INTO tasks (company_id, contact_id)` | Company A and Contact belonging to Company A. | **EXPECTED PASS** | Foreign key and check constraints satisfied | **NOT TESTED** |
| **INT-02** | Mismatched Task Company & Contact | Human Operator | `INSERT INTO tasks (company_id, contact_id)` | Company A and Contact belonging to Company B. | **EXPECTED BLOCK** | Abort with FK Violation on `fk_tasks_contact_company` | **NOT TESTED** |
| **INT-03** | Task with Company and NULL Contact | Human Operator | `INSERT INTO tasks (company_id, contact_id)` | Company A and `contact_id = NULL`. | **EXPECTED PASS** | Statically verified; check constraint satisfied | **NOT TESTED** |
| **INT-04** | Task with NULL Company and Populated Contact | Human Operator | `INSERT INTO tasks (company_id, contact_id)` | `company_id = NULL` and Contact A populated. | **EXPECTED BLOCK** | Abort with CHECK Constraint Violation `chk_tasks_contact_requires_company` | **NOT TESTED** |
| **INT-05** | Task with Both Company & Contact NULL | Human Operator | `INSERT INTO tasks (company_id, contact_id)` | Both `company_id` and `contact_id` are NULL. | **EXPECTED PASS** | Statically verified; general system task permitted | **NOT TESTED** |
| **PAY-01** | Operator Deposit Confirmation | Human Operator | `UPDATE projects SET deposit_paid = TRUE, deposit_confirmed_by_human_id = auth.uid()` | Operator confirming deposit with matching authenticated identity. | **EXPECTED PASS** | Statically verified in `fn_enforce_project_payment_governance` | **NOT TESTED** |
| **PAY-02** | Unauthenticated Payment Confirmation | Anon / Null Session | `UPDATE projects SET deposit_paid = TRUE` | Mutation without authenticated session (`auth.uid() IS NULL`). | **EXPECTED BLOCK** | Abort with Exception `'Payment confirmation requires an authenticated session'` | **NOT TESTED** |
| **PAY-03** | Agent Payment Confirmation | Any Agent | `UPDATE projects SET deposit_paid = TRUE` | Agent token attempting to confirm upfront kickoff deposit. | **EXPECTED BLOCK** | Abort with Exception `'Agents are strictly prohibited from modifying project payment parameters'` | **NOT TESTED** |
| **PAY-04** | Forged Confirmation Timestamp | Human Operator | `UPDATE projects SET deposit_paid = TRUE, deposit_paid_at = '2020-01-01'` | Caller passing forged historical timestamp during confirmation. | **EXPECTED PASS** (Overwritten) | Database overwrites timestamp via `clock_timestamp()` | **NOT TESTED** |
| **PAY-05** | Confirmed Metadata Tampering | Human Operator | `UPDATE projects SET deposit_confirmed_by_human_id = Other_ID WHERE deposit_paid = TRUE` | Attempting to mutate confirmed actor ID on confirmed deposit. | **EXPECTED BLOCK** | Abort with Exception `'Cannot alter deposit confirmation metadata on an already confirmed project'` | **NOT TESTED** |
| **PAY-06** | Operator Payment Reversal Attempt | Human Operator | `UPDATE projects SET deposit_paid = FALSE WHERE deposit_paid = TRUE` | Non-admin operator attempting to reverse confirmed deposit. | **EXPECTED BLOCK** | Abort with Exception `'Only authorized admin or founder roles can reverse a payment confirmation'` | **NOT TESTED** |
| **PAY-07** | Admin Payment Reversal with Reason | Admin / Founder | `UPDATE projects SET deposit_paid = FALSE, payment_reversal_reason = 'Payment bounced' WHERE deposit_paid = TRUE` | Admin reversing deposit with mandatory non-empty reason. | **EXPECTED PASS** | Statically verified; reversal metadata stamped via database | **NOT TESTED** |
| **PAY-08** | Independent Reversal Metadata Manipulation | Admin / Operator | `UPDATE projects SET payment_reversal_reason = 'Fake reason' WHERE deposit_paid = TRUE` | Modifying reversal metadata without changing payment status. | **EXPECTED BLOCK** | Abort with Exception `'Payment reversal metadata cannot be independently inserted, modified, or cleared outside of an authorized payment reversal'` | **NOT TESTED** |
| **PAY-09** | Reversal on Closed Project | Admin / Founder | `UPDATE projects SET final_payment_confirmed = FALSE, payment_reversal_reason = 'Correction' WHERE status = 'closed'` | Attempting to reverse final payment on closed project. | **EXPECTED BLOCK** | Abort with Exception `'Final payment confirmation cannot be reversed on a closed project'` | **NOT TESTED** |
| **PAY-10** | Reversal Metadata on INSERT | Human Operator | `INSERT INTO projects (payment_reversal_reason, ...)` | Attempting to inject reversal metadata on initial project creation. | **EXPECTED BLOCK** | Abort with Exception `'Payment reversal metadata cannot be initialized on new project creation'` | **NOT TESTED** |
| **RED-01** | Researcher Company Isolation | Researcher Agent | `SELECT * FROM companies` | Querying companies table with an active task for Company A only. | **EXPECTED PASS** (Scoped) | Returns only Company A; all unassigned companies filtered | **NOT TESTED** |
| **RED-02** | Drafter Outreach Isolation | Drafter Agent | `SELECT * FROM outreach_messages` | Drafter querying messages across companies. | **EXPECTED PASS** (Scoped) | Returns only drafts authored by agent or assigned via active tasks | **NOT TESTED** |
| **RED-03** | Unassigned Domain Access Attempt | Any Agent | `SELECT * FROM company_domains WHERE company_id = Unassigned_UUID` | Querying domains of unassigned prospect. | **EXPECTED BLOCK** | Returns 0 rows (blocked by task-scoped RLS policy) | **NOT TESTED** |
| **RED-04** | Suppression Registry Enumeration | Any Agent | `SELECT * FROM suppression_registry` | Agent attempting to browse or dump the suppression registry. | **EXPECTED BLOCK** | Returns 0 rows (no SELECT policy exists for agents) | **NOT TESTED** |

---

## 6. Static Validation Evidence

Static code analysis and AST syntax checks were conducted using Python 3.12 and `sqlparse`:

1. **SQL Parsing & AST Verification:**
   - [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql): **184 statements** parsed with zero syntax errors.
   - [`crm/delivery-governance-expansion.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/delivery-governance-expansion.sql): **61 statements** parsed with zero syntax errors.
2. **PL/pgSQL Block & Dollar Quote Integrity:**
   - All `$$` function definition blocks are perfectly balanced across both files (`crm/schema.sql`: 38 dollar quotes; `crm/delivery-governance-expansion.sql`: 16 dollar quotes).
   - All conditional `IF...ELSIF...ELSE...END IF;` and `CASE...END CASE;` constructs have verified closure.
3. **Database Schema Totals:**
   - Tables: **18 total** (14 Core + 4 Delivery Expansion).
   - Functions: **27 total** (19 Core + 8 Delivery Expansion).
   - Triggers: **35 total** (20 Core + 15 Delivery Expansion).
   - RLS Policies: **52 total** (44 Core + 8 Delivery Expansion).

---

## 7. Tests Not Executed (Runtime Environment Boundary)

In strict accordance with the task instructions (*"This is a design and SQL hardening task only. DO NOT connect to Supabase. DO NOT execute any SQL against a remote or local database. DO NOT perform a live migration"*), the following test categories were **NOT RUN**:

1. **Live PostgreSQL Runtime Execution:** No test transactions were submitted against a live Postgres daemon or Supabase instance.
2. **Supabase Auth Hook & Edge Function Integration:** Simulated JWT tokens were validated via static AST review of claim evaluation logic; live JWT signature verification via Supabase GoTrue was not run.
3. **End-to-End Task Queue Execution:** Automated subagent orchestration workflows via Hermes were not triggered against live tables.

*All status entries marked "NOT TESTED" truthfully reflect this boundary.*

---

## 8. Migration Implications & Data-Cleanup Prerequisites

Before applying the new constraints to any existing database containing data, the following pre-migration audit and cleanup must be executed:

1. **Task Contact-Company Referential Consistency Audit:**
   ```sql
   -- Find tasks with orphaned contact (contact set but company null)
   SELECT id, title, contact_id, company_id FROM tasks 
   WHERE contact_id IS NOT NULL AND company_id IS NULL;

   -- Find tasks with cross-company contact mismatches
   SELECT t.id, t.title, t.company_id AS task_company, c.company_id AS contact_company
   FROM tasks t
   JOIN contacts c ON c.id = t.contact_id
   WHERE t.company_id IS DISTINCT FROM c.company_id;
   ```
   *Action required if rows exist:* Backfill `tasks.company_id = contacts.company_id` or set `tasks.contact_id = NULL` before adding `fk_tasks_contact_company` and `chk_tasks_contact_requires_company`.

2. **Index Creation Prerequisite:**
   `idx_tasks_contact` and `idx_tasks_campaign` should be created concurrently (`CREATE INDEX CONCURRENTLY`) if applied to a live high-traffic database to avoid locking table updates.

---

## 9. Remaining Security Risks & Architectural Considerations

1. **Service-Role Key Protection:** Database triggers and RLS policies are completely bypassed if a caller uses the Supabase `service_role` secret. Rigorous secret management must prevent leakage of this credential to subagent environments.
2. **Task Creation Prerequisite:** Because all agent read and insert permissions are strictly task-scoped, Hermes Agent or human operators must insert a task into `tasks` before subagents can access or modify company intelligence. If tasks are not created, subagents receive empty result sets by design.
3. **JWT Custom Claim Issuance:** Database security depends on the integrity of `app_metadata` claims (`user_role`, `subagent_type`, `technical_role`, `agent_id`). These claims must be issued exclusively by trusted Supabase Auth Edge Functions or administrative hooks, never through client-editable user metadata (`user_metadata`).

---

## 10. Decisions Requiring Founder Input

1. **Decision D-01: Supabase Hosting Region:** European region (Frankfurt - `eu-central-1`) for GDPR posture vs. US East (N. Virginia - `us-east-1`) based on target partner distribution.
2. **Decision D-02: Base Commercial Currency:** Supported currencies include USD, EUR, GBP; default commercial currency model requires founder sign-off before milestone contracts execute.
3. **Decision D-03: Outreach Dispatch Service Provider:** Selection between Resend, Postmark, or dedicated agency SMTP infrastructure for outbound approved correspondence.
4. **Decision D-04: Audit Log Retention & Archiving:** Cold-storage S3/GCS archival policy for records older than 12 months.

---

## 11. Conclusion & Verification Summary

Batch A.2 Residual Authorization Closure has successfully resolved:
1. Strict contact-level activity scoping (**SEC-A21**), closing the NULL-contact bypass for contact-restricted tasks.
2. Drafter campaign read isolation (**SEC-A22**), establishing relational campaign scoping via `tasks.campaign_id`.
3. Suppression lookup probing prevention (**SEC-A23**), transforming `is_identifier_suppressed()` into a task-scoped, exception-guarded lookup while keeping the pre-dispatch trigger gate direct.
4. Database-level task company/contact referential integrity (**SEC-A24**), enforcing composite foreign keys and mandatory company references.

All changes have passed static AST verification with zero errors and remain uncommitted in the local repository awaiting human authorization.
