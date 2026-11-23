# Migrations — how this directory works

`0000_foundation.sql` runs first and provides what the CRM schema assumes exists: the three roles
the grants are written against, the `auth` schema, `auth.users`, and `auth.uid()` / `auth.jwt()`.
`migrations/` then holds the dated CRM migrations, applied in version order.

Apply and inspect with:

```
python scripts/db_migrate.py --status
python scripts/db_migrate.py --dry-run
python scripts/db_migrate.py
```

## The ledger is the authority

`public.schema_migrations` records `(version, name, sha256, applied_at, applied_by)`.
`--status` compares the files on disk to that ledger, and a file whose checksum changed after it
was applied is **refused**: an applied migration is immutable, because editing it would mean the
ledger no longer describes what actually ran. Write a new migration instead.

`0000_foundation` is recorded without a 14-digit version because it is not a dated change; it is
the base every dated migration expects.

## Status headers inside the SQL files are historical

Two of the migrated files still say "AUTHORED, NOT APPLIED" in their header comment. That was true
when they were written. The headers were deliberately **not** edited: changing an applied
migration's text changes its checksum and breaks the ledger's meaning. The ledger and this
document are the record of application.

## Changing the schema

A new schema change is a new file in `migrations/`, applied by the process above, and it requires
owner approval before it runs. No migration is applied by the application at runtime.
