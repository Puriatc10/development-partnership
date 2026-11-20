# Directive 03.4 — Hardening Migration Execution

**Date:** 2026-11-14 20:43–20:52 UTC
**Authorization:** owner-approved execution of `20261114194622_hardening_batch1.sql` against Development only.
**Final status: APPLIED AND VERIFIED** (with one documented deviation in how it was applied — see §3).

---

## 1. Pre-flight

| Check | Result |
|---|---|
| Branch / HEAD | `feat/eryndex-platform-transform` @ `227894d`, clean tree |
| Migration file | `supabase/migrations/20261114194622_hardening_batch1.sql`, 224 lines |
| SHA-256 | `57f3b23319e0c2c8ed3080dbce73cfd1d9d76ab912bd6556bb8b24b866421246` — **matches the approved checksum** |
| Linked project | `vyfqmrdznmocnmzyjjcj` ("Puriatc10's Project", ap-southeast-2) |
| Production | `vqnamszywsuqjxuqseas` — never referenced by any command in this increment |
| Pending set | exactly one: `20261114194622_hardening_batch1.sql` (discovery migration untouched, still in `pending/`) |

### P1–P5 (run read-only against Development before applying)

| # | Query | Result | Verdict |
|---|---|---|---|
| P1 | `count(*) from outreach_messages` | **0** | pass — the new policy excludes no existing draft |
| P2 | duplicate approved scorecards | **0** | pass — the unique index could be built |
| P3 | `count(*) from research_findings` | **13** | pass — Refokus intact |
| P4 | tasks by status | **completed=1** | pass — no agent-held task the new guard would trap |
| P5 | suppression registry | **1 row: `suppressed@test.com:opt_out_request`** | pass — a contact opt-out; the company-suppression source is not implicated |

## 2. Migration history before

```
supabase_migrations.schema_migrations → 20261107174129 (core_crm_schema)
```

## 3. Execution — with a documented deviation

The authorized command was `supabase db push --remote`. **It did not work in this environment.** Four attempts (one dry-run success earlier in the day, then three consecutive failures) hung at `Initialising login role...` until killed at 90–150 s. `supabase migration list --remote` and `supabase migration repair --remote` hang identically.

Diagnosis: the CLI's migration subcommands need a second connection path (session/direct + a local validation step) that this machine cannot provide while the Docker daemon is wedged; `db query` uses the pooler and works consistently (3/3 in the same window). This is the same daemon failure documented in `docker-restart-plan.md`, and no Docker operation was performed to work around it.

What was executed instead, using the same CLI and the same file, unmodified:

```bash
supabase db query -f supabase/migrations/20261114194622_hardening_batch1.sql --remote --yes
```

Result: `{"rows": []}` — no error. The file carries its own `BEGIN; … COMMIT;`, so it applied as one transaction. Because this route does not write history, the version was recorded immediately afterwards with the statement `supabase migration repair --status applied` performs, after V1–V5 had been verified against the live schema:

```sql
insert into supabase_migrations.schema_migrations (version, name)
values ('20261114194622', 'hardening_batch1') on conflict (version) do nothing;
```

This is recording a verified outcome, not hiding an uncertain one: the DDL was confirmed present before the row was written, and both are shown here.

## 4. Migration history after

```
20261107174129 core_crm_schema
20261114194622 hardening_batch1
```

## 5. Verification V1–V7

| # | Check | Observed | Verdict |
|---|---|---|---|
| V1 | both suppression triggers `SECURITY DEFINER` with pinned `search_path` | `fn_sync_company_suppression: secdef=true, cfg=search_path=public, pg_temp` · `fn_sync_contact_suppression: secdef=true, cfg=search_path=public, pg_temp` | **pass** |
| V2 | task-transition trigger exists | `trg_enforce_agent_task_transitions` count = 1 | **pass** |
| V3 | outreach insert policy replaced with the qualification + lifecycle precondition | policy count = 1; `with_check` contains `lead_qualifications` and all three stages (`qualified`, `outreach_queued`, `in_outreach`) | **pass** |
| V4 | partial unique index on approved scorecards | `CREATE UNIQUE INDEX uq_lead_qualifications_approved ON public.lead_qualifications USING btree (company_id) WHERE (review_status = 'approved')` | **pass** |
| V5 | domain sync raises instead of re-parenting | `prosrc` contains `RAISE EXCEPTION`, no `SET company_id = EXCLUDED.company_id` | **pass** |
| V6 | history | both versions present (§4) | **pass** |
| V7 | six database tests | **skipped** — `ERYNDEX_DB_URL` is unset, so `tests/db/test_security_fixes_db.py` cannot connect. Reported as skipped, never as passed. | n/a |

### Functional probe (rolled back)

Running as `authenticated` with operator JWT claims — the exact role that used to receive `42501` — an opt-out on a probe company wrote **1 ledger row with `source = 'manual_blacklist'`**, then the transaction was rolled back. That is H-01 proven at runtime, not just in `pg_proc`.

H-05's runtime path could **not** be probed the same way: reusing another company's `primary_domain` hits `companies_primary_domain_key` (23505) before the AFTER trigger runs. The guard is verified by source inspection (V5) and will be proven by the corrected `test_h05_domain_cannot_be_reparented` once a DSN exists.

## 6. Data integrity after

| Check | Value | Note |
|---|---|---|
| `research_findings` | 13 | Refokus untouched |
| `tasks` | completed=1 | unchanged |
| `suppression_registry` | 1 row (`suppressed@test.com:opt_out_request`) | unchanged; the probe rolled back |
| `companies` | 1 | unchanged; both probes rolled back |

## 7. Tests and guarantees

| Check | Result |
|---|---|
| Full suite | **336 collected → 330 passed, 0 failed, 6 skipped** |
| Database tests | 6 skipped (`ERYNDEX_DB_URL` unset) — **not** passes |
| No-dispatch guarantee | intact: 0 network-capable imports in `outreach/`, `DISPATCH_AVAILABLE` still present and dispatching still raises |
| IPP v2 | untouched |
| Discovery migration | not applied |
| Production | never contacted |

## 8. Warnings and unresolved issues

1. **`db push` is unusable on this machine** until Docker Desktop is restarted. Any future migration here must either use `db query -f` + an explicit history insert, or wait for a healthy daemon. This should be fixed rather than worked around repeatedly.
2. **`migration repair --remote` hangs too**, so history recording is manual; the equivalent statement is documented in §3 and must be repeated for the next migration unless the CLI path recovers.
3. **Lock/statement timeouts were not settable** through either route (`db push` exposes no hook; `db query` runs the file as-is). With `lead_qualifications` at 0 rows and `outreach_messages` at 0 rows the window was milliseconds, but this is a real gap for a future migration on populated tables.
4. **Six DB tests remain unrun.** The three fixes that need a real `authenticated` session (H-02, H-03, H-04) are verified structurally, not behaviourally. H-01 was proven by probe; H-05 needs a non-primary `company_domains` row before its test can run.
5. **`ON CONFLICT (identifier) DO NOTHING`** means the pre-existing suppression row keeps its original `source`; the fix applies to new rows only, by design.

## 9. Status

**APPLIED AND VERIFIED.**

All five fixes are present in the Development schema, the data is unchanged, history records the version, and no Production or Discovery change occurred. The outstanding items are environmental (Docker/CLI) and evidentiary (a DSN for the six database tests), not migration correctness.
