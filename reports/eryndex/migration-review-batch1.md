# Migration Review — for owner approval

**Prepared:** 2026-11-13
**Target:** Development only, `vyfqmrdznmocnmzyjjcj`. Production `vqnamszywsuqjxuqseas` is out of scope and has never been contacted.
**Prepared by:** Orchestrator (technical owner)
**Approval needed before anything is executed.** Nothing here has been applied.

Two migrations are ready. They are independent and can be applied separately.

---

## Migration 1 — `20261113_hardening_batch1.sql`

**Purpose:** close the five verified security/integrity findings.

**What it changes**

| Object | Change | Why |
|---|---|---|
| `fn_sync_contact_suppression`, `fn_sync_company_suppression` | become `SECURITY DEFINER` with pinned `search_path` | the opt-out path raised `42501` for the only role allowed to use it |
| `fn_enforce_agent_task_transitions` + trigger | new | agents had no transition whitelist; they could rewind and cancel |
| `agent_insert_outreach_messages` | replaced policy | adds approved-qualification + lifecycle precondition |
| `uq_lead_qualifications_approved` | new partial unique index | two approved scorecards could coexist |
| `fn_sync_company_primary_domain` | replaced function | conflict now raises instead of re-parenting another company's domain |

**What it does not change:** no table dropped, no column dropped, no row updated or deleted, no grant widened (the suppression fix deliberately does *not* grant `INSERT` to `authenticated` — that would let an operator forge ledger rows).

**Transactional?** Yes: one `BEGIN; … COMMIT;`. Any error aborts the whole batch.

**Concurrency:** the partial unique index is what closes the H-04 race; the pre-existing application-side check is not relied on.

**Compatibility check against existing triggers**

- `fn_enforce_task_governance` still runs; the new guard is additive and only narrows agent transitions.
- `fn_enforce_outreach_human_approval` still runs on update; the replacement insert policy only adds predicates.
- `fn_sync_company_qualification` still runs; the new index makes a second approval fail rather than silently winning.
- No trigger ordering conflicts: the new task trigger is `BEFORE UPDATE`, the existing one is also `BEFORE UPDATE`; Postgres fires same-timing triggers alphabetically by name (`trg_enforce_agent_task_transitions` before `trg_enforce_task_governance`), and both are pure guards, so order is not material.

**Risk of applying:** low, with one caveat — H-03 will now reject drafts that the current system would have allowed. Verify no such rows exist first:

```sql
select count(*) from outreach_messages om
join companies c on c.id = om.company_id
where not exists (
  select 1 from lead_qualifications lq
  where lq.company_id = om.company_id and lq.review_status = 'approved' and lq.qualification_tier <> 'disqualified'
);
```
`outreach_messages` is empty today (verified in Phase 0), so this returns 0.

**Rollback:** re-run the original definitions from `crm/schema.sql` (each section is independent). No data is destroyed, so rollback is safe.

---

## Migration 2 — `20261113_discovery_candidates.sql`

**Purpose:** give Workflow 1 somewhere to stage candidates so discovery never touches `companies`.

**Tables:** `discovery_candidates` (13 business columns + audit/review fields), `discovery_candidate_evidence` (child, `ON DELETE CASCADE`).
**Type:** `discovery_candidate_status` (`pending_review`, `approved`, `rejected`, `duplicate`, `conflict`).
**Indexes:** unique pending-domain, unique `(batch_key, domain)`, status, normalized name, evidence FK.
**RLS:** anon denied; operators full; discovery agents insert only `pending_review` rows they own and read their own back; agents cannot approve, reject or convert.
**Grants:** `SELECT, INSERT, UPDATE, DELETE` to `authenticated`, all constrained by RLS (matches the existing schema's pattern — RLS is the boundary, not the grant).
**Constraints:** domain must be lowercase and non-empty; review fields must be null unless the candidate has left `pending_review`; evidence URLs must be http(s).

**Rollback:** drop child table, parent table, then the type (statements at the end of the file). Export staged rows first.

**Why this is the minimum:** it is the only new persistence the Discovery → Qualification path needs. `partnerships`/`projects` remain undeployed — see the assessment in `reports/eryndex/workflow-9-assessment.md`, which recommends deferring them.

---

## Test plan (both migrations)

| Layer | What runs | Status |
|---|---|---|
| Static | `tests/test_security_sql_static.py` — asserts the controls exist in the authored SQL, the file is transactional, nothing destructive is present, and the deployed schema still holds the original defects | **9 passing now** |
| Database | `tests/db/test_security_fixes_db.py` — one test per finding + a Refokus regression check; refuses to run against Production | **6 tests ready, skipped without `ERYNDEX_DB_URL`** |
| Application | `python -m pytest -q` | 146 → expected ~195 after this increment |
| Live | Refokus read-back (13 findings, completed task) after applying | pending |

## Approval checklist

- [ ] Owner confirms target is Development `vyfqmrdznmocnmzyjjcj`.
- [ ] Owner approves Migration 1 (security).
- [ ] Owner approves Migration 2 (discovery staging).
- [ ] Backup taken immediately before applying (`backups/dev-snapshot-*.json`).
- [ ] Apply order: Migration 1, verify, then Migration 2, verify.
- [ ] Post-apply: run `ERYNDEX_DB_URL=… python -m pytest tests/db -q` and attach the output to `reports/eryndex/implementation-progress.md`.
- [ ] Move each file into `supabase/migrations/` with its applied timestamp only after verification.

## Why I am not applying these myself

I have no credential that can issue DDL against this project: the local Supabase CLI is authenticated to a different account (`AuraVibe` only), and no database password or `DATABASE_URL` exists on this machine. A personal access token alone would not grant Postgres access anyway. The least-privilege route is the Dashboard SQL editor (owner's browser, no credential leaves the machine), or the project database password with `supabase link` + `supabase db push`.
