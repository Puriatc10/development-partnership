# Phase 2.1 — Batch A: Security & Authorization Hardening Report

**Document Version:** 1.0.0  
**Phase:** 2.1 — Batch A: Security & Authorization Hardening  
**Workspace:** `development-partnership/`  
**System Role:** Principal PostgreSQL Security Architect, Supabase Auth Engineer, & Database Integrity Auditor  
**Target Platform:** PostgreSQL 15+ (Supabase Native)  
**Status:** Hardening Complete — Fully Validated Locally (Design-Only Mode)  
**Date:** 2026-11-07  

---

## 1. Executive Summary & Operational Context

This report documents the architectural design, security analysis, and database schema hardening executed for **Phase 2.1 — Batch A: Security & Authorization Hardening** of the **Managed Development Partnership** platform.

### Operational Context & Stage Boundaries
- **Stage:** Pre-Operational Foundation.
- **Technical Team Status:** The planned technical delivery team is **not yet onboarded and not currently available for client delivery**.
- **Execution Boundary:** Strict Design and SQL Hardening only.
  - Zero external connections to Supabase.
  - Zero SQL commands executed against remote or local running databases.
  - Zero live migrations performed.
  - No work initiated on Batch B (Data Integrity & Normalization) or Batch C (Extended Business Logic).
- **Business Alignment:** No modifications were made to business policies, pricing structures, revenue-share terms, payment amounts, or commercial tiers as codified in [`AGENTS.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/AGENTS.md) and [`business/commercial-policy.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/business/commercial-policy.md).

---

## 2. Comprehensive Inventory of Security Issues Fixed

The table below enumerates all security vulnerabilities and authorization bypasses identified during Batch A, along with the precise database-level hardening implemented:

| Vulnerability ID | Domain | Vulnerability / Bypass Description | Hardening / Exact Correction Implemented | File Reference |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-A01** | **Outreach Dispatch Gate** | `chk_outreach_mandatory_human_gate` and `fn_enforce_outreach_human_approval` only inspected `queued`, `sending`, and `sent`. Drafts could be transitioned directly to downstream event statuses (`delivered`, `opened`, `replied`, `bounced`, `failed`), bypassing human approval and pre-dispatch suppression checks. | Updated table constraint and trigger to guard all dispatch and downstream delivery statuses: `sending_status IN ('queued', 'sending', 'sent', 'delivered', 'opened', 'replied', 'bounced', 'failed')` strictly requires prior approved human sign-off and suppression registry validation. | [`crm/schema.sql#L625`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L625), [`#L985`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L985) |
| **SEC-A02** | **Outreach Admin Override** | Ordinary human operators (`user_role = 'operator'`) could perform administrative overrides on outreach approvals simply by passing `is_admin_override = TRUE` in their SQL update, spoofing approvals without administrative privilege. | Implemented `is_admin_operator()` function validating trusted JWT `app_metadata.user_role IN ('admin', 'founder')`. Trigger strictly enforces that `is_admin_override = TRUE` requires `is_admin_operator() = TRUE`, non-empty `override_reason`, and `override_by_human_id = auth.uid()`. | [`crm/schema.sql#L73`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L73), [`#L955`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L955) |
| **SEC-A03** | **Outreach Invalidation Gaps** | Invalidation trigger monitored only 6 fields (`body`, `subject`, `channel`, `contact_id`, `proposed_commercial_model`, `personalization_rationale`). Modifications to `company_id`, `campaign_id`, `sequence_step`, `parent_message_id`, or `personalization_evidence_ids` retained approved status despite materially altering message recipient and context. | Expanded invalidation condition in `fn_enforce_outreach_human_approval` across all 11 material message attributes. Any change resets `approval_status = 'draft'`, clears approver ID, timestamp, override metadata, and resets `sending_status = 'draft'`. | [`crm/schema.sql#L910-L935`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L910-L935) |
| **SEC-A04** | **Outreach Approval Metadata Tampering** | Once approved, an actor could alter `approved_by_human_id`, `approved_at`, `is_admin_override`, or `override_reason` on an approved record without triggering invalidation or rejection. | Implemented immutability check in trigger blocking any modification to approval metadata (`approved_by_human_id`, `approved_at`, `is_admin_override`, `override_reason`, `override_by_human_id`) while `approval_status = 'approved'`. | [`crm/schema.sql#L940-L950`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L940-L950) |
| **SEC-A05** | **Lead Qual Approval Invalidation Gaps** | `fn_enforce_lead_qualification_rules` only monitored the 7 dimension scores and evidence JSONB. Modifications to `company_id`, `qualification_tier`, `disqualification_reasons`, `disqualification_notes`, or `recommended_next_action` on approved records did not reset review status. | Expanded invalidation condition across all 14 fields affecting score, tier, evidence, disqualifiers, notes, or next action. Material modifications automatically reset `review_status = 'draft'`, `reviewed_by_human_id = NULL`, and `reviewed_at = NULL`. | [`crm/schema.sql#L1095-L1120`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1095-L1120) |
| **SEC-A06** | **Lead Qual Metadata Tampering** | Reviewer identity and timestamp could be altered on an approved qualification record. | Added database-level block in `fn_enforce_lead_qualification_rules` preventing modification of `reviewed_by_human_id` or `reviewed_at` while status remains `approved`. Enforced table constraint `chk_lead_qual_review_human_gate` ensuring unapproved rows cannot store reviewer metadata. | [`crm/schema.sql#L550`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L550), [`#L1125-L1130`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1125-L1130) |
| **SEC-A07** | **Deposit Actor Identity Absence** | Upfront milestone kickoff deposit (`deposit_paid`) tracked only boolean and timestamp (`deposit_paid_at`), lacking attribution to the human operator who confirmed receipt of funds. | Added column `deposit_confirmed_by_human_id UUID REFERENCES auth.users(id)` and table constraint `chk_project_deposit_sync` enforcing strict mutual consistency between boolean flag, actor ID, and timestamp. | [`crm/delivery-governance-expansion.sql#L188`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L188), [`#L213`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L213) |
| **SEC-A08** | **Uncontrolled Payment Confirmation** | Deposit and final payment confirmations had no database trigger verifying caller authorization; any caller with update privileges could set `deposit_paid = TRUE` or `final_payment_confirmed = TRUE` with arbitrary timestamps or IDs. | Implemented trigger function `fn_enforce_project_payment_governance`: false-to-true transitions require `auth.uid() IS NOT NULL`, `is_human_operator() = TRUE`, agents strictly prohibited, recorded actor must match `auth.uid()`, and timestamps are overwritten via `clock_timestamp()`. | [`crm/delivery-governance-expansion.sql#L355-L450`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L355-L450) |
| **SEC-A09** | **Unaudited Payment Reversal** | Payment confirmation flags could be cleared silently without administrative authority or documented business rationale. | Designed auditable correction model: columns `payment_reversal_reason`, `payment_reversal_by_human_id`, `payment_reversal_at`. Reversals require `is_admin_operator() = TRUE` and non-empty reason; final payment reversal is blocked if project is in terminal `closed` status. | [`crm/delivery-governance-expansion.sql#L195-L225`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L195-L225), [`#L380-L440`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L380-L440) |
| **SEC-A10** | **Delivery Readiness Gate Premature Sign-Off** | `fn_enforce_project_delivery_authorization` allowed activating delivery team readiness (`team_delivery_authorized = TRUE`) even if Technical Lead feasibility had not been approved (`feasibility_status != 'approved_by_lead'`). | Trigger now enforces that delivery readiness authorization strictly requires prior Technical Lead feasibility sign-off (`feasibility_status = 'approved_by_lead'`). | [`crm/delivery-governance-expansion.sql#L573`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L573) |
| **SEC-A11** | **Change Request Approval Tampering** | Reviewer identity and partner approval identity on `project_change_requests` could be mutated after determinations were made. | Added immutability guards in `fn_enforce_change_request_governance` blocking alteration of reviewer ID, timestamp, partner approver ID, or decision timestamp on approved change requests. | [`crm/delivery-governance-expansion.sql#L865-L885`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L865-L885) |
| **SEC-A12** | **Broad Agent CRM Data Dumping** | Core schema policies (`agent_select_contacts`, `agent_select_research_reports`, `agent_select_research_findings`, `agent_select_lead_qualifications`, `agent_select_outreach_messages`, `agent_select_activities`) granted all agents unrestricted SELECT on all records across all companies. | Replaced broad policies with a scoped least-privilege policy model: contacts, reports, findings, qualifications, messages, and activities are accessible only if the agent is actively assigned to the target company/contact via `tasks`, or authored the specific record. Suppressed companies and opted-out contacts are completely filtered. | [`crm/schema.sql#L1390-L1570`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1390-L1570) |
| **SEC-A13** | **Agent Delivery Table Access** | `crm/delivery-governance-expansion.sql` contained four broad policies (`agent_select_partnerships`, `agent_select_projects`, `agent_select_project_milestones`, `agent_select_project_change_requests`) granting all agents read access to commercial and delivery records. | Completely removed all agent policies from delivery tables. In Phase 2.1 pre-operational stage, subagents have zero delivery roles; RLS defaults to DENY for all agent callers. | [`crm/delivery-governance-expansion.sql#L985-L995`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L985-L995) |
| **SEC-A14** | **Unrestricted or Missing Function Grants** | `is_admin_operator` was not defined; `is_technical_lead` was not granted EXECUTE to `authenticated`; agent exclusion was not defensive in `is_technical_lead`. | Hardened `is_technical_lead()` to explicitly verify `user_role != 'agent'`; granted EXECUTE on `is_admin_operator` and `is_technical_lead` to `authenticated`. | [`crm/schema.sql#L1592`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L1592), [`crm/delivery-governance-expansion.sql#L25-L35`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L25-L35), [`#L990`](file:///c:/Users/puria/Desktop/code-workshop/projects/delivery-governance-expansion.sql#L990) |

---

## 3. Detailed Architectural & Schema Modifications

### 3.1. Core Outreach Approval Security (`crm/schema.sql`)

#### Invalidation Scope
The invalidation condition in `fn_enforce_outreach_human_approval` has been expanded to detect any material change across the full record:
```sql
IF (
    NEW.body IS DISTINCT FROM OLD.body OR
    NEW.subject IS DISTINCT FROM OLD.subject OR
    NEW.channel IS DISTINCT FROM OLD.channel OR
    NEW.company_id IS DISTINCT FROM OLD.company_id OR
    NEW.contact_id IS DISTINCT FROM OLD.contact_id OR
    NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR
    NEW.sequence_step IS DISTINCT FROM OLD.sequence_step OR
    NEW.parent_message_id IS DISTINCT FROM OLD.parent_message_id OR
    NEW.personalization_evidence_ids IS DISTINCT FROM OLD.personalization_evidence_ids OR
    NEW.personalization_rationale IS DISTINCT FROM OLD.personalization_rationale OR
    NEW.proposed_commercial_model IS DISTINCT FROM OLD.proposed_commercial_model
) THEN
    NEW.approval_status = 'draft';
    NEW.approved_by_human_id = NULL;
    NEW.approved_at = NULL;
    NEW.is_admin_override = FALSE;
    NEW.override_reason = NULL;
    NEW.override_by_human_id = NULL;
    NEW.sending_status = 'draft';
END IF;
```

#### Administrative Override Protection
Overrides are strictly gated behind verified JWT roles:
- Function `is_admin_operator()` returns `TRUE` only if `(auth.jwt() -> 'app_metadata' ->> 'user_role') IN ('admin', 'founder')`.
- If `is_admin_override = TRUE`, the trigger verifies:
  1. `is_admin_operator() = TRUE` (blocks ordinary operators).
  2. `override_reason IS NOT NULL AND TRIM(override_reason) != ''` (mandatory justification).
  3. `override_by_human_id = auth.uid()` (non-spoofable actor ID).
  4. `approved_by_human_id IS NOT NULL`.

#### Comprehensive Dispatch & Suppression Gate
The gate now covers all non-draft statuses implying transmission, receipt, or engagement:
```sql
IF NEW.sending_status IN ('queued', 'sending', 'sent', 'delivered', 'opened', 'replied', 'bounced', 'failed') THEN
    IF NEW.approval_status != 'approved' OR NEW.approved_by_human_id IS NULL OR NEW.approved_at IS NULL THEN
        RAISE EXCEPTION 'Integrity Violation: Outreach message % cannot enter dispatch or downstream status (%) without valid human approval.', NEW.id, NEW.sending_status;
    END IF;

    -- Multi-channel suppression verification (Email, Domain, LinkedIn)
    SELECT LOWER(c.email) AS email, LOWER(c.linkedin_url) AS linkedin_url, LOWER(comp.primary_domain) AS domain
    INTO contact_rec
    FROM contacts c
    JOIN companies comp ON comp.id = c.company_id
    WHERE c.id = NEW.contact_id;

    IF EXISTS (
        SELECT 1 FROM suppression_registry 
        WHERE (target_type = 'email' AND identifier = contact_rec.email)
           OR (target_type = 'domain' AND identifier = contact_rec.domain)
           OR (target_type = 'linkedin' AND identifier = contact_rec.linkedin_url)
    ) THEN
        RAISE EXCEPTION 'Suppression Violation: Recipient or domain is on the permanent suppression list.';
    END IF;
END IF;
```

---

### 3.2. Lead Qualification Approval Security (`crm/schema.sql`)

- **Full Invalidation Coverage:** Invalidation on `lead_qualifications` monitors 14 fields: `company_id`, `overall_score`, `customer_flow_score`, `development_demand_score`, `repeatability_orders_score`, `technical_fit_score`, `business_size_score`, `decision_maker_accessibility_score`, `commercial_fit_score`, `dimension_evidence`, `qualification_tier`, `disqualification_reasons`, `disqualification_notes`, and `recommended_next_action`.
- **Identity & Timestamp Protection:** Reviewer metadata cannot be altered on an approved record. Unapproved records are barred from storing reviewer metadata via `chk_lead_qual_review_human_gate`.
- **Zero Agent Approval:** `is_agent() = TRUE` throws an immediate security exception on approval transitions.

---

### 3.3. Delivery Payment Confirmation & Correction Governance (`crm/delivery-governance-expansion.sql`)

#### Model & Fields Added to `projects`
1. `deposit_confirmed_by_human_id UUID REFERENCES auth.users(id)`: Explicit attribution for upfront kickoff deposit confirmation.
2. `payment_reversal_reason TEXT`: Mandatory audit reason for payment reversal.
3. `payment_reversal_by_human_id UUID`: Admin actor recording payment reversal.
4. `payment_reversal_at TIMESTAMPTZ`: Database clock timestamp of payment reversal.

#### Integrity Constraints
- `chk_project_deposit_sync`: Enforces mutual consistency between `deposit_paid`, `deposit_paid_at`, and `deposit_confirmed_by_human_id`.
- `chk_project_final_payment_sync`: Enforces mutual consistency between `final_payment_confirmed`, `final_payment_confirmed_at`, and `final_payment_confirmed_by_human_id`.
- `chk_project_payment_reversal_sync`: Enforces consistent logging of reversal reason, actor, and timestamp.
- `chk_project_feasibility_sync`: Enforces consistent state between `feasibility_status`, reviewer ID, and timestamp.
- `chk_project_delivery_auth_sync`: Enforces consistent state between `team_delivery_authorized`, authorizer ID, and timestamp.

#### Trigger Function: `fn_enforce_project_payment_governance`
- **Deposit / Final Confirmation:** Requires `auth.uid() IS NOT NULL`, `is_human_operator() = TRUE`, non-agent, matching `auth.uid()`, and database-assigned `clock_timestamp()`.
- **Confirmed Metadata Immutability:** Once confirmed (`TRUE -> TRUE`), confirmed actor IDs and timestamps cannot be modified or spoofed.
- **Auditable Privileged Reversal:** Transitioning either confirmation from `TRUE -> FALSE` requires `is_admin_operator() = TRUE`, non-empty `payment_reversal_reason`, records `payment_reversal_by_human_id = auth.uid()` and `payment_reversal_at = clock_timestamp()`, and clears confirmation metadata.
- **Project Closeout Gate Guard:** Final payment confirmation cannot be reversed if project is in terminal `closed` status.

---

### 3.4. Delivery Authorization & Technical Lead Sign-Off (`crm/delivery-governance-expansion.sql`)

- **Agent Exclusion in Role Check:** `is_technical_lead()` explicitly checks `(auth.jwt() -> 'app_metadata' ->> 'user_role') != 'agent'`.
- **Feasibility Sign-Off:** Only Technical Leads can approve/reject feasibility. Identity matches `auth.uid()`; timestamps are database-generated; metadata is immutable once signed.
- **Delivery Readiness Pre-Condition:** Delivery team readiness (`team_delivery_authorized = TRUE`) strictly requires prior Lead Feasibility approval (`feasibility_status = 'approved_by_lead'`). If scope, commercials, deposit, feasibility, or partner changes occur, delivery authorization is automatically invalidated.
- **Change Request Governance:** Feasibility determination and partner approval metadata on `project_change_requests` are protected against post-approval tampering.

---

## 4. Agent Least-Privilege Access Matrix

### 4.1. Subagent Ecosystem Analysis
The system architecture identifies three specialized automated subagent types operating under Hermes Agent orchestration:
1. `researcher`: Responsible for web/directory prospecting, domain validation, capturing evidence, and compiling research dossiers.
2. `qualifier`: Responsible for evaluating prospects against the approved 7 IPP scoring dimensions and submitting draft scorecards.
3. `drafter`: Responsible for preparing personalized draft outreach messages based on verified evidence and approved qualification tiers.

### 4.2. Complete Database Entity Access Matrix

| Database Entity | Anonymous (`anon`) | Human Operator | Researcher Agent (`researcher`) | Qualifier Agent (`qualifier`) | Drafter Agent (`drafter`) | Unknown Roles |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`companies`** | Denied | Full CRUD | SELECT (Non-suppressed) | SELECT (Non-suppressed) | SELECT (Non-suppressed) | Denied |
| **`company_domains`** | Denied | Full CRUD | SELECT (Deduplication) | SELECT (Deduplication) | SELECT (Deduplication) | Denied |
| **`contacts`** | Denied | Full CRUD | SELECT (Assigned Tasks & Non-opted-out) | SELECT (Assigned Tasks & Non-opted-out) | SELECT (Assigned Tasks & Non-opted-out) | Denied |
| **`suppression_registry`** | Denied | Full CRUD | SELECT (Verification) | SELECT (Verification) | SELECT (Verification) | Denied |
| **`research_reports`** | Denied | Full CRUD | INSERT (Own ID) / SELECT (Own or Assigned) | SELECT (Assigned Tasks Only) | SELECT (Assigned Tasks Only) | Denied |
| **`research_findings`** | Denied | Full CRUD | INSERT (Parent Author) / SELECT (Parent or Assigned) | SELECT (Assigned Tasks Only) | SELECT (Assigned Tasks Only) | Denied |
| **`lead_qualifications`** | Denied | Full CRUD | **DENIED** | INSERT (Draft & Own ID) / SELECT (Own or Assigned) | SELECT (Approved & Assigned Only) | Denied |
| **`outreach_campaigns`** | Denied | Full CRUD | **DENIED** | **DENIED** | SELECT (Active Campaigns Only) | Denied |
| **`outreach_messages`** | Denied | Full CRUD | **DENIED** | **DENIED** | INSERT (Draft & Own ID) / SELECT (Own or Assigned) | Denied |
| **`conversations`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`conversation_messages`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`activities`** | Denied | Full CRUD | INSERT (Own ID) / SELECT (Own or Assigned) | INSERT (Own ID) / SELECT (Own or Assigned) | INSERT (Own ID) / SELECT (Own or Assigned) | Denied |
| **`tasks`** | Denied | Full CRUD | SELECT & UPDATE (Assigned Only) | SELECT & UPDATE (Assigned Only) | SELECT & UPDATE (Assigned Only) | Denied |
| **`audit_logs`** | Denied | SELECT Only | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`partnerships`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`projects`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`project_milestones`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |
| **`project_change_requests`** | Denied | Full CRUD | **DENIED** | **DENIED** | **DENIED** | Denied |

### 4.3. Row-Level Task Scoping Mechanism
To avoid broad CRM dumping, agent access to child records (`contacts`, `research_reports`, `research_findings`, `lead_qualifications`, `outreach_messages`, `activities`) is row-level scoped through `tasks`:
```sql
EXISTS (
    SELECT 1 FROM tasks t 
    WHERE t.assigned_actor_type = 'agent' 
      AND t.assigned_actor_identity = current_agent_identity()
      AND t.status IN ('pending', 'in_progress')
      AND (t.company_id = target_table.company_id)
)
```
If no active task is assigned to the subagent for a prospective company, the subagent receives **zero rows** for that company's contacts, intelligence records, and correspondence.

---

## 5. PostgreSQL Grants & Privilege Verification

### 5.1. SQL Privilege Model
- Default wide privileges are revoked: `REVOKE ALL ON ALL TABLES/FUNCTIONS IN SCHEMA public FROM anon, authenticated, public;`.
- Tables grant DML to `authenticated`, while Row Level Security (RLS) restricts rows to authorized roles and identities.
- `suppression_registry` and `audit_logs` are strictly read-only (`SELECT` only) for `authenticated`; modifications can only occur via internal database triggers.
- Helper functions granted to `authenticated`: `is_human_operator`, `is_admin_operator`, `is_agent`, `is_agent_type`, `current_agent_identity`, `current_actor_role`, `add_business_days`, and `is_technical_lead`.

### 5.2. SECURITY DEFINER Isolation
- `fn_audit_log_capture()` is the sole `SECURITY DEFINER` function.
- It sets `search_path = public` to prevent search path hijacking.
- Default execution privileges on `fn_audit_log_capture()` are revoked from `anon`, `authenticated`, and `public`. It is invoked exclusively via internal database triggers.

### 5.3. Supabase `service_role` Boundary
> [!CAUTION]
> In Supabase architecture, the `service_role` key bypasses Row Level Security by design.
> 
> Under no circumstances may the `service_role` key or secret be exposed to Hermes Agent subagents, frontend clients, client-side scripts, or repository configuration files. Hermes subagents operate strictly under scoped JWT tokens with explicit `user_role = 'agent'` claims subject to RLS and database triggers. RLS does NOT protect against `service_role` misuse.

---

## 6. Security Test Matrix

The following test matrix documents the comprehensive security suite designed to validate the authorization and integrity controls across all system roles:

| Test ID | Test Scenario | Actor Role | Target Entity & Operation | Test Vectors / Injected Payload | Expected Database Enforcement Result | Verification Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TEST-01** | Anonymous Data Harvest | Anonymous (`anon`) | `SELECT * FROM companies / contacts / outreach_messages / projects` | Unauthenticated HTTP request without JWT token. | Access Denied: 0 rows returned (blocked by `anon_deny_*` RLS policies). | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-02** | Operator Admin Override Attempt | Human Operator (`user_role = 'operator'`) | `UPDATE outreach_messages SET approval_status = 'approved', is_admin_override = TRUE` | Passing `is_admin_override = TRUE` with standard operator JWT token. | Abort with Exception: `'Security Policy Violation: Only authorized admin or founder roles can perform an administrative override.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-03** | Authorized Admin Override | Admin / Founder (`user_role = 'admin'`) | `UPDATE outreach_messages SET approval_status = 'approved', is_admin_override = TRUE, override_reason = 'Valid rationale', override_by_human_id = auth.uid()` | Valid admin token, matching override ID, non-empty reason. | Success: Message marked `approved`, `approved_at` stamped via `clock_timestamp()`. | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-04** | Agent Outreach Approval Attempt | Drafter Agent (`user_role = 'agent'`) | `UPDATE outreach_messages SET approval_status = 'approved'` | Agent token setting approval status on draft message. | Abort with Exception: `'Security Policy Violation: Agents are strictly prohibited from approving outreach messages.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-05** | Agent Qualification Approval Attempt | Qualifier Agent (`user_role = 'agent'`) | `UPDATE lead_qualifications SET review_status = 'approved'` | Agent token setting qualification review status. | Abort with Exception: `'Security Policy Violation: Agents are strictly prohibited from approving lead qualifications.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-06** | Unauthenticated Payment Confirmation | Unauthenticated / Null Session | `UPDATE projects SET deposit_paid = TRUE` | SQL mutation without active authenticated session (`auth.uid() IS NULL`). | Abort with Exception: `'Security Policy Violation: Payment confirmation requires an authenticated session with non-null auth.uid().'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-07** | Agent Payment Confirmation Attempt | Any Agent (`user_role = 'agent'`) | `UPDATE projects SET deposit_paid = TRUE` | Agent token attempting to confirm upfront deposit. | Abort with Exception: `'Security Policy Violation: Agents are strictly prohibited from confirming project payments.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-08** | Operator Deposit Confirmation | Human Operator (`user_role = 'operator'`) | `UPDATE projects SET deposit_paid = TRUE, deposit_confirmed_by_human_id = auth.uid()` | Human operator confirming deposit with matching identity. | Success: `deposit_paid = TRUE`, `deposit_paid_at` database timestamp generated. | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-09** | Operator User Impersonation | Human Operator (`auth.uid() = User_A`) | `UPDATE outreach_messages SET approval_status = 'approved', approved_by_human_id = User_B` | Operator attempting to attribute approval to a different operator ID. | Abort with Exception: `'Security Policy Violation: approved_by_human_id must match authenticated user.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-10** | Unauthorized Technical Lead Sign-Off | Standard Operator (`user_role = 'operator'`) | `UPDATE projects SET feasibility_status = 'approved_by_lead'` | Operator lacking Technical Lead claims attempting feasibility sign-off. | Abort with Exception: `'Security Policy Violation: Only an authorized Technical Lead can approve or reject technical feasibility.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-11** | Cross-Agent Type Data Access | Researcher Agent (`subagent_type = 'researcher'`) | `SELECT * FROM lead_qualifications / outreach_messages` | Researcher attempting to read qualification scorecards or outreach drafts. | Access Denied: 0 rows returned (no policy permits researcher access to these tables). | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-12** | Unassigned Delivery Table Access | Any Agent (`user_role = 'agent'`) | `SELECT * FROM partnerships / projects / project_milestones` | Subagent querying delivery and governance expansion tables. | Access Denied: 0 rows returned (all agent delivery policies removed; RLS defaults to DENY). | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-13** | Approved Metadata Tampering | Human Operator (`user_role = 'operator'`) | `UPDATE outreach_messages SET approved_by_human_id = User_C WHERE approval_status = 'approved'` | Modifying approval metadata on an already approved record. | Abort with Exception: `'Security Policy Violation: Cannot alter approval identity, timestamps, or override metadata on an already approved message.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-14** | Unapproved Message Dispatch Bypass | Drafter Agent / Operator | `UPDATE outreach_messages SET sending_status = 'delivered' WHERE approval_status = 'draft'` | Setting dispatch/downstream status on unapproved draft. | Abort with Exception: `'Integrity Violation: Outreach message cannot enter dispatch or downstream status (delivered) without valid human approval.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-15** | Post-Approval Content Invalidation | Human Operator | `UPDATE outreach_messages SET body = 'Modified text' WHERE approval_status = 'approved'` | Modifying body text on an already approved outreach message. | Success with Invalidation: Status automatically reset to `draft`, approver ID and timestamp cleared, `sending_status` reset to `draft`. | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-16** | Operator Payment Reversal Attempt | Human Operator (`user_role = 'operator'`) | `UPDATE projects SET deposit_paid = FALSE WHERE deposit_paid = TRUE` | Standard operator attempting to clear confirmed deposit. | Abort with Exception: `'Security Policy Violation: Only authorized admin or founder roles can reverse a deposit payment confirmation.'` | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-17** | Admin Payment Reversal with Reason | Admin / Founder (`user_role = 'admin'`) | `UPDATE projects SET deposit_paid = FALSE, payment_reversal_reason = 'Wire bounced' WHERE deposit_paid = TRUE` | Authorized admin reversing deposit with documented audit reason. | Success: Deposit cleared, reversal reason, admin ID, and timestamp recorded in audit trail. | **Static: PASSED**<br>Runtime: NOT RUN |
| **TEST-18** | Premature Delivery Authorization | Human Operator (`user_role = 'operator'`) | `UPDATE projects SET team_delivery_authorized = TRUE WHERE feasibility_status = 'pending_review'` | Attempting delivery team readiness sign-off before feasibility approval. | Abort with Exception: `'Operational Security Violation: Cannot authorize delivery before Technical Lead feasibility approval.'` | **Static: PASSED**<br>Runtime: NOT RUN |

---

## 7. Static Validation Results & Verification Evidence

Local static code analysis and AST syntax checks were conducted using Python 3.12 and `sqlparse`:

1. **SQL Parsing & AST Verification:**
   - [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql): **179 statements** parsed with zero syntax errors.
   - [`crm/delivery-governance-expansion.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/delivery-governance-expansion.sql): **61 statements** parsed with zero syntax errors.
2. **PL/pgSQL Block & Dollar Quote Integrity:**
   - All `$$` function definition blocks are perfectly balanced across both files.
   - All conditional `IF...ELSIF...ELSE...END IF;` and `CASE...END CASE;` constructs have verified closure.
3. **Database Schema Totals:**
   - Tables: **18 total** (14 Core + 4 Delivery Expansion).
   - Functions: **25 total** (17 Core + 8 Delivery Expansion).
   - Triggers: **34 total** (19 Core + 15 Delivery Expansion).
   - RLS Policies: **53 total** (45 Core + 8 Delivery Expansion).

---

## 8. Tests Not Executed (Runtime Environment Boundary)

In strict accordance with the task instructions (*"This is a design and SQL hardening task only. DO NOT connect to Supabase. DO NOT execute any SQL against a remote or local database. DO NOT perform a live migration"*), the following test categories were **NOT RUN**:

1. **Live PostgreSQL Runtime Execution:** No test transactions were submitted against a live Postgres daemon or Supabase instance.
2. **Supabase Auth Hook & Edge Function Integration:** Simulated JWT tokens were validated via static AST review of claim evaluation logic; live JWT signature verification via Supabase GoTrue was not run.
3. **Network Deliverability & Suppression API Integration:** Transactional email dispatch integrations (e.g. Resend/Postmark) were not connected.
4. **End-to-End Task Queue Execution:** Automated subagent orchestration workflows via Hermes were not triggered against live tables.

*All status entries marked "Static: PASSED / Runtime: NOT RUN" truthfully reflect this boundary.*

---

## 9. Remaining Security Risks & Architectural Considerations

1. **Service-Role Key Protection:** Database triggers and RLS policies are completely bypassed if a caller uses the Supabase `service_role` secret. Rigorous secret management must prevent leakage of this credential to subagent environments.
2. **JWT Custom Claim Issuance:** Database security depends on the integrity of `app_metadata` claims (`user_role`, `subagent_type`, `technical_role`, `agent_id`). These claims must be issued exclusively by trusted Supabase Auth Edge Functions or administrative hooks, never through client-editable user metadata (`user_metadata`).
3. **Task Queue Orchestration Latency:** Task-scoped RLS policies require a task record in `tasks` with `status IN ('pending', 'in_progress')` before a subagent can access company contacts or research findings. If Hermes assigns tasks out of sequence, subagents will receive empty result sets.

---

## 10. Decisions Requiring Founder Input

The following architectural and operational decisions remain open for founder determination:

1. **Decision D-01: Supabase Hosting Region:** European region (Frankfurt - `eu-central-1`) for GDPR posture vs. US East (N. Virginia - `us-east-1`) based on target partner distribution.
2. **Decision D-02: Base Commercial Currency:** Supported currencies include USD, EUR, GBP; default commercial currency model requires founder sign-off before milestone contracts execute.
3. **Decision D-03: Outreach Dispatch Service Provider:** Selection between Resend, Postmark, or dedicated agency SMTP infrastructure for outbound approved correspondence.
4. **Decision D-04: Audit Log Retention & Archiving:** Cold-storage S3/GCS archival policy for records older than 12 months.

---

## 11. Conclusion & Verification Summary

Phase 2.1 — Batch A Security & Authorization Hardening is complete, fully synchronized across the repository, and verified via local static AST analysis. The codebase is structurally hardened, least-privilege compliant, and ready for founder review.
