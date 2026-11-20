# Directive 03.7 — Discovery Staging Migration Application

**Target:** Development `vyfqmrdznmocnmzyjjcj` — and nothing else.
**Starting HEAD:** `4fe6168cbbb56d5e82b9f9a2f1cf961a4dc1a712` (clean tree)
**Migration sha256:** `385d79c7c1028d0eabf2c40176bcb775bb35faab1b6dbc3441b73dd459d40511`
(172 lines, applied byte-identical)
**Applied version:** `20261115212728`

**Verdict: `APPLIED AND VERIFIED — DEVELOPMENT`** — with one **blocking code finding**
(§8) that must be corrected before any live Discovery run.

---

## 1. Preflight

| # | Check | Result |
|---|---|---|
| 1 | Branch / HEAD | `feat/eryndex-platform-transform` / `4fe6168` — matches the directive |
| 2 | Working tree | clean (`git status --porcelain` empty) |
| 3 | Migration file read | `supabase/migrations/pending/20261113_discovery_candidates.sql`, 172 lines |
| 4 | SHA-256 | `385d79c7c1028d0eabf2c40176bcb775bb35faab1b6dbc3441b73dd459d40511` |
| 5 | Target project | `supabase/.temp/project-ref` = `vyfqmrdznmocnmzyjjcj`; CLI printed `Target: vyfqmrdznmocnmzyjjcj` |
| 6 | Already applied? | no — `pg_type.discovery_candidate_status` = 0 rows, both tables absent, history `{20261107174129, 20261114194622}` |
| 7a | `companies` | present |
| 7b | `update_updated_at_column()` | present (2 overloads) |
| 7c | `is_human_operator()` | present |
| 7d | `is_agent()` | present |
| 7e | `is_agent_type()` | present |
| 7f | `current_agent_identity()` | present |
| 8 | Object conflicts | policies 0, indexes 0, trigger 0 — all eight policy names, six index names and the trigger name were free |

No preflight check failed, so execution proceeded.

## 2. Execution

Method: the Development procedure established in Directive 03.4 —

```
supabase db query -f supabase/migrations/pending/20261113_discovery_candidates.sql --remote --yes
```

`db push` / `migration list` / `migration repair` still hang at
`Initialising login role…` (Docker daemon wedged), so the authenticated remote
SQL-query path was used again. The file was applied **unmodified**, carrying its own
`BEGIN`/`COMMIT`, so its transaction boundary was preserved. No other pending file was
executed. No credential, token or connection string appears in this report or in any log
kept for it.

Outcome: exit 0, `rows: []` (a DDL batch returns no rows), no error, no partial-state
message. Nothing was retried.

## 3. Catalog verification (Development `vyfqmrdznmocnmzyjjcj`, PostgreSQL 17.11)

| Check | Verified value |
|---|---|
| **V1** enum `discovery_candidate_status` | `pending_review, approved, rejected, duplicate, conflict` — all five, in order |
| **V2** `discovery_candidates` columns | `id, name, normalized_name, domain, source_name, source_url, discovered_at, signals, status, batch_key, discovered_by_agent_identity, reviewed_by_human_id, reviewed_at, review_notes, converted_company_id, created_at, updated_at` |
| **V3** `discovery_candidate_evidence` columns | `id, candidate_id, url, excerpt, retrieved_at, provider` |
| **V4** constraints | `chk_candidate_evidence_url`, `chk_discovery_candidate_domain_format`, `chk_discovery_candidate_review_pair`, `discovery_candidate_evidence_candidate_id_fkey`, `discovery_candidate_evidence_pkey`, `discovery_candidates_converted_company_id_fkey`, `discovery_candidates_pkey` |
| **V5** indexes | `uq_discovery_candidates_pending_domain`, `uq_discovery_candidates_batch_domain`, `uq_discovery_candidate_evidence_url`, `idx_discovery_candidates_status`, `idx_discovery_candidates_normalized_name`, `idx_discovery_candidate_evidence_candidate`, plus the two primary keys |
| **V6** RLS | `discovery_candidates = true`, `discovery_candidate_evidence = true` |
| **V7** policies (8) | `agent_insert_discovery_candidates` INSERT/authenticated · `agent_select_own_discovery_candidates` SELECT/authenticated · `agent_insert_discovery_evidence` INSERT/authenticated · `agent_select_discovery_evidence` SELECT/authenticated · `human_operator_all_discovery_candidates` ALL/authenticated · `human_operator_all_discovery_evidence` ALL/authenticated · `anon_deny_discovery_candidates` ALL/anon · `anon_deny_discovery_evidence` ALL/anon |
| **V8** grants | no `anon` / `authenticated` / `service_role` **table grants** — access is policy-only, as the migration intends. No broad-access or service-role policy exists. |
| **V9** trigger | `trg_discovery_candidates_updated_at` → `update_updated_at_column()`, `tgtype = 19` (ROW + BEFORE + UPDATE) |
| **V10** evidence FK | `discovery_candidate_evidence_candidate_id_fkey` → `discovery_candidates` |
| **V12** row counts | `discovery_candidates = 0`, `discovery_candidate_evidence = 0` (no test data left behind) |
| **V13** unrelated data | `companies = 1`, `research_findings = 13`, `tasks = 1` — identical to the Phase 0 baseline |

Index definitions confirmed verbatim from `pg_indexes`:

```
uq_discovery_candidates_batch_domain   UNIQUE (batch_key, domain)
uq_discovery_candidates_pending_domain UNIQUE (domain) WHERE status = 'pending_review'
uq_discovery_candidate_evidence_url    UNIQUE (candidate_id, url)
```

## 4. Rolled-back functional probes

Everything below ran inside one transaction that ended in `ROLLBACK`; both tables
returned `count = 0` afterwards. These are structural/constraint probes only — no Auth
identity, no RLS bypass, no persistent row.

| Probe | Observed |
|---|---|
| P1 insert a pending candidate | succeeds, `status = pending_review` |
| P2 `updated_at` trigger on UPDATE | fired |
| P3 malformed domain | `23514` (`chk_discovery_candidate_domain_format`) |
| P4 second pending row, same domain, **different** batch | `23505 uq_discovery_candidates_pending_domain` |
| P5 second row, same domain, **same** batch, still pending | `23505 uq_discovery_candidates_pending_domain` |
| P6 evidence for a non-existent candidate | `23503` (FK) |
| P7 duplicate `(candidate_id, url)` evidence | `23505 uq_discovery_candidate_evidence_url` |
| P8 `reviewed_by_human_id` without `reviewed_at` | `23514` (`chk_discovery_candidate_review_pair`) |

Follow-up probe to pin down which index fires when:

| Case | Index that fired |
|---|---|
| A: same batch, same domain, row still `pending_review` | `uq_discovery_candidates_pending_domain` |
| B: same batch, same domain, row no longer pending | `uq_discovery_candidates_batch_domain` |
| C: different batch, same domain, row no longer pending | no violation (allowed) |

## 5. Migration history

`supabase db query` executes SQL and records nothing, so — exactly as in Directive 03.4 —
history was recorded **after** the state had been verified, with the application
timestamp as version:

```sql
insert into supabase_migrations.schema_migrations (version, name)
values ('20261115212728', 'discovery_candidates') on conflict (version) do nothing;
```

| Version | Name | File |
|---|---|---|
| `20261107174129` | `core_crm_schema` | `migrations/20261107174129_core_crm_schema.sql` |
| `20261114194622` | `hardening_batch1` | `migrations/20261114194622_hardening_batch1.sql` |
| `20261115212728` | `discovery_candidates` | `migrations/20261115212728_discovery_candidates.sql` |

The file was then moved with `git mv` out of `pending/` under that version prefix and its
sha256 re-verified: **identical** (`385d79c7…0511`). The 8-digit `20261113` prefix it was
authored with is a date, not a Supabase version; the applied version is the application
timestamp. `supabase/migrations/pending/README.md` now states that nothing is pending and
lists all three applied versions (it previously claimed the hardening batch was pending —
that line was stale and is corrected).

## 6. Skipped or unavailable verification

| Not verified | Why |
|---|---|
| Live agent RLS behaviour (agent can insert/read only its own pending rows) | no Discovery Agent Auth identity exists, and §4 forbids creating one or weakening RLS. Policies are verified as *definitions*, not as exercised behaviour. |
| `agent_select_discovery_evidence` join semantics | same reason — needs a real agent JWT and a candidate it owns |
| A real Discovery cycle over HTTP | would stage candidates via the agent client, which needs that identity |
| Human Gate 1 conversion path (`converted_company_id` → `companies`) | operator identity + a real review decision |

These remain **unverified, not passed**.

## 7. Safety confirmation

- Production `vqnamszywsuqjxuqseas` was never contacted; every command named
  `vyfqmrdznmocnmzyjjcj` and the CLI echoed that target.
- Only the one authorized migration file was executed. No other SQL, no schema change
  beyond it, no `service_role`, no RLS change, no Auth identity, no outreach dispatch.
- No company, finding or task row was created, modified or deleted (V13 unchanged).
- No Docker or infrastructure operation was performed.
- The migration file was not edited.

## 8. Blocking finding: the recovery classification is inverted by the real schema

This is a **code** defect, not a migration defect, and it is the reason the migration
could not be trusted until it was applied: until now the constraint names were only
modelled in a fake client.

The real schema (probes A/B/C) is:

- `uq_discovery_candidates_pending_domain` is **partial**: `UNIQUE (domain) WHERE
  status = 'pending_review'`. It is what guards a domain across batches, and it fires
  **first** for the ordinary interrupted-write retry — the row is still `pending_review`.
- `uq_discovery_candidates_batch_domain` is `UNIQUE (batch_key, domain)` and therefore
  only fires when the same batch's row is **no longer** pending.

`discovery/ports.py` maps those two signals the other way round:

- `BATCH_CONSTRAINT` → same-batch recovery (a path that, per probe A, is unreachable for
  the case it was built for — and per probe B would anyway be refused by the
  `pending_review` status guard);
- `PENDING_DOMAIN_CONSTRAINT` → `domain_staged_elsewhere` (which is what a legitimate
  retry of **my own** rows now gets).

Effect on the live system: a run whose evidence write failed could not be completed on
retry — it would be reported as a cross-batch conflict, so evidence would never be
written. That is precisely the defect Directives 03.6.1 and 03.6.2 set out to fix, and it
survives because the fake client modelled `pending_domain` as batch-scoped and reported
`batch_domain` for a same-batch retry.

**Required correction (not performed here — outside this directive's scope):** classify by
what the scoped read finds, not by which index name appears in the error —
on any unique violation, read `(batch_key, actor, domain)`:

- row exists for this batch + actor + domain → **recovered** (verify id, domain,
  `pending_review`, reuse the id, continue to evidence);
- no row for this batch + actor but the domain is pending under another batch → **conflict**;
- neither → **fail closed** with an explicit message.

The fake client must also reproduce the partial index faithfully (pending uniqueness is
domain-scoped, not batch-scoped). Until that lands, `SupabaseCandidateSink` should not be
pointed at the live tables.

## 9. Repository bookkeeping after application

Because the file is now applied, three repo facts had to follow it — none of them SQL:

| Change | Reason |
|---|---|
| `git mv supabase/migrations/pending/20261113_discovery_candidates.sql → supabase/migrations/20261115212728_discovery_candidates.sql` | so the directory and the remote history agree; sha256 re-verified identical afterwards |
| `supabase/migrations/pending/README.md` rewritten | it still claimed the hardening batch was *pending, not applied* (stale since 03.4) and listed discovery as unapplied |
| `tests/test_security_sql_static.py` and `tests/test_discovery_persistence.py` repointed | both read the migration by its `pending/` path or asserted it must stay there; the column-parity tests now read the applied file |

Suite after all of it: **391 collected → 385 passed, 6 skipped, 0 failed** (the 6 skips
are the `ERYNDEX_DB_URL` database tests, unchanged).

## 10. Final status

**`APPLIED AND VERIFIED — DEVELOPMENT`**

The migration is applied, byte-identical to the approved file, structurally complete and
constraint-verified against the real catalog. The Discovery persistence layer still needs
the §8 correction before a live run; that correction does not touch the schema.
