# Phase 2.1 — Final Security, Data Integrity, and Architecture Review Report

**Document Version:** 1.0.0  
**Phase:** 2.1 Final Security & Data Integrity Hardening  
**Workspace:** `development-partnership/`  
**System Role:** Principal PostgreSQL Architect, Supabase Security Engineer, & Database Integrity Auditor  
**Target Platform:** PostgreSQL 15+ (Supabase Native)  
**Status:** Final Hardening Complete — Awaiting Founder Review  

---

## 1. Executive Summary

This report documents the final security audit, data integrity verification, and architectural hardening conducted on the operational database for the **Managed Development Partner** business.

All structural vulnerabilities, referential integrity gaps, permission leaks, and validation edge cases identified across Phase 2.1 iterations have been resolved at the database schema level.

### Operational Context
- **Stage:** Pre-Operational Foundation.
- **Technical Team Status:** The planned technical team (one experienced lead contributor and three junior developers utilizing AI-assisted workflows) is **not yet onboarded and not currently available for client delivery**.
- **Execution Boundary:** Design-only. Zero external database connections, no Supabase project creation, no API keys generated, and no migrations executed against a live database.

---

## 2. Comprehensive Inventory of Identified Issues & Exact Corrections

The table below enumerates every issue discovered during the security and architectural audit, along with the exact correction implemented in the schema:

| Issue # | Domain | Identified Vulnerability / Gap | Exact Correction Implemented | File Reference |
| :---: | :--- | :--- | :--- | :--- |
| **SEC-01** | **Authentication & RLS** | Negative-matching pattern `NOT is_human_operator()` granted agent access to unknown, client, or unverified authenticated roles. | Implemented explicit `is_agent()` checking `app_metadata.user_role = 'agent'`. Unknown roles receive zero operational access. | [`crm/schema.sql#L65-L75`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L65-L75) |
| **SEC-02** | **Privilege Separation** | Blanket agent policies allowed any subagent to write to draft scorecards or draft messages indiscriminately. | Implemented granular `is_agent_type()` checks: Researchers write reports; Qualifiers write draft scorecards; Drafters write draft outreach. | [`crm/schema.sql#L77-L88`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L77-L88) |
| **SEC-03** | **Task Queue Security** | Any agent could view and mutate any operational task regardless of assignment. | RLS policies restrict agent task access strictly to rows where `assigned_actor_identity = current_agent_identity()`. | [`crm/schema.sql#L836-L856`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L836-L856) |
| **SEC-04** | **Human Approval Gate** | Triggers accepted caller-supplied approval timestamps, and allowed silent administrative identity mismatches. | Approvals require non-null `auth.uid()`, strictly match `approved_by_human_id = auth.uid()`, and overwrite timestamps via `clock_timestamp()`. | [`crm/schema.sql#L560-L620`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L560-L620) |
| **SEC-05** | **Approval Invalidation** | Approved messages or qualification scorecards could have their content modified after approval without resetting review status. | Database triggers detect material content or score modifications and automatically reset status to `draft` and clear approver IDs. | [`crm/schema.sql#L570-L585`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L570-L585) |
| **SEC-06** | **Administrative Override** | No explicit audit trail for authorized administrative actions on behalf of other operators. | Modeled explicit override columns (`is_admin_override`, `override_reason`, `override_by_human_id`) with mandatory justification constraints. | [`crm/schema.sql#L368-L373`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L368-L373) |
| **INT-01** | **Referential Integrity** | Child records could reference a contact or conversation from a different company than the record's own `company_id`. | Implemented Composite Foreign Keys on `outreach_messages`, `activities`, `research_findings`, and `projects`. | [`crm/schema.sql#L380-L385`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L380-L385) |
| **INT-02** | **Suppression Sync** | Changing an already opted-out contact's email or primary domain failed to record the new identifier in `suppression_registry`. | Re-engineered sync triggers to monitor channel modifications on opted-out entities across email, domain, and LinkedIn. | [`crm/schema.sql#L510-L555`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L510-L555) |
| **INT-03** | **Pre-Dispatch Suppression** | Outbound messages risked dispatch if suppression was added after draft creation. | Trigger `fn_enforce_outreach_human_approval` verifies recipient email, domain, and LinkedIn handle immediately before dispatch. | [`crm/schema.sql#L625-L645`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L625-L645) |
| **INT-04** | **Suppression Immutability** | Suppression records could be deleted by standard database operations. | Implemented trigger `fn_enforce_suppression_immutability` blocking any `DELETE` on `suppression_registry`. | [`crm/schema.sql#L480-L495`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L480-L495) |
| **EV-01** | **Lead Scoring Integrity** | Positive dimension scores could be inserted without supporting evidence, and tier assignments were unconstrained. | Database trigger verifies structured evidence in `dimension_evidence JSONB` for all scores > 0 (including `customer_flow_score`, `development_demand_score`, etc.) and enforces exact tier thresholds. | [`crm/schema.sql#L650-L720`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L650-L720) |
| **AUD-01** | **Audit Log Tampering** | Audit logs were claimed to be immutable but lacked database-level write blocks. | Trigger blocks manual `UPDATE` and `DELETE`; trusted `SECURITY DEFINER` function captures state changes automatically. | [`crm/schema.sql#L465-L478`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L465-L478) |
| **CAL-01** | **Timezone Volatility** | `add_business_days` was incorrectly declared `IMMUTABLE` and did not evaluate timezone-specific day boundaries. | Re-declared function as `STABLE` and parameterized with target timezone defaulting to partner operating timezone. | [`crm/schema.sql#L33-L58`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql#L33-L58) |
| **MOD-01** | **Migration Scope** | All 18 tables were bundled into a single file despite delivery tables being unneeded in the early CRM MVP. | Separated into `crm/schema.sql` (14 MVP Core tables) and `crm/delivery-governance-expansion.sql` (4 Delivery tables with `is_technical_lead()` and `final_payment_confirmed`). | [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql) & [`delivery-governance-expansion.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/delivery-governance-expansion.sql) |

---

## 3. Deliberately Deferred Issues

To adhere strictly to our **MVP scope** and avoid premature infrastructure engineering, the following capabilities are deliberately deferred:

1. **Statutory Public Holiday Calendar Engine:**
   - *Rationale:* The database function `add_business_days()` calculates true Monday–Friday working days, accurately skipping weekends. Encoding complex statutory holiday calendars across multiple international jurisdictions (US federal, UK bank holidays, German state holidays) is premature before initial partner agreements define governing jurisdictions.
   - *Deferred To:* Phase 3 (Delivery & Operations).
2. **Complete Invoicing and Financial Accounting Ledger:**
   - *Rationale:* The CRM MVP tracks contracted SOW commercial values (`commercial_value`) and upfront kickoff deposits (`deposit_required`, `deposit_paid`). Full accounts receivable ledgers, invoice PDF generation, tax calculations, and stripe payment reconciliation are deferred to avoid building an unnecessary accounting system.
   - *Deferred To:* Phase 3 (Finance & Invoicing Platform).
3. **Monthly Reserved Capacity Burn & Time Tracking Engine:**
   - *Rationale:* Monthly Reserved Capacity is a strategic commercial model for post-trust partners. The technical team is not currently onboarded, and billable capacity allocations remain marked `TBD`. Hardcoding time-tracking engines or developer sprint allocation tables in the database is premature.
   - *Deferred To:* Post-Onboarding Delivery Toolchain.

---

## 4. Security Model & Agent Permission Matrix

### 4.1. Identity Verification Strategy
- **Human Operators:** Authenticated via Supabase Auth with verified claim: `auth.jwt() -> 'app_metadata' ->> 'user_role' IN ('operator', 'admin', 'founder')`.
- **Hermes Subagents:** Authenticated via scoped JWT tokens with verified claims: `user_role = 'agent'`, `subagent_type IN ('researcher', 'qualifier', 'drafter')`, and `agent_id = '<uuid>'`.
- **Unknown Roles:** If an authenticated token lacks either claim, both `is_human_operator()` and `is_agent()` return `FALSE`. **Zero operational access is granted.**

### 4.2. Complete Permission Matrix

| Database Entity | Anonymous (`anon`) | Human Operator | Researcher Agent | Qualifier Agent | Drafter Agent | Unknown Roles |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`companies`** | Denied | Full CRUD | SELECT | SELECT | SELECT | Denied |
| **`company_domains`** | Denied | Full CRUD | SELECT | SELECT | SELECT | Denied |
| **`contacts`** | Denied | Full CRUD | SELECT (Active) | SELECT (Active) | SELECT (Active) | Denied |
| **`suppression_registry`** | Denied | Full CRUD | SELECT (Verify) | SELECT (Verify) | SELECT (Verify) | Denied |
| **`research_reports`** | Denied | Full CRUD | INSERT / SELECT | SELECT | SELECT | Denied |
| **`research_findings`** | Denied | Full CRUD | INSERT / SELECT | SELECT | SELECT | Denied |
| **`lead_qualifications`** | Denied | Full CRUD | SELECT | INSERT (Draft) / SELECT | SELECT | Denied |
| **`outreach_campaigns`** | Denied | Full CRUD | SELECT | SELECT | SELECT | Denied |
| **`outreach_messages`** | Denied | Full CRUD | SELECT | SELECT | INSERT (Draft) / SELECT | Denied |
| **`conversations`** | Denied | Full CRUD | Denied | Denied | Denied | Denied |
| **`conversation_messages`**| Denied | Full CRUD | Denied | Denied | Denied | Denied |
| **`activities`** | Denied | Full CRUD | INSERT / SELECT | INSERT / SELECT | INSERT / SELECT | Denied |
| **`tasks`** | Denied | Full CRUD | Assigned Only | Assigned Only | Assigned Only | Denied |
| **`audit_logs`** | Denied | SELECT Only | Denied | Denied | Denied | Denied |

### 4.3. Trusted Backend & Service-Role Isolation
> [!IMPORTANT]
> **Defensive Architecture:** In Supabase, the `service_role` key bypasses Row Level Security by design.
> 
> To guarantee that the database policies and triggers cannot be circumvented, the `service_role` key must **never** be exposed to browser clients, client-side scripts, or Hermes subagents. Hermes subagents operate as authenticated JWT callers subject to RLS and database triggers.

---

## 5. Human Approval and Administrative Override Model

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           Hermes Drafting Agent                           │
│  - Inserts message with approval_status = 'draft', sending_status = 'draft'│
│  - proposed_commercial_model checked: strategic_revenue_sharing rejected  │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                      Mandatory Human Approval Action                      │
│  - Human operator reviews personalized draft against research evidence    │
│  - Standard Approval:                                                     │
│      • approval_status = 'approved'                                       │
│      • approved_by_human_id MUST equal auth.uid()                         │
│      • approved_at overwritten with clock_timestamp()                     │
│  - Administrative Override (e.g. Managing Partner on behalf of Lead):     │
│      • is_admin_override = TRUE                                           │
│      • override_reason MUST be populated (Audit requirement)              │
│      • override_by_human_id MUST equal auth.uid()                         │
│      • Triggers explicit 'OVERRIDE_EXECUTED' audit log event              │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┴─────────────────────────────┐
        │                                                           │
        ▼ (If Content Modified Post-Approval)                       ▼ (If Ready for Transmission)
┌──────────────────────────────────────────────┐  ┌──────────────────────────────────────────┐
│        Approval Invalidation Trigger         │  │       Pre-Dispatch Suppression Gate      │
│  - Detects change in body, subject, channel, │  │  - Trigger checks recipient email,       │
│    contact_id, or commercial model           │  │    domain, and LinkedIn handle against   │
│  - Resets approval_status = 'draft'          │  │    suppression_registry                  │
│  - Clears approved_by_human_id & timestamp   │  │  - Any match aborts dispatch immediately │
│  - Resets sending_status = 'draft'           │  │  - Passes clean ➔ queued / sending       │
└──────────────────────────────────────────────┘  └──────────────────────────────────────────┘
```

---

## 6. Multi-Channel Suppression Architecture

The suppression model guarantees that opted-out contacts and blacklisted organizations can never be re-contacted:

1. **Independent Ledger (`suppression_registry`):** Decoupled from core entities without foreign key constraints. Deleting a contact or company record does not delete their suppression entries.
2. **Multi-Channel Coverage:**
   - **Email:** Normalized lowercased email addresses (`target_type = 'email'`).
   - **Domain:** Cleaned domain names (`target_type = 'domain'`).
   - **LinkedIn:** Profile URLs or handles (`target_type = 'linkedin'`).
   - **Phone:** Normalized phone numbers (`target_type = 'phone'`).
3. **Automatic Synchronization Triggers:**
   - Marking `contacts.is_opted_out = TRUE` automatically syncs email and LinkedIn handle.
   - Updating the email or LinkedIn handle of an already opted-out contact automatically inserts the new identifier into the registry.
   - Marking `companies.is_suppressed = TRUE` automatically syncs the primary domain.
4. **Pre-Dispatch Verification:** Trigger `fn_enforce_outreach_human_approval` queries `suppression_registry` for all associated contact and company identifiers before allowing messages to enter `queued`, `sending`, or `sent` states.
5. **Deletion Immutability:** Trigger `fn_enforce_suppression_immutability` intercepts `DELETE` commands on `suppression_registry` and throws a compliance exception.

---

## 7. Referential Integrity & Composite Foreign Key Model

To prevent cross-company data corruption, the schema replaces single-column foreign keys with composite foreign keys across all multi-entity relationships:

```
[companies]
    ▲
    │ (id, company_id)
[contacts] ◄─────────────────────────┐
    ▲                                │ (contact_id, company_id)
    │ (contact_id, company_id)       │
[outreach_messages]            [activities] ────────► [conversations]
                                     ▲                     ▲
                                     │                     │ (id, company_id)
                                     └─────────────────────┘
[research_reports]
    ▲
    │ (report_id, company_id)
[research_findings]

[partnerships]
    ▲
    │ (partnership_id, company_id)
[projects]
```

### Integrity Guarantees
- An outreach message cannot be sent to Contact X while referencing Company Y.
- An activity cannot link a conversation from Company A to a contact from Company B.
- A research finding cannot be attached to a report belonging to a different company.
- A project cannot reference a partnership belonging to a different organization.

---

## 8. Migration Boundaries (MVP Core vs. Delivery Expansion)

| Parameter | File 1: MVP Core ([`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql)) | File 2: Delivery Expansion ([`crm/delivery-governance-expansion.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/delivery-governance-expansion.sql)) |
| :--- | :--- | :--- |
| **Operational Phase** | Phase 2.1 — Business Development & Outreach | Phase 2.2 / Phase 3 — Delivery & Operations |
| **Table Count** | **14 Tables** | **4 Tables** |
| **Key Entities** | `suppression_registry`, `companies`, `company_domains`, `contacts`, `research_reports`, `research_findings`, `lead_qualifications`, `outreach_campaigns`, `outreach_messages`, `conversations`, `conversation_messages`, `activities`, `tasks`, `audit_logs` | `partnerships`, `projects`, `project_milestones`, `project_change_requests` |
| **Business Objective** | Discover, research, score, qualify, draft outreach, log activities, and track human approvals. | Formalize master wholesale agreements, enforce lead feasibility sign-offs, manage milestone inspections, and govern change orders. |
| **Delivery Team Dependency** | **None.** Operates fully in pre-operational foundation stage. | **Guarded.** Execution blocked until human operator authorizes delivery readiness. |

---

## 9. Remaining Open Decisions (Founder Approval Required)

The following architectural and commercial decisions remain uncommitted in the schema and require formal founder determination:

1. **Supabase Region Selection (D-01):** Unresolved. Must be selected based on primary target partner geography and GDPR compliance posture (e.g. Frankfurt vs. N. Virginia).
2. **Currency Model (D-02):** Unresolved. Stored as flexible `VARCHAR(3)` without hardcoding USD as default.
3. **Hermes Authentication Architecture (D-03):** Direct scoped JWT custom claims vs. trusted Edge Function gateway.
4. **Outreach Dispatch Provider (D-04):** Transactional email provider selection (e.g., Resend vs. Postmark).
5. **Audit Log Retention Period (D-05):** Cold storage archiving vs. rolling 12-month partition.

---

## 10. Known Limitations

1. **Static Validation Posture:** Static validation was conducted using Python AST and `sqlparse` tooling. No runtime database migrations were executed in accordance with design-only rules.
2. **Statutory Public Holiday Handling:** The `add_business_days` function skips Saturdays and Sundays in the partner's operating timezone, but does not hardcode jurisdiction-specific statutory public holidays. Custom holiday calendars can be added in Phase 3.
3. **Pre-Operational Delivery Freeze:** Projects cannot enter active implementation until the founder authorizes delivery readiness and the technical team is onboarded.

---

*Report completed and synchronized with [`crm/schema.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/schema.sql) and [`crm/delivery-governance-expansion.sql`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/crm/delivery-governance-expansion.sql).*
