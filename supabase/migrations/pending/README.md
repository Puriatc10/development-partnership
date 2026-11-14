# Migrations pending credentials

Files here are **authored and reviewed but NOT applied** to any database.

They live outside `supabase/migrations/` on purpose: a 14-digit version there is what a
future `supabase db push` will pick up.

> **Authority is the remote history, not the directory.** After Directive 03.3 the
> hardening batch was staged for execution as
> `supabase/migrations/20261114194622_hardening_batch1.sql` (sha256 `57f3b233…1246`,
> byte-identical to the approved file). It is still **unapplied**: `supabase migration
> list --remote` shows `20261114194622` local with no remote counterpart, and the
> execution plan is `reports/eryndex/directive-03-3-migration-execution-plan.md`. Do not
> read "the file is in migrations/" as "the migration ran".

## How to apply (once BLK-01 clears)

1. Confirm the target is Development (`vyfqmrdznmocnmzyjjcj`) and that Production
   (`vqnamszywsuqjxuqseas`) is untouched.
2. Take a fresh logical export: `python <scratch>/backup_dev.py` → `backups/`.
3. `supabase link --project-ref vyfqmrdznmocnmzyjjcj`
4. `psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/pending/<file>.sql`
   (the files are wrapped in a transaction).
5. Re-run the security checks in `reports/eryndex/security-findings.md` §Verification.
6. Move the file into `supabase/migrations/` with the applied timestamp prefix and
   record the run in `reports/eryndex/implementation-progress.md`.

## Files

| File | Fixes | Risk |
|---|---|---|
| *(none — hardening moved)* | the hardening batch was staged for execution as `supabase/migrations/20261114194622_hardening_batch1.sql` on 2026-11-14; it is pending, not applied. See `reports/eryndex/directive-03-3-migration-execution-plan.md` | — |
| `20261113_discovery_candidates.sql` | Workflow 1 staging: `discovery_candidates` + `discovery_candidate_evidence` + `discovery_candidate_status`, with agent staging limited to own `pending_review` rows and operators owning Human Gate 1 | Low: additive only (two new tables, one new type, indexes, policies). No existing object is altered. Depends on `companies(id)` and the existing helper functions; no ordering constraint against the hardening batch. |
