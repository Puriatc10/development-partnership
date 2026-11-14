# Migration Approval Packet — `20261113_hardening_batch1.sql`

**Prepared:** 2026-11-14 (review date)
**Subject:** `supabase/migrations/pending/20261113_hardening_batch1.sql`, 224 lines, sha256 `57f3b23319e0c2c8ed3080dbce73cfd1d9d76ab912bd6556bb8b24b866421246` (re-computed during this review, matches the value under review).
**Companion file (separate approval):** `supabase/migrations/pending/20261113_discovery_candidates.sql`, 165 lines, sha256 `d3c8544e5558d9eeb2a2282175e7828fc63f7085d0612f5ac35caaca44d9a71d`.
**Intended target:** Development `vyfqmrdznmocnmzyjjcj` only.
**Verdict:** see §12.

---

## 1. Scope and method

### 1.1 What was read

| Artefact | Purpose here |
|---|---|
| `supabase/migrations/pending/20261113_hardening_batch1.sql` (224 L) | the file under review, read in full |
| `supabase/migrations/pending/20261113_discovery_candidates.sql` (165 L) | separate section, §10 |
| `crm/schema.sql` (2090 L) | deployed schema of record; every referenced object re-derived from it |
| `tests/test_security_sql_static.py` (111 L) | the static assertions that already pin these fixes |
| `tests/db/test_security_fixes_db.py` (head) | the executable counterpart, still skipped without `ERYNDEX_DB_URL` |
| `reports/eryndex/security-findings.md` | H-01…H-05 findings this batch claims to close |
| `reports/eryndex/migration-review-batch1.md` | prior owner-facing review |
| `reports/eryndex/phase-0-baseline.md` | row-count baseline and backup artefact |
| `reports/eryndex/supabase-access-matrix.md` | credential and apply-route facts |
| `integrations/supabase/qualification_repository.py`, `agents/researcher/agent.py` | callers that the new guards could break |

### 1.2 What was NOT executed — explicit statement

**No SQL ran. Not one statement, in any environment.** Specifically, during this review:

- The two migration files were **read**, not executed.
- `supabase db push` was **not** run, not even `--dry-run`. No `supabase` command was run at all.
- No migration was applied, renamed, or moved. `supabase/migrations/` still contains exactly one file, `20261107174129_core_crm_schema.sql`.
- No connection was opened to any database. The remote Development project was not contacted.
- Production `vqnamszywsuqjxuqseas` was never named to any tool, never resolved, never connected.
- No secret was read, printed, or requested. `.env` was not opened.

The only things actually executed were **read-only local operations**: `git log`/`git status`, `sha256sum`, `grep`/`sed`, and `python -m pytest` (which runs pure-Python tests against files and mocks). Test evidence from this review:

| Command | Result |
|---|---|
| `sha256sum` on both pending files | matches the pinned hashes above |
| `python -m pytest tests/test_security_sql_static.py -q` | **10 passed**, exit 0 |
| `python -m pytest -q` (whole suite) | **330 passed, 6 skipped**, exit 0 — the progress line reads `....................ssssss` (330 dots + 6 skips); this environment suppresses the summary line, so the count was taken from the progress output |

Static analysis was performed with a throwaway script in `%TEMP%` (outside the repo) that split the files into statements on semicolons outside `$$` bodies, diffed the replacement policy predicate-by-predicate against the deployed one, and checked every referenced identifier against `crm/schema.sql`.

### 1.3 Two documentation hazards worth fixing outside this batch

1. **Two different `H-01…H-05` numbering schemes coexist in `reports/eryndex/`.** In `security-findings.md` L14-18, `H-01` is the suppression permission failure; in `security-closure-matrix.md` L47-51, `H-01` is an outreach-gate lexical-guard defect. This packet uses the **`security-findings.md`** numbering, which is what the migration's own comments use (e.g. `20261113_hardening_batch1.sql` L24, L92, L131, L179, L191). Anyone reading the two reports side by side will mis-map them.
2. **`migration-review-batch1.md` L90 says "9 passing now"** for the static file; it contains 10 tests (measured: 10 passed). Its L92 estimate "146 → ~195" is also stale — the suite is now 336 collected. Neither affects the migration; both affect whether a reader trusts the report.

### 1.4 The stale "8-digit prefix" hypothesis — disposed of

The hypothesis is that the CLI ignores these files because they carry 8-digit date prefixes (`20261113_`) rather than 14-digit versions (`YYYYMMDDHHMMSS`). **The mechanism is real; the framing as a defect is wrong.**

- `supabase/migrations/pending/` is **outside** the CLI's push path (`supabase/migrations/*.sql`). Confirmed by inspection: the applied directory holds exactly one file. So the CLI ignores these files because of their **directory**, not only their name.
- That is **deliberate and load-bearing**, and it is enforced by test: `tests/test_security_sql_static.py` L26-29 asserts `applied == [DEPLOYED]`, i.e. a file inside `supabase/migrations/` **means applied**. Renaming without applying would fabricate migration history — the repo would claim a change that never happened.
- Consequence for any apply route: it must **both** move+rename the file to a 14-digit version **and** execute the SQL **and** have the version recorded, in one sitting. Three routes and their version-recording requirement are in §8.3. `supabase-access-matrix.md` L237 states the same asymmetry ("Do both in one sitting, or neither"), and L245 already notes that neither `CREATE TYPE`/`CREATE TABLE`/`CREATE TRIGGER` is re-runnable, so a duplicate-apply attempt fails loudly rather than corrupting.

**Conclusion:** not a blocker, and not a property of this file. It is a process requirement that §8.3 and §9 pin down.

---

## 2. Operation-by-operation inventory

The file is **one transaction**: `BEGIN` at L21, `COMMIT` at L224. Ten semicolon-terminated statements — `BEGIN`, `COMMIT`, and **eight substantive DDL statements**, inventoried below in file order. There is no top-level DML: every `INSERT`/`UPDATE` in this file sits inside a `$$ … $$` function body (L46, L55, L75, L203, L217) and therefore does **not** execute during the migration.

| # | Line | Statement | Object | Table(s) | DDL/DML | Rewrites data? | Idempotent on re-run? |
|---|---|---|---|---|---|---|---|
| 1 | L38-65 | `CREATE OR REPLACE FUNCTION fn_sync_contact_suppression` | replaces function; becomes `SECURITY DEFINER` (L40) + pinned `search_path` (L41) | writes `suppression_registry` at run time only | DDL | No | **Yes** — `OR REPLACE`, attributes fully reset |
| 2 | L67-89 | `CREATE OR REPLACE FUNCTION fn_sync_company_suppression` | replaces function; `SECURITY DEFINER` (L69) + pinned `search_path` (L70) | writes `suppression_registry` at run time only | DDL | No | **Yes** |
| 3 | L104-123 | `CREATE OR REPLACE FUNCTION fn_enforce_agent_task_transitions` | **new** function | reads `tasks` at run time | DDL | No | **Yes** |
| 4 | L125-128 | `CREATE TRIGGER trg_enforce_agent_task_transitions … BEFORE UPDATE ON tasks` | new trigger | `tasks` | DDL | No | **NO** — no PostgreSQL version has `CREATE TRIGGER IF NOT EXISTS`; PG14+ offers `CREATE OR REPLACE TRIGGER`, which this file does not use. A re-run fails at L125 with `42710 duplicate_object` and the whole transaction aborts |
| 5 | L139 | `DROP POLICY IF EXISTS agent_insert_outreach_messages ON outreach_messages` | drops deployed policy | `outreach_messages` | DDL | No | **Yes** — no-op if absent |
| 6 | L141-176 | `CREATE POLICY agent_insert_outreach_messages … FOR INSERT TO authenticated` | replaces the policy dropped in #5 | `outreach_messages` | DDL | No | **Yes, but only because #5 precedes it**; standalone it would fail on a duplicate name |
| 7 | L186-188 | `CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_qualifications_approved … WHERE review_status = 'approved'` | new partial unique index | `lead_qualifications` | DDL | No — builds an index; **fails** if duplicate approved rows already exist | **Yes** (`IF NOT EXISTS`), with the caveat in §2.1 |
| 8 | L198-222 | `CREATE OR REPLACE FUNCTION fn_sync_company_primary_domain` | replaces function | writes `company_domains` at run time only | DDL | No | **Yes** |

**Net:** 8 substantive statements; 0 tables created, 0 dropped, 0 columns added or dropped, 0 grants or revokes, 0 rows read or written at apply time. Of the eight, seven are re-runnable and **one (#4) is not** — so the file as a whole is **single-shot**: a second run aborts at L125 and rolls the transaction back, leaving the database exactly as it was. That failure mode is safe but must not be mistaken for "harmless" — it means the file can never be used as a "make sure it's applied" re-run, and the post-checks in §9 are the only way to know it landed.

### 2.1 Two notes on statement #7

- `CREATE UNIQUE INDEX IF NOT EXISTS` matches on the **index name only**. If an index named `uq_lead_qualifications_approved` existed with a different definition, the statement would silently accept it and H-04 would not be closed. `tests/test_security_sql_static.py` L111 asserts the name is absent from the deployed schema, and `crm/schema.sql` L1695 shows only `idx_lead_qualifications_company` — so the name is free today. Verification check V4 (§9) re-reads the definition rather than trusting the name.
- Because the index is declared `WHERE review_status = 'approved'`, it does **not** constrain drafts. This matters: `integrations/supabase/qualification_repository.py` L167 posts a new draft row whenever the fingerprint changes (L161-165), so several drafts per company remain legal. H-04 constrains approval, not drafting.

---

## 3. Compatibility with the deployed Development schema

Every object the file references, with the line in `crm/schema.sql` that defines it. Signature/return-type agreement was checked, not assumed. **No mismatch found.**

### 3.1 Functions

| Reference in batch | Deployed definition | Deployed signature | What the batch assumes | Match? |
|---|---|---|---|---|
| `fn_sync_contact_suppression()` L38 | L861-887 | `RETURNS TRIGGER`, `LANGUAGE plpgsql`, INVOKER | return type only | ✅ |
| `fn_sync_company_suppression()` L67 | L894-909 | `RETURNS TRIGGER`, INVOKER | return type only | ✅ |
| `is_agent()` L107 | L83-91 | `RETURNS BOOLEAN`, `STABLE` | boolean in `IF NOT is_agent()` | ✅ |
| `is_agent_type('drafter')` L144 | L93-102 | `is_agent_type(p_type TEXT) RETURNS BOOLEAN` | one text arg → boolean | ✅ |
| `current_agent_identity()` L145, L157 | L104-112 | `RETURNS TEXT` | compared to `drafted_by_agent_identity TEXT` (L601) and `t.assigned_actor_identity TEXT` (L746) | ✅ TEXT/TEXT, no cast |
| `fn_enforce_task_governance()` L94-95 (comment) | L1494-1566 | `RETURNS TRIGGER` | must coexist on `tasks` | ✅ (see §7) |
| `fn_sync_company_qualification()` L181 (comment) | L1407-1424 | `RETURNS TRIGGER` | keeps running after H-04 | ✅ (see §7.4) |
| `update_updated_at_column()` — not touched | L18-24 | `RETURNS TRIGGER` | unaffected | ✅ |
| `is_identifier_suppressed(…)` — not touched | L930-1050 | `SECURITY DEFINER`, `search_path = pg_catalog, public` (L935-936) | H-01 does not alter it | ✅ |
| `fn_enforce_agent_task_transitions()` L104 | **absent** | — | created by this batch | ✅ expected-new |

### 3.2 Tables and columns

| Reference | Deployed definition | Required property | Present? |
|---|---|---|---|
| `suppression_registry` (INSERT, L46/L75) | L316-328 | `identifier TEXT NOT NULL UNIQUE` (L319) — required by `ON CONFLICT (identifier)` (L51) | ✅ |
| `suppression_registry.target_type` | L318 + enum L299-305 | `'email'`, `'domain'`, `'linkedin'` must exist | ✅ all three |
| `suppression_registry.source` | L321 | `TEXT NOT NULL`, **no enum, no CHECK** → both `'manual_blacklist'` and `'opt_out_request'` are legal | ✅ |
| `suppression_registry.created_by_actor_type` | L322 + enum L293-297 | `'system'` must exist | ✅ |
| `suppression_registry` chk | L327 | `identifier = LOWER(TRIM(identifier))` — the triggers apply `LOWER(TRIM(...))` (L49, L58, L78) | ✅ satisfies the constraint |
| `contacts.is_opted_out` / `email` / `linkedin_url` / `opt_out_reason` | L423, L416, L417, L425 | nullable `email`/`linkedin_url` guarded by `IS NOT NULL` (L45, L54) | ✅ |
| `companies.is_suppressed` / `suppression_reason` / `primary_domain` | L358, L359, L340 | `primary_domain NOT NULL UNIQUE` (L340) — H-05's conflict path depends on this | ✅ |
| `tasks` table + `status` | L738-769, `status` L744, enum L286-291 | `'pending'`, `'in_progress'`, `'completed'` used at L113-114 | ✅ all exist (also `'cancelled'`, which the whitelist deliberately excludes) |
| `lead_qualifications.company_id` / `review_status` / `qualification_tier` | L516, L532, L528 | `review_status='approved'` and tier `< > 'disqualified'` | ✅ |
| `qualification_tier_type` enum | L146-151 | `'disqualified'` must exist as a literal | ✅ L150 |
| `companies.lifecycle_stage` + enum | L357, L153-165 | `'qualified'`, `'outreach_queued'`, `'in_outreach'` must exist | ✅ L156, L158, L159 |
| `outreach_messages` columns used by H-03 | L596-646 | `drafted_by_agent_identity` L601, `approval_status` L608, `approved_by_human_id` L609, `approved_at` L610, `is_admin_override` L612, `override_reason` L613, `override_by_human_id` L614, `sending_status` L615, `proposed_commercial_model` L607, `company_id` L599, `contact_id` L600, `campaign_id` L598 | ✅ all present |
| `outreach_approval_status_type` / `outreach_sending_status_type` / `commercial_model_type` | L228-234, L236-247, L215-219 | `'draft'` in both statuses; `'strategic_revenue_sharing'` exists | ✅ |
| `company_domains(company_id, domain, is_primary)` | L390-398 | `domain TEXT NOT NULL UNIQUE` (L393) — required by `ON CONFLICT (domain)` (L205) | ✅ |
| `company_domains` format check | L397 | `domain ~* '^[a-z0-9.-]+\.[a-z]{2,}$'` — H-05 normalises with `LOWER(TRIM(...))` (L204) | ✅ consistent |

### 3.3 Pre-existing constraints the batch must not violate

| Constraint | Line | Why it matters to this batch | Verdict |
|---|---|---|---|
| `chk_outreach_mandatory_human_gate` | L635-638 | H-03 pins `sending_status = 'draft'`, so the constraint can never be reached from this policy | ✅ consistent (tightened) |
| `chk_outreach_no_revenue_sharing` | L630-632 | H-03 keeps `proposed_commercial_model != 'strategic_revenue_sharing'` (L153), duplicating the constraint — belt and braces, no conflict | ✅ |
| `fk_outreach_contact_company` | L626-627 | H-03's task join uses `(t.contact_id IS NULL OR t.contact_id = outreach_messages.contact_id)`; the composite FK already guarantees contact∈company | ✅ orthogonal |
| `chk_lead_qual_review_human_gate` | L557-560 | an `approved` row must carry `reviewed_by_human_id` + `reviewed_at`; H-03 does not re-check them because the constraint does | ✅ |
| `chk_companies_suppression_logic` | L369-372 | forces `suppression_reason NOT NULL` whenever `is_suppressed = TRUE` | ✅ — and it is why the `COALESCE` at L78 is dead code (§4.3) |
| `chk_companies_disqualified_stage` | L373-376 | a `disqualified` company may only sit in `identified`/`researching`/`disqualified`/`churned` — so H-03's lifecycle clause (L174) can never admit a disqualified company | ✅ redundant but coherent |
| `chk_task_completed_at_sync` | L765-768 | `completed_at` must be non-null iff `status='completed'`; `fn_enforce_task_governance` sets it (L1498-1504) | ✅ H-02 does not interfere (§7.2) |

### 3.4 RLS policies the batch depends on at evaluation time

H-03's `WITH CHECK` contains two sub-selects. Policy expressions evaluate **as the invoking role**, so RLS applies to those sub-selects. This is the one place where H-03 could have been self-defeating — a drafter might not be able to see the very rows the policy requires. It is not, and here is why:

| Sub-select | RLS policy that governs visibility | Why a drafter can satisfy it |
|---|---|---|
| `FROM tasks t` (L154-162) | `agent_select_assigned_tasks` L2046-2051 (`is_agent()` + own identity) | the sub-select filters on exactly the same predicates |
| `FROM lead_qualifications lq` (L165-170) | `agent_select_lead_qualifications` L1953-1974; drafter branch L1966-1972 requires `review_status='approved'` **and** an active task on that company | the policy asks for `review_status='approved'` (L168) and the task clause (L154) is already satisfied |
| `FROM companies c` (L171-175) | `agent_select_companies` L1810-1821 (`is_agent()` + active task on that company + `is_suppressed = FALSE`) | `is_agent_type` is **not** restricted here — any agent type with an active task sees the row, so a `drafter` qualifies; a suppressed company yields no row, so the policy fails closed |

**No mismatch, no blocker.** One consequence worth recording: because `agent_select_companies` L1813 requires `is_suppressed = FALSE`, a suppressed company silently fails the lifecycle clause rather than raising — fail-closed, which is the right direction.

### 3.5 Observations that are not blockers

| # | Observation | Evidence | Why it is not a blocker |
|---|---|---|---|
| O-1 | H-01's company trigger keeps `'manual_blacklist'` but changes the **fallback reason** from `'Company suppression/blacklist'` to `'Company suppression'` | deployed L902 vs batch L78 | unreachable: `chk_companies_suppression_logic` (L369-372) guarantees `suppression_reason IS NOT NULL` whenever `is_suppressed = TRUE`, and the trigger only inserts in that branch (L73). `COALESCE` never falls through. See §4.3 |
| O-2 | The new `fn_enforce_agent_task_transitions` (L104) is created **without** the `REVOKE ALL … FROM PUBLIC, anon, authenticated` that the schema applies to every other trigger function (L2074-2076). Postgres grants `EXECUTE` to `PUBLIC` on new functions by default, and that tail block does not re-run. | L2071-2077 vs batch L104-123 | not exploitable: it is a `RETURNS TRIGGER` function with no `SECURITY DEFINER`; direct calls raise `0A000 trigger functions can only be called as triggers`. It is a least-privilege deviation from the file's own convention, not a hole. Optional one-line follow-up in §12 |
| O-3 | H-01 pins `search_path = public, pg_temp` while the schema's own convention is `pg_catalog, public` with explicit `public.` qualification (L936, L1614, comment L1608-1609) | batch L41, L70 vs schema L936 | safe: `pg_catalog` is always searched first implicitly, and `pg_temp` is last (the hijack requires it first). The only relation touched is `suppression_registry`, which lives in `public` — the schema itself treats `public` as trusted for unqualified references throughout its policies. Stronger would be `public.suppression_registry`; not required |
| O-4 | H-02's replacement whitelist narrows agents to two transitions; the deployed governance trigger allowed `in_progress → pending` and `in_progress → cancelled` | schema L1494-1516 has no such block; batch L112-119 | intended (the finding), and no caller relies on the removed transitions: `agents/researcher/agent.py` L60 writes `in_progress`, L196 writes `completed`, and no Python in `agents/`, `orchestration/`, `outreach/` writes `cancelled` (grep, 2026-11-14) |

---

## 4. The `suppression_registry.source` correction, re-checked independently

This was re-derived from `crm/schema.sql` directly, without relying on the migration's comment at L79-82 or on the prior review.

### 4.1 What each side actually writes

| Writer | Identifier written | `source` | `reason` fallback | `created_by_*` | Line |
|---|---|---|---|---|---|
| **Deployed** contact trigger | `'email'` — `LOWER(TRIM(NEW.email))` | `'opt_out_request'` | `'Contact opt-out request'` | `'system'`, `'trg_contacts_suppression_sync'` | `crm/schema.sql` L867-872 |
| **Deployed** contact trigger | `'linkedin'` — `LOWER(TRIM(NEW.linkedin_url))` | `'opt_out_request'` | `'Contact opt-out request'` | `'system'`, `'trg_contacts_suppression_sync'` | L877-882 |
| **Deployed** company trigger | `'domain'` — `LOWER(TRIM(NEW.primary_domain))` | **`'manual_blacklist'`** | `'Company suppression/blacklist'` | `'system'`, `'trg_companies_suppression_sync'` | L899-903 |
| **Batch** contact trigger | same two | `'opt_out_request'` (unchanged) | same | same | batch L46-51, L55-60 |
| **Batch** company trigger | same | **`'manual_blacklist'`** (repaired) | `'Company suppression'` (text drift, unreachable — O-1) | `'system'`, `'trg_companies_suppression_sync'` | batch L75-84 |

The schema's own comment enumerates the legal vocabulary: `'opt_out_request', 'gdpr_removal', 'bounced_permanent', 'manual_blacklist'` (`crm/schema.sql` L321). The column is untyped `TEXT NOT NULL` with **no CHECK**, so nothing in the database would have rejected `'opt_out_request'` on a company row — the wrong value would have been written silently and permanently (L822-835 forbid deleting a suppression row).

### 4.2 Why the contact value is **not** also changed

A contact opt-out genuinely *is* an inbound opt-out request: the row is created because `NEW.is_opted_out = TRUE` (batch L44, deployed L864) and the reason comes from `contacts.opt_out_reason` (L49). `'opt_out_request'` is the accurate source for that event, and it is what the deployed schema records (L871).

A company suppression is categorically different: the row is created because an **operator** set `companies.is_suppressed = TRUE` with a `suppression_reason` (batch L73, deployed L897). No third party asked to be removed. Labelling it `'opt_out_request'` would assert, in a permanent compliance ledger, that the subject made a request they never made — and would make `manual_blacklist` unreachable as a value for `target_type='domain'`, since the company trigger is the only writer of domain rows.

So the asymmetry is correct in both directions: **contact → `opt_out_request`** (unchanged), **company → `manual_blacklist`** (restored). The correction changes the permission model and nothing about what the ledger records.

### 4.3 Why the remaining textual difference is provably dead code

The batch writes `COALESCE(NEW.suppression_reason, 'Company suppression')` (L78) where the deployed function wrote `'Company suppression/blacklist'` (L902). This is reachable only when `NEW.suppression_reason IS NULL` while `NEW.is_suppressed = TRUE`. `chk_companies_suppression_logic` (`crm/schema.sql` L369-372) rejects exactly that combination:

```
(is_suppressed = FALSE AND suppression_reason IS NULL AND suppression_date IS NULL) OR
(is_suppressed = TRUE  AND suppression_reason IS NOT NULL AND suppression_date IS NOT NULL)
```

A CHECK constraint is evaluated before the `AFTER` trigger fires (L911-914), so by the time this branch runs, `suppression_reason` is non-null and `COALESCE` always yields it. The fallback string can never reach the ledger. **No behavioural delta; no blocker.** (If a future migration relaxes that CHECK, the fallback text becomes live and should be aligned — worth a comment, not a change now.)

### 4.4 What the batch does **not** do to the ledger

| Property | Status |
|---|---|
| Grants `INSERT` on `suppression_registry` to `authenticated` | **No.** The file contains zero `GRANT`/`REVOKE` statements (grep; the only matches at L26/L28 are prose). Re-verified mechanically. |
| Writes any suppression row at apply time | No — the `INSERT`s are inside `$$` bodies (L46, L75) |
| Rewrites or deletes existing suppression rows | No — no top-level DML, and `trg_enforce_suppression_immutability` (L832-835) forbids deletes anyway |
| Widens any policy on `suppression_registry` | No — `human_operator_all_suppression` (L1760-1761) is untouched |

Fixing the permission failure by granting `INSERT` would have been worse than the bug: `human_operator_all_suppression` (L1760-1761) is `FOR ALL TO authenticated` with `WITH CHECK (is_human_operator())`, so a table-level `INSERT` grant would have let any operator forge arbitrary ledger rows with any `source`, `reason`, and `created_by_actor_identity`, bypassing the trigger's fixed logic entirely. `SECURITY DEFINER` keeps the write path narrow: the only way to add a row is through the two functions' hard-coded column set.

---

## 5. The two headline defects, mechanically

### 5.1 H-01 — why the opt-out path raised `42501`

Postgres checks **two independent gates** for an `INSERT`, in this order: (a) table-level privilege (`GRANT`), then (b) RLS. H-01 failed at gate (a).

| Step | Detail | Evidence |
|---|---|---|
| 1 | The deployed trigger functions are **SECURITY INVOKER** — the default, and no `SECURITY` clause is present | `crm/schema.sql` L861-863, L894-896 |
| 2 | `SECURITY INVOKER` means the body runs with the **caller's** privileges. The caller is an operator holding the `authenticated` role. | Postgres semantics; the function's own comment block L857-860 does not claim otherwise |
| 3 | The schema revokes everything, then grants back a **read-only** surface on the ledger | `REVOKE ALL ON ALL TABLES … FROM anon, authenticated, public` L2070; `GRANT SELECT ON suppression_registry, audit_logs TO authenticated` L2086 |
| 4 | So `authenticated` holds `SELECT` and nothing else on `suppression_registry`. The `INSERT` at L867 (deployed) is attempted as `authenticated` → **`42501 permission denied for table suppression_registry`** | privilege check precedes RLS, so this fires before any policy is consulted |
| 5 | The RLS layer would in fact have **allowed** it: `human_operator_all_suppression` is `FOR ALL TO authenticated USING (is_human_operator()) WITH CHECK (is_human_operator())` | L1760-1761 — so the operator's intent was legitimate; only the grant was missing |
| 6 | **Consequence:** the documented operator action "set `contacts.is_opted_out = TRUE`" could never succeed. `chk_contacts_opt_out_logic` (L432-435) forces `opt_out_at` to be set with it, so the UPDATE is well-formed and passes every constraint, then dies on the trigger's `INSERT`. The suppression ledger — the compliance control the whole outreach gate depends on (`fn_enforce_outreach_human_approval` L1246-1253 refuses dispatch on any hit) — could not receive new entries from the only role allowed to create them. | |

**The fix:** `SECURITY DEFINER` (L40, L69) makes the body run as the function **owner**. The owner is the role that applied the migration, which owns (or is superuser over) `suppression_registry`, so gate (a) passes and — because table owners bypass RLS when `FORCE ROW LEVEL SECURITY` is not set (grep: **no `FORCE ROW LEVEL SECURITY` anywhere in `crm/schema.sql`**) — gate (b) passes too. `SET search_path = public, pg_temp` (L41, L70) closes the classic definer hole by pinning resolution and putting `pg_temp` last.

Two supporting facts that make the fix complete rather than partial:

- Trigger functions need **no** `EXECUTE` grant to fire; Postgres does not check `EXECUTE` for trigger invocation. So leaving `EXECUTE` revoked (L2071) does not break the trigger, and it keeps the function uncallable by clients. That is the desired narrow surface.
- The nested audit write still works: `trg_audit_suppression` (L1673-1675) fires on every `INSERT` into the ledger, and `fn_audit_log_capture` is itself `SECURITY DEFINER` with a pinned path (L1613-1614) — which it must be, since `audit_logs` is also `SELECT`-only for `authenticated` (L2086). So H-01 does not need a second fix for the audit trail; it inherits the definer context.

**Residual assumption to verify on the day (V1a in §9):** `SECURITY DEFINER` only helps if the function's owner actually owns the table. If the file were applied by a low-privilege role, the definer context would reproduce the same `42501` from a different angle. Check `pg_proc.proowner` = `pg_class.relowner` for `suppression_registry` before trusting the fix.

### 5.2 H-03 — how the replacement policy closes the missing qualification precondition

**The gap.** The deployed `agent_insert_outreach_messages` (`crm/schema.sql` L1977-1999) required: the caller is an agent of type `drafter`; the row is a draft with no approval metadata; no override; no revenue-sharing model; and an active task on the same company/contact/campaign. It never asked whether the company had been **qualified at all**. A drafter holding a task could therefore write outreach for a company that had never been researched, scored, or approved — the human gate downstream (`chk_outreach_mandatory_human_gate` L635-638, `fn_enforce_outreach_human_approval` L1176-1220) only governs *sending*, not *drafting*, so nothing in the database stopped the draft from existing. `agent-contracts.md` L457 states the precondition; the DB did not.

**What the replacement adds** (batch L141-176), clause by clause, with the enum values each depends on:

| # | Clause | Batch line | Predicate evaluated | Legal values it relies on | Source |
|---|---|---|---|---|---|
| 1 | **Approved, non-disqualified scorecard exists** | L165-170 | `lq.company_id = outreach_messages.company_id AND lq.review_status = 'approved' AND lq.qualification_tier <> 'disqualified'` | `review_status ∈ {draft, pending_human_review, approved, rejected}`; `qualification_tier ∈ {tier_1_priority, tier_2_prospective, tier_3_deferred, disqualified}` | enums L208-213, L146-151 |
| 2 | **Company has reached a stage where outreach is legitimate** | L171-175 | `c.id = outreach_messages.company_id AND c.lifecycle_stage IN ('qualified', 'outreach_queued', 'in_outreach')` | those three of the eleven stages | enum L153-165 (L156, L158, L159) |
| 3 | Role gate (unchanged) | L143-144 | `is_agent() AND is_agent_type('drafter')` — i.e. `app_metadata.user_role='agent'` **and** `app_metadata.subagent_type='drafter'` | `is_agent` L83-91, `is_agent_type` L93-102 | |
| 4 | Identity binding (unchanged) | L145 | `drafted_by_agent_identity = current_agent_identity()` (falls back to JWT `sub`) | L104-112 | |
| 5 | Draft-only state (unchanged) | L146-152 | `approval_status='draft'`, `approved_by_human_id IS NULL`, `approved_at IS NULL`, `is_admin_override=FALSE`, `override_reason IS NULL`, `override_by_human_id IS NULL`, `sending_status='draft'` | L228-234, L236-247 | |
| 6 | No revenue sharing (unchanged) | L153 | `proposed_commercial_model != 'strategic_revenue_sharing'` | L215-219 | |
| 7 | Active task scope (unchanged) | L154-162 | agent task on same company, contact (`IS NULL OR =`), campaign (`IS NULL OR =`), `status IN ('pending','in_progress')` | L286-291 | |

**Nothing was removed.** A predicate-by-predicate diff of the deployed policy against the replacement (mechanical, splitting on `AND`) shows all 12 deployed atoms present verbatim; the only differences are the three added clauses in rows 1-2 (plus a parenthesis that moved). H-03 is strictly narrower.

**Interaction with H-04, and with the role model:**

- **H-04 makes row 1 unambiguous.** Because `uq_lead_qualifications_approved` (L186-188) permits at most one `approved` row per company, the `EXISTS` cannot be satisfied by a stale approval while a newer one is pending. Without H-04 the policy would still be *sound* (any approved row is enough) but the company's tier could drift; with it, "approved" and "the effective approval" are the same row. The two fixes are independent but mutually reinforcing — the batch does not need H-04 to precede H-03, because the policy only reads at evaluation time.
- **Operators are unaffected.** The policy is `FOR INSERT TO authenticated` and begins with `is_agent()` (L143), so for a human operator it evaluates false; permissive policies are OR-ed, and `human_operator_all_outreach_messages` (L1784-1785) still admits the operator's insert. No human workflow loses access.
- **No existing row is invalidated.** Replacing an `INSERT` policy has no retroactive effect — unlike a `CHECK` constraint there is no `VALIDATE` step and no scan of existing rows. Combined with the baseline (`outreach_messages` = **0 rows**, `phase-0-baseline.md` L70), there is nothing to migrate.
- **What the policy still does not check:** that the *qualification's* tier matches the *company's* `qualification_tier`, or that the scorecard's approval is the one `fn_sync_company_qualification` (L1407-1424) propagated. In practice the company row is only set from an approved scorecard (L1410), and criterion 2's lifecycle test plus `chk_companies_disqualified_stage` (L373-376) keep a disqualified company out. This is a defence-in-depth residual, not a hole this batch opened.

**Cost:** each inserting agent now evaluates three `EXISTS` sub-queries, two of them RLS-filtered (each adding its own task lookup) — roughly five correlated `tasks` probes per insert against tables holding 1 task and 1 company today (`phase-0-baseline.md` L60, L63). Irrelevant at this size; worth remembering if bulletin volume grows.

---

## 6. Lock, data, and partial-application risk

Lock levels below are PostgreSQL's documented behaviour for each statement class. They were **not measured against a live server**, because no SQL was executed (§1.2).

### 6.1 Locks taken by the eight statements

| # | Statement | Locked object | Lock mode | What it blocks |
|---|---|---|---|---|
| 1, 2, 3, 8 | `CREATE OR REPLACE FUNCTION` (L38, L67, L104, L198) | `pg_proc` row only | row-level catalog lock | nothing at table level; concurrent sessions keep using the old function body until `COMMIT` |
| 4 | `CREATE TRIGGER … ON tasks` (L125) | `tasks` | **SHARE ROW EXCLUSIVE** | `INSERT`/`UPDATE`/`DELETE` on `tasks`; concurrent DDL on `tasks` |
| 5 | `DROP POLICY` (L139) | `outreach_messages` | **ACCESS EXCLUSIVE** | all access including `SELECT` (see 6.2) |
| 6 | `CREATE POLICY` (L141) | `outreach_messages` | **ACCESS EXCLUSIVE** (see 6.2) | same |
| 7 | `CREATE UNIQUE INDEX` (L186), non-concurrent | `lead_qualifications` | **SHARE** | `INSERT`/`UPDATE`/`DELETE`; `SELECT` still allowed |

**CORRECTED (QA audit, 2026-11-14).** The first version of this table said `DROP POLICY` / `CREATE POLICY` take `SHARE ROW EXCLUSIVE`. They take `ACCESS EXCLUSIVE` (`src/backend/commands/policy.c`: `CreatePolicy`, `RemovePolicyById`), which blocks **reads** too — so a concurrent `SELECT` on `outreach_messages` makes the migration wait, and under `lock_timeout` aborts it. `CREATE TRIGGER` (L125, `SHARE ROW EXCLUSIVE`) and the non-concurrent `CREATE UNIQUE INDEX` (L186, `SHARE`) were right. The one `ACCESS EXCLUSIVE` in the discovery file (`ALTER TABLE … ENABLE ROW LEVEL SECURITY`, L92-93) is harmless: those tables are created in the same transaction and nothing can be reading them.

All locks are held **to `COMMIT`** (L224), so the practical window is one transaction, not eight statements. Because `CREATE UNIQUE INDEX` is not `CONCURRENTLY`, it *must* be inside the transaction — which is also why the whole batch is atomic. The index build scans `lead_qualifications`, which held **0 rows** at baseline (`phase-0-baseline.md` L68); at that size the build is microseconds and the `SHARE` window is not worth engineering around. This is the one measurement that should be re-taken before applying (P2, §9) — if the table has grown to a size where a `SHARE` lock would be felt, the fix is to schedule the batch, not to split it.

### 6.2 Data rewrite / scan

| Question | Answer | Evidence |
|---|---|---|
| Does any statement rewrite rows? | **No.** Zero top-level `INSERT`/`UPDATE`/`DELETE`. No `ALTER TABLE … TYPE`, no backfill. | statement inventory §2; `tests/test_security_sql_static.py` L100-103 forbids `delete from`, `truncate`, `drop table`, `drop column`, `update companies`, `update contacts` |
| Does any statement scan a business table? | **One** — the index build reads `lead_qualifications` (§6.1 row 7). Everything else is catalog-only. | |
| Does any statement change a column's type or drop anything? | **No** — 0 tables created/dropped, 0 columns added/dropped, 0 grants/revokes | grep for `GRANT`/`REVOKE`/`DROP TABLE`/`DROP COLUMN` returns nothing but prose at L26/L28 |
| Does any statement change behaviour for rows that already exist? | Only forward-looking: the index constrains *future* approvals; the policy constrains *future* inserts; the functions take effect on the next trigger firing; H-05 raises on *future* conflicted domain writes. | |

### 6.3 Partial application

**Not possible at statement granularity.** All eight statements are transactional DDL and the file opens its own `BEGIN` (L21) and closes with `COMMIT` (L224). If any statement fails, PostgreSQL rolls back the transaction; the `COMMIT` is then executed as `ROLLBACK` and nothing is left behind — no half-applied trigger, no index without its policy, no definer function without its sibling. `tests/test_security_sql_static.py` L32-37 pins the single `BEGIN`/single `COMMIT` shape so a future edit cannot quietly split it.

**Partial application *is* possible at route granularity**, and this is the real risk:

| Failure | Result | Mitigation |
|---|---|---|
| The SQL commits, but the file is never renamed into `supabase/migrations/` with its version | Database is fixed; repo says otherwise; the next `db push` sees nothing to do (fine) — but the repo's "pending" state is now a lie in the safe direction | record the version per §8.3 in the same sitting |
| The file is renamed but the SQL never runs | **The dangerous direction:** `migrations/` = applied, so the repo claims a security fix that does not exist, and `db push` will later try to re-apply and fail at `CREATE TRIGGER` (L125) | never rename before the post-checks (§9 V1-V9) pass; `tests/test_security_sql_static.py` L26-29 exists to catch this drift |
| A client auto-commits each statement separately (some GUI clients do) | The explicit `BEGIN` still opens an explicit transaction, so atomicity normally holds; a client that issues its own `COMMIT` between statements would break it | use a route with abort-on-error: Dashboard SQL editor, or `psql -v ON_ERROR_STOP=1` |
| Connection drops mid-transaction | Server rolls back; nothing applied | retry the **whole** file, never a fragment (`supabase-access-matrix.md` L258 records 75% pooler reliability, so this is not hypothetical) |

### 6.4 Recommended lock/session settings

Because the file opens its own transaction, `SET LOCAL` cannot be placed before `BEGIN`. Set the GUCs at **session** level before running the file; they apply inside its transaction:

```sql
SET lock_timeout = '5s';        -- fail fast instead of queueing behind a long transaction
SET statement_timeout = '60s';  -- bound the index build and any catalog wait
-- then execute 20261113_hardening_batch1.sql verbatim, then COMMIT;
```

Rationale: with `lock_timeout = 5s`, a competing transaction on `tasks` or `outreach_messages` causes a clean abort-and-rollback rather than a silent queue behind an idle-in-transaction session, and the retry is cheap because nothing was applied. Do not raise `lock_timeout` to "wait it out": on a 1-company database, the correct response to a lock conflict is to find the blocking session, not to wait. `statement_timeout` should stay generous enough for the index build (60 s is ~5 orders of magnitude of headroom at today's row count).

---

## 7. Trigger interaction analysis

### 7.1 Every trigger on the five affected tables, with fire order

Postgres fires same-timing triggers in **name order, ASCII-alphabetical** (`pg_trigger.tgname`). Order below is the actual firing order, derived from the names in `crm/schema.sql` and the one added name in the batch.

**`tasks`** (batch adds one `BEFORE UPDATE`):

| Order | Trigger | Timing / events | Line | Effect |
|---|---|---|---|---|
| 1 | `trg_enforce_agent_task_transitions` **(new)** | BEFORE UPDATE | batch L125-128 | agent whitelist: `pending→in_progress`, `in_progress→completed`; anything else raises |
| 2 | `trg_enforce_task_governance` | BEFORE UPDATE | L1568-1571 | sets/clears `completed_at` (L1498-1504); blocks agents editing completed/cancelled rows (L1509-1516) |
| 3 | `trg_tasks_updated_at` | BEFORE UPDATE | L773-776 | stamps `updated_at` |

`trg_enforce_…` < `trg_tasks_…` because `e` < `t`, so the two guards run before the timestamp stamp — irrelevant, since none of them inspect `updated_at`.

**`companies`** (batch replaces one function body, adds no trigger):

| Order | Trigger | Timing / events | Line |
|---|---|---|---|
| 1 | `trg_enforce_company_lifecycle` | BEFORE UPDATE OF `lifecycle_stage` | L1470-1473 |
| 2 | `trg_companies_updated_at` | BEFORE UPDATE | L381-384 |
| 3 | `trg_sync_company_primary_domain` | AFTER INSERT OR UPDATE OF `primary_domain` | L852-855 (body replaced by L198-222) |
| 4 | `trg_sync_company_suppression` | AFTER INSERT OR UPDATE OF `is_suppressed, primary_domain` | L911-914 (body replaced by L67-89) |

`p` < `s`, so domain sync precedes suppression sync. Both write **different** tables (`company_domains`, `suppression_registry`) and neither reads the other's target, so their order is immaterial.

**`contacts`**: `trg_contacts_updated_at` (BEFORE, L444-447) then `trg_sync_contact_suppression` (AFTER INSERT OR UPDATE OF `is_opted_out, email, linkedin_url`, L889-892, body replaced by L38-65).

**`outreach_messages`**: `trg_enforce_outreach_approval` (BEFORE INSERT OR UPDATE, L1260-1263) then `trg_outreach_messages_updated_at` (BEFORE UPDATE, L650-653) — `enforce` < `outreach` — then `trg_audit_outreach_messages` (AFTER INSERT OR UPDATE OR DELETE, L1665-1667).

**`lead_qualifications`**: `trg_enforce_lead_qual_rules` (BEFORE INSERT OR UPDATE, L1399-1402), `trg_lead_qualifications_updated_at` (BEFORE UPDATE, L565-568), `trg_audit_lead_qualifications` (AFTER, L1669-1671), then `trg_sync_lead_qualification_to_company` (AFTER INSERT OR UPDATE, L1426-1429) — `audit` < `sync`.

**`suppression_registry`**: `trg_enforce_suppression_immutability` (BEFORE DELETE, L832-835), `trg_audit_suppression` (AFTER INSERT OR UPDATE, L1673-1675).

### 7.2 Does H-02 conflict with the existing task guard?

No, and the interaction is benign in the one case worth checking:

- Agent `pending → completed`: **new trigger fires first** (order 1) and raises `Lifecycle Violation` before the governance trigger can auto-fill `completed_at`. Previously the governance trigger raised its own message (L1514-1516). Both refuse; only the message changes.
- Agent `in_progress → completed`: new trigger allows; governance then sets `NEW.completed_at = clock_timestamp()` (L1498-1501), satisfying `chk_task_completed_at_sync` (L765-768). **Order matters here and it is correct** — H-02 does not inspect `completed_at`, so running before the mutation is safe.
- Human operator: `is_agent()` false → new trigger returns immediately (L107-109); governance retains full authority. Unchanged.
- **Insert bypass?** H-02 is `BEFORE UPDATE` only. There is no agent `INSERT` policy on `tasks` — the only task policies for agents are `agent_select_assigned_tasks` (L2046-2051) and `agent_update_assigned_tasks` (L2053-2063); inserts are permitted only by `human_operator_all_tasks` (L1796-1797). So an agent cannot sidestep the whitelist by creating a task already `completed`.
- **Caller compatibility:** `agents/researcher/agent.py` L60 (`in_progress`) and L196 (`completed`) are exactly the two whitelisted transitions.

### 7.3 Can any pair deadlock, or double-write the ledger?

| Pair | Shared resource | Deadlock? | Double-write? |
|---|---|---|---|
| H-02 guard ↔ `fn_enforce_task_governance` | same `tasks` row, BEFORE UPDATE, same transaction | **No** — same row, same transaction, no second lock | n/a (both only mutate `NEW`) |
| `trg_sync_company_primary_domain` ↔ `trg_sync_company_suppression` | both fire on `UPDATE OF primary_domain` on `companies` | **No** — lock order is always `companies` → `company_domains` → `suppression_registry`, in that fixed alphabetical order, in every session | No: they write disjoint tables and distinct identifier kinds (`domain` vs `domain` — see below) |
| `fn_sync_company_qualification` (L1407-1424) ↔ `trg_enforce_company_lifecycle` (L1470) | `lead_qualifications` → `companies`, one direction | **No** — the edge is always qualifications→companies; no path goes back | No |
| H-01 triggers ↔ `trg_audit_suppression` | `contacts`/`companies` → `suppression_registry` → `audit_logs`, one direction | **No** | No — the audit trigger only inserts into `audit_logs` |
| `trg_sync_company_primary_domain` (H-05 body) ↔ `trg_sync_company_suppression` (H-01 body), both on the same `companies` UPDATE | `suppression_registry` row for identifier `= LOWER(primary_domain)` | No | **Cannot double-write** the same identifier: the domain-sync path writes `company_domains`, the suppression path writes `suppression_registry`. Two different tables, two different identifier spaces. |

**No new lock cycle is introduced.** H-01 does make the `companies → suppression_registry` edge actually *execute* for the first time (it previously aborted at the privilege check), but it is still a single direction, and the same order holds for every session that touches those tables, which is what prevents deadlock.

### 7.4 The `ON CONFLICT (identifier) DO NOTHING` consequence — the audit semantic worth understanding

`suppression_registry.identifier` is **globally unique across `target_type`** (`crm/schema.sql` L319 — `identifier TEXT NOT NULL UNIQUE`, not `UNIQUE(target_type, identifier)`). All three writers use `ON CONFLICT (identifier) DO NOTHING` (batch L51, L60, L84; deployed L872, L882, L904).

Consequences, stated precisely:

1. **First writer wins, permanently.** If a domain was first suppressed as an inbound opt-out (hypothetically, `source='opt_out_request'`), a later operator blacklist of the same domain inserts nothing: `source`, `reason`, `created_by_actor_identity` and `suppressed_at` remain from the first write. The batch does **not** change this and must not be read as making `source` authoritative — it makes the *permission* correct for new rows only.
2. **Re-suppression after a change is a silent no-op.** The triggers re-fire when `is_opted_out` / `is_suppressed` / the identifier changes (batch L45, L54, L74 vs deployed L866, L876, L898), but `DO NOTHING` means a second attempt to record a *different* reason for the same identifier is discarded. That is the intended immutability semantic (L822-835 block deletes) rather than a defect, but an operator expecting the ledger to reflect the newest justification will be surprised.
3. **`target_type` is not part of the conflict key**, so an identifier that is both a plausible email and a plausible handle (`e.g.` a LinkedIn slug) can only ever exist once. Not reachable today — the writers normalise to `email`/`linkedin`/`domain`, and `chk_suppression_identifier_clean` (L327) forces lowercase/trimmed — but it is a schema-level sharp edge, not something this batch created.
4. **`DO NOTHING` is what keeps H-01 safe to re-fire**: the trigger runs on every relevant `UPDATE` (L889-892), so without the conflict clause a re-save of an opted-out contact would raise a unique violation on an ordinary operator action. Removing it later would be a regression.

**Pre-flight therefore includes reading the one existing ledger row's `source`** (P5, §9): baseline records `suppression_registry` = **1 row** (`phase-0-baseline.md` L69), and the evidence snapshot stores only the count, by design — the value must be read on the day.

---

## 8. Rollback and recovery

### 8.1 Exact rollback statements, per section

Rollback is **schema-only** and requires no data restore. Each section is independent, as the file's own header claims (L15-18).

| Fix | Rollback operation | Notes |
|---|---|---|
| **H-01** | Re-execute the deployed definitions verbatim: `crm/schema.sql` L861-887 and L894-909. `CREATE OR REPLACE FUNCTION` replaces the **whole** definition, including the `SECURITY DEFINER` attribute and the `SET search_path` clause — so replaying the deployed text reverts both, it is not enough to "add `SECURITY INVOKER`". | Restores the `42501` failure mode: opt-out writes break again. Any suppression row written while the fix was live **stays** — correctly, since the ledger is permanent (L822-835). Rollback must never delete it. |
| **H-02** | `DROP TRIGGER trg_enforce_agent_task_transitions ON tasks;` then optionally `DROP FUNCTION fn_enforce_agent_task_transitions();` | Order matters: dropping the function first fails on the trigger's dependency. Dropping the trigger alone leaves an unused function (O-2's ACL note applies). No data affected; tasks completed under the whitelist stay completed. |
| **H-03** | Re-execute `crm/schema.sql` L1977-1999 (plain `CREATE POLICY` fails if the policy exists, so precede with `DROP POLICY IF EXISTS agent_insert_outreach_messages ON outreach_messages;`). | Drafts written under the new policy are **not** removed — an `INSERT` policy has no retroactive effect. Those rows were legitimate at write time. |
| **H-04** | `DROP INDEX uq_lead_qualifications_approved;` (plain, inside a transaction) — or `DROP INDEX CONCURRENTLY IF EXISTS …` **outside** a transaction block. | `CONCURRENTLY` cannot run inside `BEGIN`/`COMMIT`; do not paste it into a transactional script. Dropping the index re-opens the two-approved-scorecards defect for future writes; existing rows are untouched. |
| **H-05** | Re-execute `crm/schema.sql` L841-850 | Re-enables silent re-parenting. If a legitimate domain move is needed **while H-05 is live**, the supported path is to delete the stale `company_domains` row first (DELETE is granted at L2083 and permitted by `human_operator_all_company_domains` L1766-1767), then update `companies.primary_domain`. |

### 8.2 What is left behind, and whether rollback is safe with post-application data

| Question | Answer |
|---|---|
| Does rollback leave orphan objects? | No. The batch creates no table, type, column, or grant. H-02 is the only new object, and its `DROP TRIGGER`/`DROP FUNCTION` pair removes it completely. |
| Does rollback destroy data? | No. Every rollback statement is DDL; no row is deleted or rewritten. The only destructive-sounding operation — dropping the unique index — destroys an *index*, not rows. |
| Is rollback safe **after** data has been written under the fix? | **Structurally yes, operationally no — it is a deliberate security regression.** Concretely: (a) suppression rows created through the fixed trigger persist and keep working, because `fn_sync_*` is only the *writer*; the *reader* `is_identifier_suppressed` (L930-1050) and the dispatch-time check (L1246-1253) are untouched, so suppression enforcement survives rollback; (b) outreach drafts created under H-03 persist; (c) tasks completed under H-02 persist; (d) the moment H-04's index is gone, a second scorecard can be approved again and the company's tier again depends on write order (L1407-1424). |
| Is rollback safe with **concurrent** writers? | Yes at the level that matters: all rollback statements except `DROP INDEX CONCURRENTLY` are transactional, and each section takes the same locks the forward migration did. The one caution is `DROP INDEX` (plain) briefly taking an `ACCESS EXCLUSIVE` lock on `lead_qualifications` — schedule it, don't fire it mid-write-burst. |
| Does rollback need a backup? | Not for schema reversal. A logical export is still recommended *before applying* (`backups/dev-snapshot-20261113T194803Z.json`, 22 rows across 14 tables, `phase-0-baseline.md` L172) because it is the only artefact that lets you answer "what did the ledger look like before" — the rollback cannot restore that, since nothing deletes it. |

### 8.3 Version recording — the part that makes rollback meaningful

A rollback that leaves the repo claiming the file was applied is worse than no rollback. Whichever route is used, the record must match reality:

| Route | Execution | Version recording |
|---|---|---|
| **A. CLI push** | `git mv supabase/migrations/pending/20261113_hardening_batch1.sql supabase/migrations/20261114090000_hardening_batch1.sql`, then `supabase db push --remote` | `db push` writes the version into `supabase_migrations.schema_migrations` itself. Push immediately after the `git mv` — never leave the tree in the renamed-but-unpushed state (`supabase-access-matrix.md` L237). If the push fails, `git mv` it back. |
| **B. Dashboard SQL editor** | Paste the file, Development project, run it | The editor records **nothing** in `schema_migrations`. Record it explicitly: either `supabase migration repair --status applied 20261114090000` (owner's CLI), or accept that the next `db push` will try to re-apply and fail at `CREATE TRIGGER` — which is why §2's non-idempotence matters. Rename the file **only after** V1-V9 pass. |
| **C. Manual `psql`** | `psql -v ON_ERROR_STOP=1 -f …` | Same as B: nothing is recorded automatically. |

`supabase-access-matrix.md` L245 states the same requirement for the dashboard route and already anticipates the `CREATE TYPE`/`CREATE TABLE`/`CREATE TRIGGER` re-run failure. On this machine a DSN does not exist (L178-183: no `DATABASE_URL`, no `ERYNDEX_DB_URL`, `pooler-url` has no password segment), so **route A is not available from here today** — which is fine, because the decision being requested is approval, not execution.

---

## 9. Post-application verification

Read-only throughout. Run **P1-P7 first** (pre-flight, before approval is acted on), then **V1-V9** after applying. Every query targets Development only; none writes.

### 9.1 Pre-flight (run before applying)

| # | Check | Query | Expected |
|---|---|---|---|
| **P1** | Wrong database | `select current_user, current_database();` | `current_user` must contain `vyfqmrdznmocnmzyjjcj`. If it contains `vqnamszywsuqjxuqseas`, **stop** — that is Production |
| **P2** | H-04 feet-gun: duplicate approvals would abort the index build | `select company_id, count(*) from lead_qualifications where review_status = 'approved' group by 1 having count(*) > 1;` | **0 rows.** Baseline: `lead_qualifications` = 0 rows total (`phase-0-baseline.md` L68), so this is expected-empty — but re-measure, because a non-empty result makes the whole batch roll back |
| **P3** | H-05 has not already re-parented a domain | `select cd.domain, cd.company_id, c.id as real_owner from company_domains cd join companies c on lower(btrim(c.primary_domain)) = cd.domain where cd.company_id <> c.id;` | **0 rows.** Baseline: 1 company, 1 domain row (L60-61). Any row here is pre-existing corruption that H-05 will merely stop extending |
| **P4** | H-03 would retroactively matter | `select count(*) from outreach_messages om where not exists (select 1 from lead_qualifications lq where lq.company_id = om.company_id and lq.review_status = 'approved' and lq.qualification_tier <> 'disqualified');` | **0.** Baseline: `outreach_messages` = 0 rows (L70) |
| **P5** | Ledger precedence (the `DO NOTHING` question, §7.4) | `select target_type, source, count(*) from suppression_registry group by 1,2;` | 1 row total at baseline (L69). **Record its `source`** — it determines what "first writer wins" will mean for that identifier forever |
| **P6** | Definitions-dependent fact: will `SECURITY DEFINER` actually have the privilege? | `select p.proname, p.proowner::regrole from pg_proc p where p.proname in ('fn_sync_contact_suppression','fn_sync_company_suppression');` and `select relowner::regrole from pg_class where relname = 'suppression_registry';` | the function owner and the table owner must be **the same role** (or a superuser). If not, H-01 will still raise `42501`, from inside the definer |
| **P7** | Recoverability | a fresh logical export exists (Dashboard backup, or a `pg_dump` with an owner DSN) | present and timestamped **after** P1. `backups/dev-snapshot-20261113T194803Z.json` is the pre-batch reference (L172), not a substitute for one taken on the day |

### 9.2 Post-apply — the five fix checks

| # | Fix | Query | Expected |
|---|---|---|---|
| **V1** | **H-01** attribute | `select proname, prosecdef, proconfig from pg_proc where proname in ('fn_sync_contact_suppression','fn_sync_company_suppression');` | two rows, `prosecdef = true`, `proconfig = {search_path=public, pg_temp}` |
| **V1a** | **H-01** functional (the defect itself) | as the operator identity: `update contacts set is_opted_out = true, opt_out_at = clock_timestamp() where id = '<a test contact id>';` then `select target_type, identifier, source from suppression_registry order by suppressed_at desc limit 2;` | the update **succeeds** (pre-fix it raised `42501`); one row `email`/`linkedin` with `source = 'opt_out_request'`. This is exactly `security-findings.md` L42. Roll the test row back, or use a disposable contact — the ledger row is permanent by design, so prefer a `BEGIN … ROLLBACK` wrapper |
| **V2** | **H-02** presence | `select tgname from pg_trigger where tgrelid = 'tasks'::regclass and not tgisinternal order by tgname;` | three rows: `trg_enforce_agent_task_transitions`, `trg_enforce_task_governance`, `trg_tasks_updated_at` (this order is also the firing order, §7.1) |
| **V2a** | **H-02** functional | as a researcher on an `in_progress` task: `update tasks set status = 'pending' where id = '<task>';` → then `update tasks set status = 'completed' where id = '<task>';` | first raises `Lifecycle Violation: agents may only move pending -> in_progress -> completed. Attempted in_progress -> pending`; second succeeds and sets `completed_at` (`security-findings.md` L43) |
| **V3** | **H-03** definition | `select with_check from pg_policies where schemaname = 'public' and tablename = 'outreach_messages' and policyname = 'agent_insert_outreach_messages';` (use `with_check`, **not** `qual` — the policy is `FOR INSERT`, so it has no `USING` and `qual` is `NULL`) | the expression contains `lead_qualifications`, `review_status = 'approved'`, `qualification_tier <> 'disqualified'`, and `lifecycle_stage = ANY (...)` with `qualified`, `outreach_queued`, `in_outreach` |
| **V3a** | **H-03** functional | as a drafter with an active task on a company whose only scorecard is `draft`: insert a minimal draft into `outreach_messages` → then have an operator approve the scorecard and retry the identical insert | first is rejected by RLS (`new row violates row-level security policy`); second succeeds (`security-findings.md` L44) |
| **V4** | **H-04** definition | `select indexdef from pg_indexes where schemaname = 'public' and indexname = 'uq_lead_qualifications_approved';` | `CREATE UNIQUE INDEX … ON public.lead_qualifications USING btree (company_id) WHERE (review_status = 'approved'::qualification_review_status_type)` — read the predicate, do **not** trust the name (§2.1) |
| **V4a** | **H-04** functional | with one `approved` row for company X, try to insert/approve a second | `duplicate key value violates unique constraint "uq_lead_qualifications_approved"` (`security-findings.md` L45) |
| **V5** | **H-05** definition | `select prosrc from pg_proc where proname = 'fn_sync_company_primary_domain';` | contains `ON CONFLICT (domain) DO NOTHING` and `Duplicate domain`; **must not** contain `SET company_id = EXCLUDED.company_id` (the deployed defect, `crm/schema.sql` L846-847) |
| **V5a** | **H-05** functional | set a second company's `primary_domain` to a domain already owned by another company in `company_domains` | raises `Duplicate domain: … is already registered to company …`; the existing `company_domains` row is unchanged (`security-findings.md` L46) |

### 9.3 Confirming the transaction fully applied

| # | Check | Query | Expected |
|---|---|---|---|
| **V6** | **All five, one statement** — the authoritative "did it commit" test | the single query in the block below this table (kept out of the cell so it stays copy-pasteable) | **six `true` values.** Any `false` = the batch did not commit; do not rename the file, and re-read §6.3 |
| **V7** | Version recorded | `select version, name from supabase_migrations.schema_migrations order by version desc limit 5;` | the 14-digit version assigned in §8.3 is present **iff** the file was moved. On the dashboard route with no repair, this will be absent — that is the gap §8.3 exists to close, and it must be fixed before the next `db push` |
| **V8** | Nothing collateral changed | `select count(*) from companies; select count(*) from contacts; select count(*) from lead_qualifications; select count(*) from outreach_messages;` | identical to baseline (1 / 0 / 0 / 0, `phase-0-baseline.md` L60-70) — the batch rewrites no rows, so any drift means something else ran |
| **V9** | Regression sweep | `python -m pytest -q` (local, no DB) and, once an owner DSN exists, `ERYNDEX_DB_URL=… python -m pytest tests/db -q` | 330 passed / 6 skipped locally (measured §1.2); the six DB tests go from `skipped` to executed, each named after its finding (`tests/db/test_security_fixes_db.py` L1-11) |

**V6 query** — six booleans; any `false` means the batch is not fully applied:

```sql
select
  (select prosecdef from pg_proc where proname = 'fn_sync_contact_suppression')            as h01_contact,
  (select prosecdef from pg_proc where proname = 'fn_sync_company_suppression')            as h01_company,
  (select count(*) = 1 from pg_trigger
     where tgname = 'trg_enforce_agent_task_transitions')                                  as h02,
  (select count(*) = 1 from pg_policies
     where policyname = 'agent_insert_outreach_messages'
       and with_check like '%lead_qualifications%')                                        as h03,
  (select count(*) = 1 from pg_indexes
     where indexname = 'uq_lead_qualifications_approved')                                  as h04,
  (select prosrc not like '%EXCLUDED.company_id%' from pg_proc
     where proname = 'fn_sync_company_primary_domain')                                     as h05;
```

---

## 10. `20261113_discovery_candidates.sql` — separate approval

**sha256** `d3c8544e5558d9eeb2a2282175e7828fc63f7085d0612f5ac35caaca44d9a71d`, 165 lines, 24 statements (22 substantive + `BEGIN` L23 / `COMMIT` L150).

### 10.1 Dependencies — all pre-existing, all satisfied

| Dependency | Used at | Deployed definition | Status |
|---|---|---|---|
| `pgcrypto` / `gen_random_uuid()` | L34, L72 | `CREATE EXTENSION IF NOT EXISTS "pgcrypto"` L15 | ✅ |
| `clock_timestamp()` | L40, L49, L50, L76 | built-in | ✅ |
| `update_updated_at_column()` | L83-86 | L18-24 | ✅ |
| `is_human_operator()` | L103, L105 | L63-71 | ✅ |
| `is_agent()` | L111, L122, L129, L141 | L83-91 | ✅ |
| `is_agent_type('discovery')` | L112, L123, L130, L142 | L93-102 (`subagent_type` claim) | ✅ function exists; the **value** `'discovery'` is new — it must match whatever the discovery agent's JWT carries, which is not yet exercised by any caller |
| `current_agent_identity()` | L113, L124, L134, L146 | L104-112 | ✅ |
| `companies(id)` | L48 (`REFERENCES … ON DELETE SET NULL`) | L336 | ✅ |
| Role `anon`, `authenticated` | L95-96, L98-105 | Supabase built-ins | ✅ |

No dependency on anything the hardening batch creates. **The two files are mutually independent.**

### 10.2 Additive-only confirmation

| Property | Evidence |
|---|---|
| Creates 1 enum, 2 tables, 6 indexes, 1 trigger, 8 policies — no drops, no alters to existing objects | statements at L25, L33, L64-69, L71, L81, L83, L92-93, L98-148 |
| Touches no existing table | every `ALTER`/`CREATE` targets `discovery_candidates` or `discovery_candidate_evidence`; the only reference to an existing table is the FK to `companies` (L48) |
| Widens no existing grant | `REVOKE ALL … FROM anon, authenticated` (L95) precedes `GRANT SELECT, INSERT, UPDATE, DELETE … TO authenticated` (L96), and both name only the two new tables |
| Changes no existing function or trigger | none referenced for replacement |
| Reversible without residue | rollback in §10.4 removes all six object classes |

**One design note, not a defect:** the operator policy is `FOR ALL … USING (is_human_operator()) WITH CHECK (is_human_operator())` (L102-103), so an operator may set `reviewed_by_human_id` to *any* UUID — there is no `= auth.uid()` binding, unlike the qualification gate (`crm/schema.sql` L1367) and the outreach gate (L1209). For a staging table feeding Human Gate 1 this is a weak audit trail rather than a hole (only operators can write it), but if the gate is to be defensible, the check belongs in a future migration, not in this review.

**Second note:** `uq_discovery_candidates_batch_domain` on `(batch_key, domain)` (L66-67) does not constrain rows where `batch_key IS NULL`, because `NULL` never equals `NULL` in a unique index. The partial pending-domain index (L64-65) still prevents a duplicate *pending* stage, so the exposure is limited to rejected/duplicate rows staged without a batch key.

### 10.3 Non-idempotence and ordering

| Question | Answer |
|---|---|
| Idempotent on re-run? | **No** — `CREATE TYPE` (L25), `CREATE TABLE` (L33, L71), `CREATE TRIGGER` (L83) and the unqualified `CREATE POLICY`/`CREATE INDEX` statements all fail on a second run. Same single-shot property as the hardening batch, same safe failure (transaction aborts, nothing applied) |
| Ordering versus the hardening batch? | **Any order.** No shared object, no shared table, no shared constraint, and neither file's objects are referenced by the other. If both are approved, apply them in separate transactions so a failure in one cannot roll back the other — and so the post-checks attribute cleanly |
| Ordering with respect to the *rename*? | 14-digit versions must sort in the intended order (`20261114090000` before `20261114090100`). Since they are independent, this matters only for bookkeeping, not safety |
| Does it change any behaviour of the applied schema? | No. It adds a staging area; `companies` remains system of record and is only referenced as an FK target |

### 10.4 Its own rollback

```sql
BEGIN;
DROP TABLE IF EXISTS discovery_candidate_evidence;  -- child first (FK, ON DELETE CASCADE at L73)
DROP TABLE IF EXISTS discovery_candidates;
DROP TYPE  IF EXISTS discovery_candidate_status;
COMMIT;
```
(as documented at L152-159). **What is left behind:** nothing — the trigger (L83) and all eight policies (L98-148) are dropped with their tables, and both indexes cascade with the table drops. **Data:** any staged candidates and their evidence are destroyed; export staged rows first if any exist (the rollback block's own note, L153). **Safety with post-application data:** the only inbound reference is `converted_company_id → companies(id)` (L48), which points *out*; no other object references these tables, so the drop cannot fail on a dependency — but any company created from an approved candidate (L161-164) survives the rollback, which is correct: the company is the system of record, the candidate is not.

---

## 11. Production isolation confirmation

Grep-level evidence, run 2026-11-14 over the files themselves and over everything they invoke.

| Check | Command | Result |
|---|---|---|
| Production ref in the hardening file | `grep -n "vqnamszywsuqjxuqseas" 20261113_hardening_batch1.sql` | **no match** |
| Production ref in the discovery file | same | **no match** |
| Production ref anywhere in `supabase/migrations/pending/` | `grep -rn` | **one match only**: `pending/README.md` L12, prose stating that Production is untouched. No executable reference |
| Production ref in the functions the batch invokes | `grep -n "vqnamszywsuqjxuqseas" crm/schema.sql` | **no match** |
| `service_role` anywhere in the two files | `grep -n "service_role"` on both | **no match** |
| `service_role` in the deployed schema they run against | `grep -n "service_role" crm/schema.sql` | **no match** (the schema has no service-role bypass path at all) |
| Elevated credentials required **at run time** by the triggers | inspection of both replaced bodies | none: no `auth.uid()` requirement, no external call, no `SECURITY DEFINER` on anything the batch *adds* except the two suppression triggers (L40, L69), which need only the table owner's own privilege — no secret, no key, no connection |
| Elevated credentials required **at apply time** | inspection | DDL only; the applying role must be the project owner (dashboard editor or `db push`). That is a privilege, not a secret — nothing in the files asks for a password, PAT, or key |
| Anything in the files that could reach another project | inspection | no `dblink`, no `postgres_fdw`, no `COPY … PROGRAM`, no dynamic `EXECUTE`, no URL. `grep -E "dblink|fdw|COPY |EXECUTE format"` → no match |
| Test-level guard, independent of this review | `tests/db/test_security_fixes_db.py` L33 (`PROD_REF` check → `pytest.fail`) and `contracts/environment.py` (allow-list) | the DB suite refuses to run if `ERYNDEX_DB_URL` names Production |

**Conclusion:** the two files are inert with respect to Production — they cannot address it, cannot authenticate to it, and name it nowhere. The only production reference in the `pending/` directory is a sentence asserting that Production was not touched.

---

## 12. Verdict

### 12.1 Verdict

> ## READY FOR OWNER APPROVAL

**No blocking defect was found.** The eight statements are individually compatible with the deployed schema (§3), jointly transactional (§6.3), non-destructive (§6.2), free of new lock cycles (§7.3), reversible (§8.1), and isolated from Production (§11). The `source` correction is right in both directions (§4), and the two headline fixes close the defects as claimed (§5) with no predicate silently dropped in the replacement policy (§5.2).

### 12.2 What the owner must decide

1. **Approve or decline the batch.** §12.1 says it is fit to apply; applying is the owner's decision, and on this machine it is also the only way it will ever be applied (no DSN exists — `supabase-access-matrix.md` L178-183).
2. **Choose the apply route and accept its version-recording consequence** (§8.3). Route A (`git mv` + `db push`) records automatically; route B/C (Dashboard, `psql`) records nothing and needs `supabase migration repair --status applied <version>` or the repo will lie. Decide before touching either file.
3. **Run P1-P7 first, and read P5's answer.** P2 is the one that can abort the batch; P5 is the one that decides what "first writer wins" means for the single existing ledger row. Both are cheap and neither can be run from here.
4. **Accept three behavioural changes in the operator's day-to-day**, all intentional. (a) Re-approving a company's scorecard is no longer a single insert — the existing approval must be un-approved (or reset to `draft`) before a second one can be approved, because H-04's index makes two approvals impossible (§2.1, `crm/schema.sql` L1346-1393 shows the reset path exists). (b) A duplicate primary domain now hard-fails instead of silently stealing the domain row, which requires an explicit `company_domains` cleanup instead of an accidental one (§8.1). (c) **Added after the QA audit:** H-03 restricts agent drafting to `lifecycle_stage IN ('qualified','outreach_queued','in_outreach')`, so an agent can no longer draft for a company at `active_conversation`, `negotiating`, `partner_active`, `partner_dormant` or `churned` — including sequence follow-ups that `sequence_step` and `parent_message_id` exist to support. Operators are unaffected. This was missing from the first version of this packet; if follow-up drafting inside an active conversation is required, H-03's predicate needs widening before it is applied, not after.
5. **Decide the two optional follow-ups** — neither blocks this batch and neither is in the pinned file: (a) `REVOKE ALL ON FUNCTION fn_enforce_agent_task_transitions() FROM PUBLIC, anon, authenticated;` to match the schema's own treatment of trigger functions (O-2); (b) align the unreachable `COALESCE` fallback text at L78 with the deployed `'Company suppression/blacklist'` (O-1). Both change the sha256, so both would be a *new* file and a *new* approval.
6. **Approve `20261113_discovery_candidates.sql` separately** (§10). It is independent of this batch, additive-only, and reversible; approving one is not approving the other.
7. **Fix the two documentation hazards in §1.3** (duplicate `H-01…H-05` numbering across reports; stale test counts in `migration-review-batch1.md`) so that the next reader does not mis-map a finding or mis-trust a number.
