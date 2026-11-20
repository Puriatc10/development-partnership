# Security Findings — CRM Schema (Development)

**Date:** 2026-11-13
**Method:** static read of `crm/schema.sql` (2,090 lines, identical body to the deployed migration) plus read-only live probes with the operator and researcher identities.
**Not applied:** no SQL was executed against any database — DDL credentials are unavailable (BLK-01).
**Scope:** findings I re-verified myself. Claims from the parallel repository audit that I could not confirm are listed separately in §4 and are **not** treated as fact.

---

## 1. Findings

| ID | Severity | Finding | Evidence | Fix |
|---|---|---|---|---|
| H-01 | **High** | The opt-out / suppression write path cannot work for the only role allowed to trigger it. | `fn_sync_contact_suppression` (L861-887) and `fn_sync_company_suppression` (L894-914) are SECURITY **INVOKER** and INSERT into `suppression_registry`; `authenticated` holds `SELECT` only (L2086, after `REVOKE ALL` L2070). An operator setting `contacts.is_opted_out = TRUE` raises `42501 permission denied`. | Authored: triggers become `SECURITY DEFINER SET search_path = public, pg_temp` |
| H-02 | **High** | Agents can rewind a task and cancel it, contradicting three documents. | `fn_enforce_task_governance` (L1494-1566) blocks `pending→completed` and edits to completed/cancelled rows, but never restricts `in_progress→pending` or `→cancelled`. `task-execution-protocol.md` L53/96/97 claims both are blocked. | Authored: `fn_enforce_agent_task_transitions` whitelists agent transitions |
| H-03 | **High** | Outreach drafting has no qualification precondition: the human gate is decorative for unqualified companies. | `agent_insert_outreach_messages` (L1977-1999) requires an active task, draft status and no revenue-share model — but never an approved `lead_qualifications` row or a `qualified` lifecycle stage. `agent-contracts.md` L457 states the precondition. | Authored: policy adds approved, non-disqualified scorecard + lifecycle precondition |
| H-04 | Medium | Two approved scorecards can coexist for one company; the effective tier depends on insertion order. | No unique index on `lead_qualifications(company_id)`; only `idx_lead_qualifications_company` (L1695). `fn_sync_company_qualification` (L1407+) applies "the last approved row". | Authored: partial unique index `WHERE review_status='approved'` |
| H-05 | Medium | Primary-domain sync re-parents another company's domain row. | `fn_sync_company_primary_domain` (L844-848): `ON CONFLICT (domain) DO UPDATE SET company_id = EXCLUDED.company_id`. | Authored: conflict now raises "duplicate domain" instead of moving the row |
| H-06 | Medium | Deleting a contact that appears in an activity fails with an unrelated NOT NULL error. | `activities.company_id` is `NOT NULL` (L710); the composite FKs (L725/729) are `ON DELETE SET NULL` without a column list, so Postgres tries to null `company_id` too. | Open — needs `ON DELETE SET NULL (contact_id)` (Postgres 15+ column list). Authored separately once H-01..H-05 are applied and tested. |
| H-07 | Medium | The two delivery expansion files disagree and neither can be applied as-is. | `diff crm/delivery-governance-expansion.sql crm/phase2_delivery_expansion.sql` = **650 diff lines**, both labelled canonical. `delivery-governance-expansion.sql` L1300 revokes `fn_enforce_team_delivery_authorization`, a function that does not exist → the migration aborts. | Open — needs a decision: which file is canonical, then a single reviewed migration |
| H-08 | Low | `supabase db reset` cannot work locally. | `config.toml` L67-72: `[db.seed] enabled = true`, `sql_paths = ["./seed.sql"]`; no `supabase/seed.sql` exists. | Open — add a seed file or disable seeding |
| H-09 | Low | Local config targets PostgreSQL 17 while every document says 15+. | `config.toml` L41 `major_version = 17`; `schema.sql` header "PostgreSQL 15+". | Open — document the real version |

---

## 2. What is *not* broken

Worth stating, because these were claimed and are true:

- Anonymous access is denied at the grant layer (`42501 permission denied for table companies`) and `/rest/v1/` rejects the anon key.
- Agent writes are task-scoped (the STAC pattern: `assigned_actor_identity = current_agent_identity()` + active task) across research, qualification and outreach policies.
- `verified_fact` requires an evidence URL and high/medium confidence (`chk_verified_fact_requires_evidence`, L495).
- Agent identities, claim verification and the fail-closed Development guard work (Phase 0 probe; 141 tests).
- `audit_logs`, `suppression_registry` and outreach approvals are trigger-protected against update.

---

## 3. Verification plan for the authored batch

`supabase/migrations/pending/20261113_hardening_batch1.sql` is authored but **unapplied**. After applying (Development only):

1. **H-01:** as operator, upsert a contact with `is_opted_out=true` → expect one `suppression_registry` row of type `email`, no `42501`.
2. **H-02:** as researcher on an `in_progress` task, PATCH status to `pending` → expect `Lifecycle Violation`; PATCH to `completed` → succeeds.
3. **H-03:** as drafter with an active task for a company with no approved qualification, insert a draft message → expect policy rejection; after an operator approves the scorecard, the same insert succeeds.
4. **H-04:** approve a second scorecard for the same company → expect unique violation.
5. **H-05:** set `primary_domain` on a second company to an existing domain → expect `Duplicate domain` exception, and the original row unchanged.
6. Regression: re-run the researcher read path and confirm the 13 Refokus findings and the completed task are untouched.

Each check is a test case; until they run, none of the fixes is "done".

---

## 4. Audit claims I could not confirm

Recorded so they are neither silently accepted nor lost:

- "`enable_signup = true` on the CRM project" — the setting was not present where the audit cited it (`config.toml` auth section). **Unverified.**
- "`auto_expose_new_tables` left at default true" — the key is commented out (L23), so the *default* applies; that part is consistent, but the practical exposure depends on the hosted project's API settings, which I cannot read. **Partially verified.**
- Specific line citations in `phase-2-final-security-review.md` and the claim that agents can `SELECT outreach_messages` (policy requires `is_agent_type('drafter')`) — consistent with my reading, but the document's line numbers are stale throughout.

## 5. The pattern behind these findings

Four of the five fixed findings share one cause: **a rule was enforced in prose (contracts and phase reports) but not in the policy or trigger that actually runs.** Documentation claiming a control exists is not a control. The migration moves each rule into the database, and `tests/test_human_gates.py` already asserts the agent-role half of the story.
