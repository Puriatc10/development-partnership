# Migrations pending credentials

Files here would be **authored and reviewed but NOT applied** to any database.

They live outside `supabase/migrations/` on purpose: a 14-digit version there is what a
future `supabase db push` will pick up.

> **Authority is the remote history, not the directory.**
>
> Nothing is pending. `supabase_migrations.schema_migrations` on Development
> (`vyfqmrdznmocnmzyjjcj`) lists:
>
> | Version | Name | File | sha256 |
> |---|---|---|---|
> | `20261107174129` | `core_crm_schema` | `migrations/20261107174129_core_crm_schema.sql` | — |
> | `20261114194622` | `hardening_batch1` | `migrations/20261114194622_hardening_batch1.sql` | `57f3b233…1246` |
> | `20261115212728` | `discovery_candidates` | `migrations/20261115212728_discovery_candidates.sql` | `385d79c7…0511` |
>
> Evidence: `reports/eryndex/directive-03-4-migration-execution.md` and
> `reports/eryndex/directive-03-7-discovery-migration-application.md`.

## How a migration is applied here

1. Confirm the target is Development (`vyfqmrdznmocnmzyjjcj`) and that Production
   (`vqnamszywsuqjxuqseas`) is untouched.
2. Verify the file's sha256 against the approved checksum and run the preflight checks.
3. Apply the exact file with `supabase db query -f <file> --remote --yes`, because
   `db push` / `migration list` / `migration repair` hang at `Initialising login role…`
   while the Docker daemon is wedged. The files carry their own `BEGIN`/`COMMIT`.
4. Verify the catalog (`pg_type`, `information_schema.columns`, `pg_indexes`,
   `pg_policies`, `pg_class.relrowsecurity`, `pg_trigger`) — a success message is not
   verification.
5. Record history with an explicit
   `insert into supabase_migrations.schema_migrations (version, name)` **using the
   application timestamp**, then move the file into `supabase/migrations/` with that
   same version prefix so the directory and the remote history agree.
6. Rolled-back functional probes for the constraints, then the report.

## Files

| File | State | Notes |
|---|---|---|
| *(none)* | — | Both previous tenants moved to `supabase/migrations/` after their verified application: `20261114194622_hardening_batch1.sql` (2026-11-14) and `20261115212728_discovery_candidates.sql` (2026-11-15). |

The discovery file was authored here as `20261113_discovery_candidates.sql`; its
8-digit prefix is a date, not a Supabase version, so the applied version is the
application timestamp `20261115212728`. Content is byte-identical before and after the
move (sha256 `385d79c7c1028d0eabf2c40176bcb775bb35faab1b6dbc3441b73dd459d40511`).
