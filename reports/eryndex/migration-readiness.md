# Migration Readiness Report (Directive 03 · Phase A)

**Date:** 2026-11-13 · **Target:** Development `vyfqmrdznmocnmzyjjcj` · **Production `vqnamszywsuqjxuqseas` was never contacted.**
**Applied:** nothing. No DDL executed, no `db push`, no Dashboard SQL. This is a readiness assessment.

---

## 1. Capability matrix — four separate things, measured separately

| Capability | Status | Evidence |
|---|---|---|
| **CLI authentication** | ✅ working | `supabase projects list` returns 2 projects under org `ktdpfeytpkqnggejhscb` |
| **Project access** | ✅ working | Development `vyfqmrdznmocnmzyjjcj` visible, `linked: true`; `.temp/linked-project.json` matches |
| **Database connectivity** | ⚠️ **intermittent** | 3 of 5 attempts reached Postgres via the CLI's temporary `cli_login_postgres` role; 2 failed (`Connection terminated unexpectedly`, `Connection timed out`). Failures are pooler-level, not auth-level |
| **DDL permission** | ❓ **unproven, not attempted** | Requires either `supabase db push` (forbidden this increment) or the project DB password. `cli_login_postgres` implies elevated rights but that is an inference, not a measurement |

**Do not conflate these.** A successful `projects list` proves authentication only. The successful `migration list` proves connectivity. Neither proves DDL.

## 2. Remote migration history

```
supabase migration list --linked
{"migrations":[{"local":"20261107174129","remote":"20261107174129","time":"2026-11-07 17:41:29"}],
 "message":"Migrations listed"}
```

Exactly one migration is applied: `20261107174129_core_crm_schema` (the 2,090-line core schema). Both hardening and discovery are absent from the remote history, confirming they are unapplied — not merely uncommitted.

## 3. Local migration inventory

| Path | Lines | Status |
|---|---|---|
| `supabase/migrations/20261107174129_core_crm_schema.sql` | 2,091 | applied |
| `supabase/migrations/pending/20261113_hardening_batch1.sql` | 220 | **authored, unapplied** |
| `supabase/migrations/pending/20261113_discovery_candidates.sql` | 165 | **authored, unapplied** |

### Important structural fact

```
supabase db push --dry-run --remote
{"upToDate":true,"dryRun":true,"migrations":[],"message":"Remote database is up to date."}
```

The CLI sees **zero pending migrations**. `supabase/migrations/pending/` is outside the push path by design: a file in `supabase/migrations/` means "applied". This is good for safety (no accidental application) and means application requires an explicit decision — move the file with a real timestamp, or run it in the Dashboard.

## 4. Dependency and ordering analysis

| Migration | Depends on | Present? |
|---|---|---|
| hardening batch 1 | `companies`, `contacts`, `lead_qualifications`, `outreach_messages`, `suppression_registry`, `tasks`, `company_domains`; `update_updated_at_column()`, `is_agent()`, `is_human_operator()` | ✅ all deployed / defined |
| discovery candidates | `companies(id)` FK; `is_human_operator()`, `is_agent()`, `is_agent_type(text)`, `current_agent_identity()`, `update_updated_at_column()` | ✅ verified at `crm/schema.sql:18,63,83,93,104` |

- **No dependency between the two.** Neither references an object the other creates.
- `is_agent_type(p_type)` compares `app_metadata->>'subagent_type'` as free text (`crm/schema.sql:93-102`), so `is_agent_type('discovery')` is valid without an enum change. No `agent_type` enum exists to extend.
- **Recommended order:** hardening first (it repairs behaviour on tables that already hold data), then discovery (purely additive). Either order is technically safe.
- Each file is a single `BEGIN; … COMMIT;` transaction, so a partial application cannot occur.

## 5. Conflict analysis against the current remote schema

| Risk | Detail | Status |
|---|---|---|
| `uq_lead_qualifications_approved` cannot be created | fails if two approved scorecards already exist for one company | Phase 0 found **0** qualification rows; re-check immediately before applying (SQL below) |
| H-03 policy rejects previously allowed writes | any draft without an approved, non-disqualified qualification becomes impossible | `outreach_messages` was empty at Phase 0; re-check before applying |
| Trigger name collision | `trg_enforce_agent_task_transitions` is new | no existing trigger of that name in `crm/schema.sql` |
| New enum `discovery_candidate_status` | no existing type of that name | safe |
| `discovery_candidates.domain` unique index | staging table is new | safe |
| CLI environment label | Development reports `environment: "production"` | cosmetic; guard stays ref-based |

Pre-flight SQL (run as a read-only check before applying, Development only):

```sql
select 'approved_dupes', company_id, count(*) from lead_qualifications
 where review_status = 'approved' group by company_id having count(*) > 1;
select 'outreach_rows', count(*) from outreach_messages;
select 'findings', count(*) from research_findings;      -- expect 13
select 'tasks_done', count(*) from tasks where status = 'completed';  -- expect >= 1
```

## 6. Exact steps to apply later (owner action)

**Option A — CLI, all-or-nothing, recommended**

```bash
cd <repo> && git checkout feat/eryndex-platform-transform
git mv supabase/migrations/pending/20261113_hardening_batch1.sql \
       supabase/migrations/20261114090000_hardening_batch1.sql
git mv supabase/migrations/pending/20261113_discovery_candidates.sql \
       supabase/migrations/20261114090100_discovery_candidates.sql
supabase db push --dry-run --remote      # expect exactly 2 migrations, in this order
supabase db push --remote                # apply
supabase migration list --linked         # expect 3 applied
```

The timestamp prefix must be later than `20261107174129`. Then run the database test suite (`ERYNDEX_DB_URL=… python -m pytest tests/db -q`) and paste the output into `implementation-progress.md`.

**Option B — Dashboard SQL editor**

Paste `20261113_hardening_batch1.sql` (it includes its own `BEGIN;`/`COMMIT;`), verify §5 checks, then paste `20261113_discovery_candidates.sql`. Afterwards record the versions manually so `migration list` does not show drift, or the next `db push` will try to re-apply and fail on existing objects.

## 7. Outstanding risks

1. **Connectivity is flaky.** Two of five attempts failed at the pooler. A `db push` that dies mid-flight is still safe (single transaction rolls back), but the retry story must be "re-run the command", not "run it in pieces".
2. **DDL rights are inferred, not proven.** `cli_login_postgres` exists for `db push`; whether it can create types/tables/policies is only demonstrated by a successful push. Do not tell anyone "we can apply migrations" until that push succeeds.
3. **`db dump` is impractical here** (spawns a container, exceeded 150 s). Row-count verification before applying must use the Dashboard SQL editor or a `psycopg` connection string, not `db dump`.
4. **The 6 database tests cannot run yet**: they need `ERYNDEX_DB_URL` (a real Postgres DSN with password). The CLI's temporary role does not yield one.
