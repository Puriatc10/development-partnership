# Supabase Access & Readiness Matrix — Directive 03.1 §4

**Date:** 2026-11-14
**Target:** Development `vyfqmrdznmocnmzyjjcj` ("Puriatc10's Project", org `ktdpfeytpkqnggejhscb`, ap-southeast-2)
**Production `vqnamszywsuqjxuqseas`:** never targeted, never linked, never contacted in this increment.
**What was executed:** read-only CLI inspection only. No `db push` without `--dry-run`, no migration SQL, no schema change, no Auth user, no `db dump`, no `link`. Nothing was committed.

Every network/CLI call was wrapped in `timeout 90` (the dry-run in `timeout 120`). Secrets are neither printed nor requested; the CLI's temporary `cli_login_postgres` password was not extracted.

---

## 1. Capability matrix — five separate things, measured separately

Conflating these is how "we have database access" becomes false. Each row states what the evidence proves and what it does not.

| # | Capability | Verdict | Exact evidence | What this does **not** prove |
|---|---|---|---|---|
| 1 | **CLI authenticated** | ✅ **PASS** | `supabase --version` → `3.0.0`. `supabase projects list` exit `0`, JSON returns both refs under `organization_id: ktdpfeytpkqnggejhscb`, both `status: ACTIVE_HEALTHY` | Nothing about any database |
| 2 | **Projects visible** | ✅ **PASS** | Same payload: Development `vyfqmrdznmocnmzyjjcj` with `"linked":true`; Production `vqnamszywsuqjxuqseas` with `"linked":false`. Development `database.version` `17.11.0.003`, region `ap-southeast-2` | Visibility is an org-membership fact, not a data fact |
| 3 | **Project linked** | ✅ **PASS** (already correct — no `link` run) | `supabase/.temp/project-ref` = `vyfqmrdznmocnmzyjjcj`; `supabase/.temp/linked-project.json` = `{"ref":"vyfqmrdznmocnmzyjjcj","name":"Puriatc10's Project","organization_id":"ktdpfeytpkqnggejhscb",…}`. Both match the allow-list ref. `supabase link` was therefore **not** run: the ref is right, so re-linking would have been a needless write to `.temp/` | Linking writes local state only; it does not test or grant a connection |
| 4 | **Postgres connectable** | ⚠️ **PASS, INTERMITTENT — 6/8 (75%)** | `supabase migration list --linked` exit `0` on 3/3 attempts this increment, each preceded by `Initialising login role...` and returning remote history from the database. Prior increment: 3/5 succeeded, 2 failed at the pooler (`Connection terminated unexpectedly`, `Connection timed out`) | That a **durable DSN** exists. The CLI mints a short-lived role per invocation; no reusable connection string is produced (§7) |
| 5 | **DDL safe to claim** | ❌ **NO CLAIM — unproven, not attempted** | There is no measurement. `db push` was run **only** with `--dry-run`, which prints a plan and applies nothing. No `CREATE`/`ALTER`/`DROP` reached the server. `cli_login_postgres` *suggests* elevated rights; that is an inference from a role name, not an observation | Anything about table creation, type creation, or policy replacement. **The migrations in §5 are unapplied and this report does not claim otherwise** |

`environment: "production"` appears in every CLI v3 target block for `vyfqmrdznmocnmzyjjcj`. That is branch semantics on the project's primary database, not this org's Production project; no conclusion anywhere in this report leans on that field. The gate remains the ref allow-list in `contracts/environment.py`.

### Target-binding tripwire found while checking §1

`supabase/config.toml:5` sets `project_id = "development-partnership"` — which is the **name of the Production project**, not of the linked one. CLI v3 uses `project_id` for local container naming and reads the remote target from `.temp/`, so this is not currently a mis-target. It is a naming trap: the string a human reads as "the production project" sits in the config file of a repo that is linked to Development. Left unmodified (out of scope), flagged in §9.

---

## 2. Remote migration history

Verbatim, 3/3 attempts identical (`--linked` emits a deprecation warning; `--remote` is the v4 spelling):

```
Target: project Puriatc10's Project (vyfqmrdznmocnmzyjjcj) · production
Initialising login role...
{"migrations":[{"local":"20261107174129","remote":"20261107174129","time":"2026-11-07 17:41:29"}],
 "message":"Migrations listed",
 "target":{"kind":"linked","project_ref":"vyfqmrdznmocnmzyjjcj",
           "project_name":"Puriatc10's Project","environment":"production"}}
```

**History string:** `20261107174129` — exactly one row, matched local≠remote-free. Both pending migrations are absent from remote history, so they are **unapplied**, not merely uncommitted. The CLI required the temporary login role on every attempt (`Initialising login role...` on the connect path — that role is what reaches Postgres, and its credentials stay inside the CLI).

---

## 3. `db push --dry-run --remote` — did it see the pending migrations?

**No. It saw zero.**

```
DRY RUN: migrations will *not* be pushed to the database.
{"upToDate":true,"dryRun":true,"migrations":[],"seeds":[],"roles":[],
 "message":"Remote database is up to date.",
 "target":{"kind":"linked","project_ref":"vyfqmrdznmocnmzyjjcj", …}}
```

Three independent reasons, all verified in the checkout:

1. **Location.** The pending files live in `supabase/migrations/pending/`. The CLI's migration path is `supabase/migrations/` only; it does not recurse into subdirectories. This exclusion is deliberate (`supabase/migrations/pending/README.md`: "a file in `supabase/migrations/` means applied"), so "up to date" is the *expected* answer and is not evidence that the remote schema matches the authored work.
2. **Filename shape.** The CLI versions a migration by the numeric prefix of `<digits>_<name>.sql`. The deployed file is `20261107174129_…` (14 digits); both pending files are `20261113_…` (8 digits) — a date, not a version timestamp. Even relocated verbatim they are not guaranteed to be picked up, and if they are, **both collapse to the same version `20261113`**, which is an ordering ambiguity, not an ordered pair. The rename is not cosmetic (§8).
3. **`config.toml`.** `[db.migrations] enabled = true`, `schema_paths = []` (`config.toml:60-65`). No extra path is registered, so nothing widens the scan.

Consequence: **`db push` cannot be the application mechanism** until the files are moved *and* renamed. Any claim of "the CLI would apply them" is untested.

---

## 4. Local migration inventory

| File | Lines | Bytes | sha256 | Status |
|---|---|---|---|---|
| `supabase/migrations/20261107174129_core_crm_schema.sql` | 2089 | 98192 | `d1dbb0fda3503934e7187233349eab5b8852ae09271f8480675dc9f01ad6bbfb` | **applied** (remote history agrees) |
| `supabase/migrations/pending/20261113_hardening_batch1.sql` | 224 | 10106 | `57f3b23319e0c2c8ed3080dbce73cfd1d9d76ab912bd6556bb8b24b866421246` | **authored, unapplied** (sha updated after the suppression-source correction in §4 of `migration-review-batch1.md`) |
| `supabase/migrations/pending/20261113_discovery_candidates.sql` | 165 | 7452 | `d3c8544e5558d9eeb2a2282175e7828fc63f7085d0612f5ac35caaca44d9a71d` | **authored, unapplied** |

`diff` of the applied migration against `crm/schema.sql` shows only header differences (BOM, title, phase line, target line) — the deployed schema is the 2090-line design document, so `crm/schema.sql` line citations below are valid against the **remote** schema.

Documentation drift in the same directory: `supabase/migrations/pending/README.md` lists **only** `20261113_hardening_batch1.sql` in its files table. `20261113_discovery_candidates.sql` is present and reviewed elsewhere but missing from that index — an owner following the README would apply one of two files.

---

## 5. Readiness review — dependency verification against the deployed schema

Every object each migration references was grepped in `crm/schema.sql` and confirmed to exist with a line number. No new object collides with a deployed name.

### 5.1 `20261113_hardening_batch1.sql`

| Migration references | Deployed? | Where |
|---|---|---|
| `fn_sync_contact_suppression` (replaced) | ✅ | `crm/schema.sql:861`, trigger `:888` |
| `fn_sync_company_suppression` (replaced) | ✅ | `crm/schema.sql:894`, trigger `:910` |
| `fn_sync_company_primary_domain` (replaced) | ✅ | `crm/schema.sql:841`, trigger `:852` |
| `suppression_registry` + `ON CONFLICT (identifier)` | ✅ | `:316`; `identifier TEXT NOT NULL UNIQUE` at `:319` |
| `companies.primary_domain / is_suppressed / suppression_reason` | ✅ | `:340`, `:358`, `:359` |
| `company_domains(company_id, domain, is_primary)` | ✅ | `:390`; `domain … UNIQUE` at `:394` |
| `tasks` + `status task_status_type` | ✅ | `:738`, `:745`; enum `:286` = `pending,in_progress,completed,cancelled` |
| `is_agent()`, `is_agent_type()`, `current_agent_identity()` | ✅ | `:83`, `:93`, `:104`; EXECUTE granted to `authenticated` at `:2080` |
| `fn_enforce_task_governance` (still active, new guard is additive) | ✅ | `:1494`, agent branch `:1507` |
| `agent_insert_outreach_messages` policy (dropped + recreated) | ✅ | `:1977` |
| `outreach_messages` columns used by the new policy | ✅ | `:596` (`company_id`, `contact_id`, `campaign_id`, `drafted_by_agent_identity`, `approval_status`, `approved_by_human_id`, `approved_at`, `is_admin_override`, `override_reason`, `override_by_human_id`, `sending_status`, `proposed_commercial_model`) |
| `lead_qualifications(review_status, qualification_tier, company_id)` | ✅ | `:514`, `:528`, `:531`, `:532` |
| `companies.lifecycle_stage` values `qualified/outreach_queued/in_outreach` | ✅ | enum `:153` |
| **New names free of collision** `fn_enforce_agent_task_transitions`, `trg_enforce_agent_task_transitions`, `uq_lead_qualifications_approved` | ✅ | zero grep hits in `crm/schema.sql` |

**Verdict: READY-WITH-PRECONDITION.** Five findings' worth of fixes, single `BEGIN;…COMMIT;` (L21/L220), no `DROP TABLE`, no `DROP COLUMN`, no `UPDATE`/`DELETE` of existing rows, no grant widening. `CREATE INDEX IF NOT EXISTS idx_lead_qualifications_company` already exists (`:1695`) so the new partial index is the only index change. Preconditions below are data-dependent and must be re-checked at apply time, because this machine cannot read those rows.

Pre-flight (Development only, read-only):

```sql
-- H-04: a duplicate approved scorecard aborts CREATE UNIQUE INDEX (whole txn rolls back)
select 'approved_dupes' as chk, company_id, count(*)
  from lead_qualifications where review_status = 'approved'
 group by company_id having count(*) > 1;

-- H-03: drafts that the new policy would forbid (expected 0 — outreach_messages was empty at Phase 0)
select 'outreach_would_break' as chk, count(*) from outreach_messages om
 where not exists (select 1 from lead_qualifications lq
                    where lq.company_id = om.company_id
                      and lq.review_status = 'approved'
                      and lq.qualification_tier <> 'disqualified')
    or not exists (select 1 from companies c
                    where c.id = om.company_id
                      and c.lifecycle_stage in ('qualified','outreach_queued','in_outreach'));

-- H-05: domains already owned by a different company than the one that claims them as primary.
-- The old trigger re-parented these silently; the new one raises. Any row here will make the
-- NEXT write to that company fail with "Duplicate domain".
select 'domain_takeover_rows' as chk, cd.company_id as domain_owner, c.id as claiming_company, cd.domain
  from company_domains cd
  join companies c on lower(c.primary_domain) = cd.domain
 where cd.company_id <> c.id;

-- H-02: agent tasks in a state the new whitelist would freeze (informational)
select 'agent_tasks_by_status' as chk, status, count(*) from tasks
 where assigned_actor_type = 'agent' group by status;
```

Two findings the review documents do **not** record:

- **H-01 silently relabels the ledger source.** The deployed `fn_sync_company_suppression` writes `source = 'manual_blacklist'` with default reason `'Company suppression/blacklist'` (`crm/schema.sql:902-903`). The replacement writes `source = 'opt_out_request'` with default reason `'Company suppression'` (pending file L78-79). `suppression_registry.source` is free `TEXT` (`:321`, documented vocabulary in its comment) so nothing fails — the *provenance* of future company suppressions changes with no mention in the batch comment, `reports/eryndex/migration-review-batch1.md`, or `security-findings.md`. Either accept it (and say so) or patch the literal back before applying.
- **`SECURITY DEFINER` is a deliberate escalation.** H-01's fix runs the two suppression triggers as the function owner, bypassing the `GRANT SELECT`-only posture at `crm/schema.sql:2086`. It is the right call (the alternative was granting `INSERT` to `authenticated`, which allows forging ledger rows) but it means these two functions now write with owner rights. They pin `SET search_path = public, pg_temp` and only insert values from `NEW`, so the escalation surface is the insert statement itself. Worth a second pair of eyes, not a blocker.

Not re-runnable by design: `CREATE TRIGGER trg_enforce_agent_task_transitions` (L121) has no `IF NOT EXISTS` form in PostgreSQL. A retry after a *failed* run is safe (single transaction rolled back); a retry after a *successful* run fails on that line.

### 5.2 `20261113_discovery_candidates.sql`

| Migration references | Deployed? | Where |
|---|---|---|
| `companies(id)` FK target | ✅ | `crm/schema.sql:336` |
| `update_updated_at_column()` | ✅ | `:18` |
| `is_human_operator()` / `is_agent()` / `is_agent_type()` / `current_agent_identity()` | ✅ | `:63`, `:83`, `:93`, `:104` |
| `gen_random_uuid()`, `clock_timestamp()` | ✅ | pgcrypto/PG13+ built-ins, used throughout |
| **New names** `discovery_candidate_status`, `discovery_candidates`, `discovery_candidate_evidence` | ✅ free | zero grep hits in `crm/schema.sql` |
| `is_agent_type('discovery')` | ✅ valid | `:93-102` compares `app_metadata->>'subagent_type'` as free text — no enum to extend |

**Verdict: READY.** Purely additive: new enum, two new tables, two indexes, RLS + six policies, one trigger. Single `BEGIN;…COMMIT;` (L23/L150). No dependency on the hardening batch and no object it needs from it — the two are order-independent at the DDL level.

Non-blocking observations:
- `uq_discovery_candidates_batch_domain` is `UNIQUE (batch_key, domain)` with `batch_key` nullable — NULL `batch_key` rows escape the idempotency guard the header comment promises. Application discipline must supply the key.
- The file's rollback block sits **after** `COMMIT` in the same file. Applying the file top-to-bottom is safe; pasting the whole file into a Dashboard editor that runs it as one batch would execute the commented tail harmlessly but is not a rollback.
- `REVOKE ALL … FROM anon, authenticated` then `GRANT SELECT, INSERT, UPDATE, DELETE … TO authenticated` correctly re-establishes the `:2070` posture for the new tables; `anon` stays denied (plus explicit `anon_deny_*` policies).

**Ordering between the two files:** no DDL constraint either way. There *is* a semantic order: `discovery_candidates` is where Gate 1 approves a candidate into `companies`, and only then can an operator approve a scorecard — which is what `fn_sync_company_qualification` (`:1407`, lifecycle promotion at `:1413-1416`) uses to move a company to `'qualified'`, which is in turn H-03's outreach precondition. Applying discovery first makes the H-03 precondition reachable without hand-written SQL. Mirroring `reports/eryndex/migration-readiness.md`, the recommended order is hardening → discovery; functionally discovery → outreach works too.

`chk_companies_disqualified_stage` (`:373-375`) does **not** block `lifecycle_stage = 'qualified'`: it only constrains rows whose `qualification_tier` is `'disqualified'`. H-03 is therefore satisfiable.

---

## 6. Is a real Postgres DSN available anywhere legitimate?

| Probe | Result |
|---|---|
| `ERYNDEX_DB_URL` in the environment | **not set** |
| `SUPABASE_DB_URL` in the environment | **not set** |
| `DATABASE_URL` in the environment | **not set** |
| `.env` keys (names only, values not read) | `OPERATOR_EMAIL`, `OPERATOR_PASSWORD`, `RESEARCHER_AGENT_ID`, `RESEARCHER_EMAIL`, `RESEARCHER_PASSWORD`, `SUPABASE_ANON_KEY`, `SUPABASE_PROJECT_REF`, `SUPABASE_URL` — **no `DATABASE_URL`, no `ERYNDEX_DB_URL`, no service-role key, no DB password** |
| `supabase/.temp/pooler-url` | `postgresql://<user>@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres` — parsed: `username_present=True`, **`password_present=False`** (unchanged from the prior observation) |

The CLI's temporary `cli_login_postgres` password was **not** extracted, and is not usable for `pytest` anyway (it is minted per invocation and scoped to the CLI's session).

**Net:** connectivity exists *through the CLI only*. Every path that needs a DSN — `psql`, `psycopg`, `python -m pytest tests/db` — is still blocked. `supabase db dump` was not re-attempted: it previously spawned a container and exceeded 150 s, and the 120 s budget is the instruction's limit. Backups must come from the Dashboard or an owner-supplied DSN.

---

## 7. Connectivity log (this increment)

| Attempt | Command | Exit | Result |
|---|---|---|---|
| 1 | `timeout 90 supabase migration list --linked` | 0 | remote history returned |
| 2 | `timeout 90 supabase migration list --linked` | 0 | identical |
| 3 | `timeout 90 supabase migration list --linked` | 0 | identical |

**3/3 this increment; 6/8 (75%) cumulative** with the prior increment (3/5, two failures: `Connection terminated unexpectedly`, `Connection timed out` — pooler-level, before authentication completed). Treat connectivity as **available but not dependable**: a single success is not a guarantee that the next write survives.

---

## 8. Exact owner commands to apply later

Nothing below has been run. Do not run it until the pre-flight in §5.1 returns zero rows.

### Step 0 — preconditions (both required)

- Confirm `git rev-parse --abbrev-ref HEAD` is `feat/eryndex-platform-transform`.
- Confirm the target: `grep . supabase/.temp/project-ref` → `vyfqmrdznmocnmzyjjcj`. If it is anything else, **stop**.
- Take a logical backup (Dashboard → Database → Backups, or a `psql`/`pg_dump` run with an owner-supplied DSN). `db dump` is not viable here.
- Run the §5.1 pre-flight SQL read-only (Dashboard SQL editor, Development only).

### Option A — CLI (`git mv` + rename, then push)

The rename is mandatory, not cosmetic: an 8-digit date prefix either fails to match the CLI's `<version>_<name>.sql` shape or collides the two files onto the same version `20261113`. Use a 14-digit prefix strictly later than `20261107174129`.

```bash
cd "C:/Users/puria/Desktop/code-workspace/projects/development-partnership"
git checkout feat/eryndex-platform-transform

git mv supabase/migrations/pending/20261113_hardening_batch1.sql \
       supabase/migrations/20261114090000_hardening_batch1.sql
git mv supabase/migrations/pending/20261113_discovery_candidates.sql \
       supabase/migrations/20261114090100_discovery_candidates.sql

# 1. prove the CLI now sees exactly two, in order — this is the cheap checkpoint
supabase db push --dry-run --remote
#    expect: upToDate:false and migrations[] = [20261114090000, 20261114090100]
#    if it still says upToDate:true, STOP — the move or the name is wrong, and a push
#    would record nothing while the repo now looks applied.

# 2. apply (the one irreversible-permission step; owner's decision)
supabase db push --remote

# 3. verify
supabase migration list --linked        # expect 3 rows, all local == remote
ERYNDEX_DB_URL='postgresql://…' python -m pytest tests/db -q   # needs the owner's DSN
```

Note the asymmetry: `git mv` **alone** changes the repository's meaning (file inside `migrations/` = "applied") without touching the database. If `db push` is not run immediately after, the repo lies. Do both in one sitting, or neither.

### Option B — Dashboard SQL editor (no CLI, no DSN)

1. Development project → SQL Editor.
2. Paste `20261113_hardening_batch1.sql` **entirely**, including its `BEGIN;`/`COMMIT;`. One statement batch; any error rolls the batch back.
3. Run the §5 "Verification plan" checks from `reports/eryndex/security-findings.md:38-49` (H-01 opt-out writes a row with no `42501`; H-02 `in_progress→pending` raises `Lifecycle Violation`; etc.).
4. Paste `20261113_discovery_candidates.sql` the same way; confirm the two tables and the enum exist.
5. **Record the versions in the repo anyway** — `git mv` to `supabase/migrations/20261114090000_…` and `…090100_…` after the fact, or the next `db push` will try to re-apply and fail on `CREATE TYPE`/`CREATE TABLE`/`CREATE TRIGGER` (none of them re-runnable). Alternatively `supabase migration repair --status applied 20261114090000` for a Dashboard-applied batch.

### Rollback

Each hardening section is independent: re-run the original definition from `crm/schema.sql` (`:841`, `:861`, `:894`, `:1977`) to revert one. Discovery has an explicit teardown comment block at the end of its file (`DROP TABLE evidence → candidates → TYPE`). No rollback destroys data because no data is rewritten.

---

## 9. Risks, ranked

1. **DDL remains unproven, and the apply path is blocked from this machine.** No DSN exists (§6), `db push` sees zero migrations (§3), and `db push` has never applied anything. Every documented route needs either an owner-supplied password (Option A/B step 2) or the Dashboard. Until one succeeds, the only true statement is "authored and reviewed" — and the repo convention (`migrations/` = applied) makes it trivially easy to *look* applied without being applied.
2. **A `git mv` without a push silently fabricates migration history.** This is the failure mode the directory split was built to prevent, and it is one command away. Mitigation: dry-run must show two migrations before any push; the two steps belong in one session.
3. **The move alone may still not be seen.** Filename shape is the second gate (`<14-digit>_<name>.sql`). `--dry-run` after `git mv` is the only cheap proof; if it reports `upToDate:true`, stop and fix the names.
4. **Connectivity is 75%, not 100%.** A push that dies mid-flight is safe (single transaction) but the operator must retry the *whole* command, never a fragment. `db push` has no resumable mode.
5. **H-01 changes audit provenance without a review entry.** Company suppressions will record `source='opt_out_request'` where they used to record `'manual_blacklist'` (§5.1). Decide before applying: accept and document, or restore the literal.
6. **H-03 narrows a live write path.** After applying, a drafter agent cannot insert a draft for a company without an approved, non-disqualified scorecard *and* a `qualified`-or-later lifecycle stage. That is the point of the fix, but it converts a silent gap into a hard `42501`-class refusal for any workflow that relied on the old behaviour.
7. **H-04's index is data-sensitive.** Two approved scorecards for one company abort the whole batch — safe (rollback) but confusing without the pre-flight query.
8. **H-05 turns a silent re-parent into a raised exception.** Any row already in the "takeover" shape (§5.1 pre-flight) will now fail the *next* write to that company. Correct behaviour, new failure mode.
9. **`config.toml:5` names the Production project.** Config drift waiting to confuse a human or a future script; ref-based gating is what is actually protecting the boundary.
10. **`pending/README.md` indexes one of two files.** An owner following it applies hardening and forgets discovery.
11. **`db dump` is not a backup option here** (container spawn > 120 s). Row-count and post-apply verification must use the Dashboard or an owner DSN.

---

## 10. Still unknown (not inferred into a claim)

- Whether `cli_login_postgres` can execute DDL. Unmeasured; a dry-run cannot answer it.
- Whether the two authored migrations actually apply cleanly. Untested against any database.
- Whether the Development database contains the duplicate/takeover rows that §5.1 pre-flight looks for. Not readable from this machine.
- Whether the Refokus scorecard is `approved` and the company `qualified` — the H-03 precondition. Unverifiable without a DSN or the Dashboard.
- Whether the remote schema equals `crm/schema.sql` beyond the migration-history row. History agreement is not a schema diff.
