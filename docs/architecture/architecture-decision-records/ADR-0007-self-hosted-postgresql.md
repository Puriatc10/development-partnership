# ADR-0007 — Self-hosted PostgreSQL instead of Supabase

**Status:** accepted, 2026-11-23
**Supersedes:** the Supabase assumptions in ADR-0002 (which extracted Supabase infrastructure so it
could be shared — it was, and then replaced).

## Context

The platform used Supabase for three things: PostgreSQL, GoTrue authentication, and PostgREST as
the HTTP data layer. Two events made that untenable:

1. **The owner's decision.** Supabase is not the shape this product should depend on. The platform
   is a single-tenant CRM for one business; it does not need a hosted control plane, and vendor
   features it never used (Storage, Realtime, Edge Functions) were never the draw.
2. **Operational friction, twice measured.** `supabase db push`, `migration list` and
   `migration repair` hang behind a Docker-dependent login-role step on this workstation, so
   migrations had to be applied through `db query` with the history written by hand. Then
   `supabase db dump` returned `451 Access from your region is restricted` when a schema snapshot was
   attempted. A database whose schema cannot be dumped on demand is not a database we can operate.

## Decision

Run **PostgreSQL 17 in Docker Compose**, owned by this repository:

- **Roles** `anon`, `authenticated`, `migrator` — the same ones every RLS policy is written against.
- **`auth` schema** with `auth.uid()`, `auth.jwt()`, `auth.role()` reading
  `request.jwt.claims`, and `auth.users` holding argon2id password hashes and the
  `app_metadata` claims the gate model reads.
- **No PostgREST.** `integrations/postgres/client.py` is an in-process psycopg client with the same
  four methods the 424 call sites already used. Every call runs in its own transaction with
  `SET LOCAL ROLE` and `SET LOCAL request.jwt.claims`, so RLS evaluates exactly what it evaluated
  before, without an HTTP layer or a second identity system.
- **No GoTrue.** `integrations/postgres/auth.py` verifies argon2id and mints HS256 tokens with the
  same claim shape; `scripts/create_identity.py` replaces the admin API.
- **A ledger we own:** `public.schema_migrations(version, name, sha256, applied_at, applied_by)`,
  applied by `scripts/db_migrate.py`, which refuses a file whose checksum changed after application.

## Consequences

**Good**

- The security model is unchanged in *shape* and now provable: the six database policy tests can
  run, and the 11 adversarial RLS cases execute in-process against the real database.
- No vendor surface, no HTTP data layer, no region-restricted admin path, no per-project quotas.
- The schema is dumpable, the migrations are files, and the identity table is ours.

**Costs and risks**

- We own backups, upgrades, TLS, and connection management. For development that is a container and
  a volume; for production it is real operational work that must be planned (Phase M2).
- We reimplemented the PostgREST filter subset we use (`eq.`, `in.`, `is.null`, ordering, paging)
  and must keep it honest: an unrecognised operator raises instead of being ignored, and a contract
  suite runs against the real database.
- The application signs its own tokens, so the database trusts the application to set claims
  correctly. That holds because the application is the only client of this database; it must be
  revisited if anything else is ever given direct access.
- PostgreSQL version upgrades are now our responsibility (17 today, and `pg_upgrade` is not free).

**Deliberately not done**

- No data migration. The owner's instruction: the existing rows do not matter. The retired project
  is left untouched; the historical Refokus research survives as a repository artefact.
- No HA, replication, PgBouncer or managed backup yet — those belong to production readiness, not
  to the development cutover.
