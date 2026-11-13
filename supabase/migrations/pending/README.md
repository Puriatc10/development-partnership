# Migrations pending credentials

Files here are **authored and reviewed but NOT applied** to any database.

They live outside `supabase/migrations/` on purpose: that directory means "applied
to the deployed project". Mixing the two would let a future `supabase db push`
claim history that never happened, and would make the migration list lie.

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
| `20261113_hardening_batch1.sql` | suppression write path (H-01), agent task transitions (H-02), outreach qualification precondition (H-03), duplicate approved scorecard (H-04), domain re-parenting (H-05) | Low: additive functions/triggers, two policy/index replacements, no data rewrite. H-03 will reject drafts the current system would have allowed — verify no such rows exist first. |
