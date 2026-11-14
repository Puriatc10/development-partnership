# Directive 03.3 — Development Hardening Migration Execution Plan

**Date:** 2026-11-14 19:46 UTC
**Purpose:** prepare `20261113_hardening_batch1.sql` for controlled execution.
**Nothing was applied.** No `db push`, no Dashboard SQL, no remote DDL/DML, no Production contact.

---

## 1. Verified repository state

| Check | Value |
|---|---|
| Branch | `feat/eryndex-platform-transform` |
| HEAD | **`cb0e1cf`** (Directive 03.2 readiness package) |
| Remote | `origin/feat/eryndex-platform-transform` = `cb0e1cf` (in sync before this change) |
| Working tree | clean before the move; only the rename + test/doc updates now |
| Tests before this change | 336 collected → 330 passed, 0 failed, 6 skipped |

Directive 03.2 deliverables confirmed present: `migration-approval-packet.md` (verdict READY FOR OWNER APPROVAL), `outreach-schema-hardening-proposal.md`, `database-readiness.md`, `docker-restart-plan.md`, `qa-03-2-audit.md`.

## 2. Migration identity

| Field | Value |
|---|---|
| Old path | `supabase/migrations/pending/20261113_hardening_batch1.sql` |
| **New path** | **`supabase/migrations/20261114194622_hardening_batch1.sql`** |
| Version | `20261114194622` (14 digits, sorts after `20261107174129`) |
| Lines | 224 |
| **sha256** | **`57f3b23319e0c2c8ed3080dbce73cfd1d9d76ab912bd6556bb8b24b866421246`** |
| Approved checksum (packet §2) | `57f3b233…1246` — **identical** |

Moved with `git mv`, so git records a rename (`R`) and the blob is unchanged: the checksum is byte-identical to the file the owner reviewed. Nothing was edited in the move.

**Discovery migration:** untouched and still outside the push path —
`supabase/migrations/pending/20261113_discovery_candidates.sql`
(sha256 `d3c8544e5558d9eeb2a2282175e7828fc63f7085d0612f5ac35caaca44d9a71d`). A static test now enforces that it is not staged in this increment.

## 3. Target identity

| | |
|---|---|
| Linked project | **`vyfqmrdznmocnmzyjjcj`** — "Puriatc10's Project", ap-southeast-2, Postgres 17.11.0.003 |
| Linked via | `supabase/.temp/linked-project.json` (org `ktdpfeytpkqnggejhscb`) |
| Production | `vqnamszywsuqjxuqseas` — `linked: false`, no command in this increment referenced it |

The CLI prints `environment: "production"` for the Development project; that is v3 branch semantics for a project's primary database, not our Production project (see `database-readiness.md` §2).

## 4. Dry-run

`supabase db push --dry-run --remote`, run 3 times because the pooler is intermittent (2 failures, 1 success):

```
attempt=1  DbConnectError: failed to connect to postgres: Connection terminated unexpectedly
attempt=2  DRY RUN: migrations will *not* be pushed to the database.
           • 20261114194622_hardening_batch1.sql
           {"upToDate":false,"dryRun":true,
            "migrations":["20261114194622_hardening_batch1.sql"],
            "seeds":[],"roles":[],
            "message":"Finished supabase db push.",
            "target":{"kind":"linked","project_ref":"vyfqmrdznmocnmzyjjcj"}}
attempt=3  DbConnectError: failed to connect to postgres: Connection timed out
```

**Exactly one migration is pending, and it is the intended one.** No seeds, no roles.

## 5. Pending migration list (remote history is the authority)

```
supabase migration list --remote
{"migrations":[
  {"local":"20261107174129","remote":"20261107174129","time":"2026-11-07 17:41:29"},
  {"local":"20261114194622","remote":"","time":"2026-11-14 19:46:22"}]}
```

One applied, one pending. The `remote` column for `20261114194622` is empty — the file exists locally and has not run.

## 6. SQL statement summary

Eight statements inside one `BEGIN; … COMMIT;` (L21–L224). Re-running the file aborts at L125 (duplicate trigger) and rolls back cleanly.

| # | Line | Statement | Effect |
|---|---|---|---|
| 1 | L38 | `CREATE OR REPLACE FUNCTION fn_sync_contact_suppression()` | becomes `SECURITY DEFINER`, `search_path = public, pg_temp`; ledger row for a contact opt-out (H-01) |
| 2 | L67 | `CREATE OR REPLACE FUNCTION fn_sync_company_suppression()` | same for a company; keeps deployed `source = 'manual_blacklist'` |
| 3 | L104 | `CREATE OR REPLACE FUNCTION fn_enforce_agent_task_transitions()` | new guard: agents may only `pending→in_progress→completed` (H-02) |
| 4 | L125 | `CREATE TRIGGER trg_enforce_agent_task_transitions` on `tasks` BEFORE UPDATE | attaches the guard; fires before `trg_enforce_task_governance` |
| 5 | L139 | `DROP POLICY IF EXISTS agent_insert_outreach_messages` | removes the policy that allowed drafting without a qualification |
| 6 | L141 | `CREATE POLICY agent_insert_outreach_messages` | adds the approved-qualification + lifecycle precondition (H-03) |
| 7 | L186 | `CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_qualifications_approved` | at most one approved scorecard per company (H-04) |
| 8 | L198 | `CREATE OR REPLACE FUNCTION fn_sync_company_primary_domain()` | a taken domain raises instead of re-parenting (H-05) |

**Business rule (Directive 03.3 §3) confirmed unchanged** — `L172-175` restricts agent drafting to:

```sql
c.lifecycle_stage IN ('qualified', 'outreach_queued', 'in_outreach')
```

No expansion to active-conversation or partner states. Follow-up workflows remain a separate design.

## 7. Expected impact and lock considerations

| Statement | Lock | Consequence |
|---|---|---|
| Functions (L38, L67, L104, L198) | catalog row lock | no table-level blocking; sessions keep the old body until `COMMIT` |
| `CREATE TRIGGER` (L125) | **SHARE ROW EXCLUSIVE** on `tasks` | blocks INSERT/UPDATE/DELETE on `tasks` for the transaction |
| `DROP POLICY` / `CREATE POLICY` (L139, L141) | **ACCESS EXCLUSIVE** on `outreach_messages` | blocks reads as well — a concurrent `SELECT` can make the batch wait |
| `CREATE UNIQUE INDEX` (L186, non-concurrent) | **SHARE** on `lead_qualifications` | blocks writes, allows reads; scans the table (0 rows at baseline) |
| `COMMIT` | — | all locks released together |

Behaviour changes to expect after commit: a second approved scorecard becomes impossible without un-approving the first; a duplicate primary domain hard-fails; agents cannot draft outside the three allowed stages; and a cancelled/in-flight task can no longer be rewound by an agent. Recommended session settings before running: `SET lock_timeout = '5s'; SET statement_timeout = '60s';` — a timeout aborts the whole transaction with nothing applied.

## 8. Verification queries and expected outcomes

Pre-flight (all should be run immediately before applying):

| # | Query | Expected |
|---|---|---|
| P1 | `select count(*) from outreach_messages;` | `0` — no draft is excluded by the new policy |
| P2 | `select company_id, count(*) from lead_qualifications where review_status='approved' group by 1 having count(*)>1;` | 0 rows — otherwise L186 aborts the batch |
| P3 | `select count(*) from research_findings;` | `13` (Refokus intact) |
| P4 | `select status, count(*) from tasks group by 1;` | at most one `completed`; no agent-held `in_progress` that the guard would trap |
| P5 | `select identifier, source from suppression_registry;` | 1 row; record its `source` — first-writer-wins under `ON CONFLICT DO NOTHING` |

Post-apply (the packet's V1–V9, abbreviated):

| # | Query | Expected |
|---|---|---|
| V1 | `select prosecdef, proconfig from pg_proc where proname in ('fn_sync_contact_suppression','fn_sync_company_suppression');` | both `prosecdef = true`, `proconfig` pins `search_path` |
| V2 | `select tgname from pg_trigger where tgname = 'trg_enforce_agent_task_transitions';` | 1 row |
| V3 | `select policyname, with_check from pg_policies where tablename='outreach_messages' and policyname='agent_insert_outreach_messages';` | `with_check` mentions `lead_qualifications` and the three lifecycle stages |
| V4 | `select indexname from pg_indexes where indexname='uq_lead_qualifications_approved';` | 1 row |
| V5 | `select prosrc like '%RAISE EXCEPTION%' from pg_proc where proname='fn_sync_company_primary_domain';` | `true` |
| V6 | `supabase migration list --remote` | `20261114194622` present in **both** columns |
| V7 | Re-run `python -m pytest tests/db -q` with a DSN | H-01/H-04/H-05 pass for the stated reason; H-03 skips unless a drafter task exists |

## 9. Execution route (owner)

**Route A — CLI (records the version automatically)**

```bash
cd "C:/Users/puria/Desktop/code-workspace/projects/development-partnership"
git status --short          # expect only this plan + the rename, committed
supabase migration list --remote   # confirm 20261114194622 still has empty remote
supabase db push --dry-run --remote  # confirm exactly one pending
supabase db push --remote           # apply
supabase migration list --remote    # 20261114194622 now in both columns
```

Retry the whole command on a pooler failure; never run a fragment. The batch is one transaction, so a mid-flight failure leaves nothing applied.

**Route B — Dashboard SQL editor** (paste the file, it carries its own `BEGIN;/COMMIT;`) then record the version, or the next `db push` will try to re-apply:

```bash
supabase migration repair --status applied 20261114194622
```

**Rollback:** the packet's §8 statements (drop the trigger, restore the three original functions and the original policy from `crm/schema.sql`, drop the index). Safe, and no data is destroyed by rolling back — but rows written after apply were validated by the new rules, so review before reverting.

## 10. Remaining risks

1. **Connectivity is 2/3 in this session** (5/5 in the previous one). A `db push` that dies mid-flight rolls back; the retry story is "re-run the command".
2. **DDL permission is still unproven.** `cli_login_postgres` completing a plan does not prove it can `CREATE POLICY`.
3. **No DSN**, so the six DB tests still skip — post-apply evidence must come from V1–V7 plus the SQL checks, not from pytest.
4. **`ON CONFLICT (identifier) DO NOTHING`** means the single existing suppression row keeps its current `source`; H-01 fixes the permission for new rows, it does not rewrite history.
5. **Docker/host state is degraded** (daemon wedged, `trading_bot_postgres` unhealthy, 481 MB free). The migration path is the Supabase pooler and does not use Docker, so it is unaffected — but do not attempt the apply while the host is swapping heavily if you can wait for the restart decision.

## 11. Verdict

**READY FOR OWNER EXECUTION APPROVAL**

Exactly one migration is staged and pending, byte-identical to the approved file, targeting the linked Development project, with the business rule in §3 unchanged. Execution requires only the owner's approval and a stale-free window; no further preparation is outstanding.
