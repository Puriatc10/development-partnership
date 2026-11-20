# Applied migrations — how to read this directory

The 14-digit version prefix is what the Supabase CLI enumerates. **These three files are
applied in Development `vyfqmrdznmocnmzyjjcj`**, and the remote history is the authority:

```
20261107174129  core_crm_schema
20261114194622  hardening_batch1
20261115212728  discovery_candidates
```

Verify with `supabase migration list --remote`, or by reading
`supabase_migrations.schema_migrations` directly — the CLI subcommands that need Docker hang
on this workstation, so the history was recorded through `db query` after the catalogue was
verified (see `reports/eryndex/directive-03-4-migration-execution.md` and
`directive-03-7-discovery-migration-application.md`).

## In-file status headers are historical

The header comment inside `20261114194622_hardening_batch1.sql` still says
"AUTHORED, NOT APPLIED", and `20261115212728_discovery_candidates.sql` still says
"AUTHORED, NOT APPLIED (BLK-01: no DDL credentials)". Both statements were true when the
files were written and are false now.

The headers were **not** edited, deliberately: an applied migration is immutable. Changing
its text would change its checksum and make the file stop describing what actually ran. The
report is the record of application, not the comment.

## Nothing is pending

`pending/` holds no SQL. A file belongs there only before application; a file that sits in
`migrations/` is a claim of application, and this directory is not a staging area.

## Changing the schema from here

Any new schema change is a separate migration and requires explicit owner approval before it
is applied. Two proposals are already written and waiting on that approval: binding Gate 1's
`reviewed_by_human_id` to `auth.uid()`, and audit triggers on `companies` and discovery
review. See `reports/eryndex/FINAL_PLATFORM_AUDIT.md` §4 and §8.
