# Phase 3.0 — First Research Agent Vertical Slice Report

**Document Version:** 1.0.0  
**Phase:** 3.0 — First Research Agent Vertical Slice  
**Target Environment:** Supabase DEVELOPMENT (`vyfqmrdznmocnmzyjjcj`)  
**Execution Runtime:** Interactive Supervised Antigravity Runtime & Python PostgREST Client  
**Date:** 2026-11-07  
**Status:** Architecture Implemented & Auth/RLS Verified (Live Run Gate: Awaiting Operator Company Selection)  

---

## 1. Executive Summary

This report documents the architectural implementation, identity provisioning, and runtime security verification of the **First Research Agent Vertical Slice** for the Managed Development Partner project.

The objective of Phase 3.0 is not to build the full autonomous platform, but to prove that an authenticated **Research Agent (`hermes-researcher-v1`)** can authenticate through Supabase Auth, receive a genuine scoped JWT, execute under the existing Row Level Security (RLS) matrix on the remote Development database (`vyfqmrdznmocnmzyjjcj`), and complete its assigned research workflow without privileged credentials.

### Key Operational Milestones:
1. **Administrative Identity Provisioning:** Built [`agents/researcher/provision.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/provision.py) and successfully provisioned the real Researcher identity (`sub = 23f94f9d-1906-498e-acf0-017555b97c87`) with immutable `app_metadata` in Supabase Auth.
2. **Authenticated Operator Identity:** Provisioned the Human Operator Auth identity (`sub = e1cd1ff2-080d-47c4-9589-477ce66258f3`) with `user_role = 'operator'` to govern company ingestion and task creation.
3. **Genuine JWT Verification:** Successfully acquired a real JWT through the public GoTrue token endpoint (`grant_type=password`) with database role `authenticated` and validated claims:
   - `app_metadata.user_role = 'agent'`
   - `app_metadata.subagent_type = 'researcher'`
   - `app_metadata.agent_id = 'hermes-researcher-v1'`
4. **PostgREST RLS Enforcement Verified:** Executed automated integration tests against live PostgREST endpoints confirming that the Researcher Agent is strictly prevented from inserting into `companies`, `contacts`, or `tasks` (all returning HTTP 403 `42501: new row violates row-level security policy`).
5. **Phase 2.3 Documentation Correction:** Corrected trigger count from 21 to 22 in [`reports/phase-2.3-supabase-deployment-report.md`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/reports/phase-2.3-supabase-deployment-report.md) by including `trg_outreach_campaigns_updated_at`.
6. **Production Isolation:** **Production (`vqnamszywsuqjxuqseas`) remained 100% untouched.**

---

## 2. Authentication & Identity Architecture

### 2.1. Provisioned Identities (Supabase Auth)

| Actor | Supabase Auth User UUID (`sub`) | Email | Verified `app_metadata` Claims | Database Role |
| :--- | :--- | :--- | :--- | :--- |
| **Research Agent** | `23f94f9d-1906-498e-acf0-017555b97c87` | `researcher.v1@agents.internal` | `user_role = 'agent'`<br>`subagent_type = 'researcher'`<br>`agent_id = 'hermes-researcher-v1'` | `authenticated` |
| **Human Operator** | `e1cd1ff2-080d-47c4-9589-477ce66258f3` | `operator@partnership.internal` | `user_role = 'operator'`<br>`subagent_type = null`<br>`agent_id = null` | `authenticated` |

### 2.2. Authentication Flow & Security Boundaries
- **No `service_role` in Execution Context:** Neither the Researcher nor the Human Operator uses `service_role` credentials for CRM operations.
- **Client Inability to Forge Claims:** Supabase Auth (GoTrue) prevents clients from updating `app_metadata` directly via user endpoints; claims were set via administrative provisioning ([`agents/researcher/provision.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/provision.py)).
- **Token Acquisition:** Tokens are issued via `POST /auth/v1/token?grant_type=password` using the public `anon` API key and user passwords.
- **SQL Helper Function Consumption:** The decoded JWT claims match the exact expectations of canonical SQL functions:
  - `is_agent()` $\rightarrow$ `TRUE`
  - `is_agent_type('researcher')` $\rightarrow$ `TRUE`
  - `current_agent_identity()` $\rightarrow$ `'hermes-researcher-v1'`
  - `is_human_operator()` $\rightarrow$ `TRUE` (for Operator session)

---

## 3. Implemented Software Modules

All code for the vertical slice has been implemented under [`agents/researcher/`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher):

1. [`agents/researcher/config.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/config.py):
   Configuration loader reading target Supabase project URL, keys, and credentials from environment or `.env`.
2. [`agents/researcher/auth.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/auth.py):
   GoTrue authentication client with automated HTTP retries, payload decoding, and Section 3 claim validation.
3. [`agents/researcher/client.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/client.py):
   PostgREST HTTP client enforcing RLS headers (`apikey`, `Authorization: Bearer <jwt>`, `Prefer: return=representation`) and structured error parsing.
4. [`agents/researcher/agent.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/agent.py):
   Core `ResearcherAgent` domain class implementing Contract 3 and Workflow 2:
   - Task acquisition (`get_assigned_active_task`)
   - Task state transition (`pending` $\rightarrow$ `in_progress`)
   - Company and domain discovery (`read_assigned_company`, `read_company_domains`)
   - Report persistence (`persist_research_report`)
   - Atomic findings persistence with enum & evidence checks (`persist_research_findings`)
   - Engagement logging (`record_activity`)
   - Task completion (`complete_task`)
5. [`agents/researcher/provision.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/provision.py):
   Administrative CLI for provisioning agent and operator accounts via the GoTrue Admin API.
6. [`agents/researcher/operator_cli.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/operator_cli.py):
   Human Operator CLI enabling authenticated company registration, task creation, and deliverable inspection.
7. [`agents/researcher/runner.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/runner.py):
   End-to-end orchestration runner executing the vertical slice workflow.
8. [`agents/researcher/test_researcher_slice.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/test_researcher_slice.py):
   Automated integration test suite verifying authentication and RLS boundaries.

---

## 4. Runtime Verification Results

The automated integration test suite ([`agents/researcher/test_researcher_slice.py`](file:///c:/Users/puria/Desktop/code-workshop/projects/development-partnership/agents/researcher/test_researcher_slice.py)) was executed against the live Supabase Development PostgREST endpoint:

```
Ran 5 tests in 19.031s

OK
```

### Detailed Verification Breakdown:

| Test Case | Description | Expected Result | Actual Live Result | Status |
| :--- | :--- | :--- | :--- | :---: |
| `test_01_researcher_jwt_claims` | Validates JWT claims returned by GoTrue | `role = 'authenticated'`, `user_role = 'agent'`, `subagent_type = 'researcher'`, `agent_id = 'hermes-researcher-v1'` | Claims strictly verified | **PASS** |
| `test_02_researcher_cannot_insert_companies` | Proves Researcher cannot create companies | HTTP 403 Forbidden (`42501: new row violates row-level security policy`) | HTTP 403 / 42501 | **PASS** |
| `test_03_researcher_cannot_insert_contacts` | Proves Researcher cannot insert contacts | HTTP 403 Forbidden (`42501: new row violates row-level security policy`) | HTTP 403 / 42501 | **PASS** |
| `test_04_researcher_cannot_insert_tasks` | Proves Researcher cannot create tasks | HTTP 403 Forbidden (`42501: new row violates row-level security policy`) | HTTP 403 / 42501 | **PASS** |
| `test_05_anonymous_access_denied` | Proves unauthenticated/anon requests are blocked | HTTP 401 / 403 Forbidden across CRM tables | HTTP 401 / 403 | **PASS** |

---

## 5. Status Breakdown: Implemented vs. Executed vs. Verified

| Component / Requirement | Status | Evidence / Notes |
| :--- | :---: | :--- |
| **Researcher Identity Provisioning** | **Executed & Verified** | Supabase Auth user `23f94f9d-1906-498e-acf0-017555b97c87` created with required `app_metadata`. |
| **Human Operator Identity Provisioning**| **Executed & Verified** | Supabase Auth user `e1cd1ff2-080d-47c4-9589-477ce66258f3` created with `user_role = 'operator'`. |
| **Researcher GoTrue Authentication** | **Executed & Verified** | Real JWT issued with role `authenticated` and expected claims. |
| **RLS Write Denial on Prohibited Tables** | **Executed & Verified** | 403 / 42501 errors captured when Researcher attempts INSERT on `companies`, `contacts`, or `tasks`. |
| **Researcher Agent Domain Logic** | **Implemented** | Full implementation in `agents/researcher/agent.py` adhering to Contract 3. |
| **Operator CRM CLI Tooling** | **Implemented** | Full implementation in `agents/researcher/operator_cli.py`. |
| **End-to-End Execution Runner** | **Implemented** | Full implementation in `agents/researcher/runner.py`. |
| **Company Selection & Registration** | **Awaiting Operator Input** | Development database currently has 0 companies. Awaiting Human Operator company selection. |
| **Task Creation & Assignment** | **Awaiting Operator Input** | Development database currently has 0 tasks. Awaiting Human Operator task creation. |
| **Live Company Research Run** | **Awaiting Operator Input** | Pre-conditioned on operator company selection. |
| **Production Isolation** | **Verified** | Production project (`vqnamszywsuqjxuqseas`) was never accessed or targeted. |

---

## 6. Next Step to Live Run Completion

In accordance with **Section 4 (Operator Preparation)**:
> *"The Human Operator must create or select one real company in the Development CRM. The company must be registered through an authenticated Human Operator session... If the operator has not selected a company, request one before proceeding with the live run."*

As confirmed by database inspection (`SELECT count(*) FROM public.companies;` $\rightarrow 0$), no company has yet been registered in the Development CRM.

The operator session (`operator@partnership.internal`) is provisioned and ready. Once the operator provides the name and domain of the target company (or selects from recommended target market agency profiles), the company registration, task creation, and live research execution will proceed immediately.
