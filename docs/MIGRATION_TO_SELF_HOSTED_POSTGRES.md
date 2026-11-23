# Migration plan — Supabase → self-hosted PostgreSQL (Docker Compose)

**Status:** proposal, awaiting owner approval. **No code has been changed.**
**Scope:** replace Supabase as the platform's database and identity provider with PostgreSQL 17
running in Docker Compose on this workstation (development), with a path to any host later.
**Data:** not migrated. The existing Supabase Development project is left untouched and
read-only; nothing is deleted from it by this work.

---

## 1. What we actually use from Supabase today

Measured by grep over the repository, not by assumption.

| Supabase feature | Used? | Where | Consequence of leaving |
|---|---|---|---|
| **PostgreSQL database** | **yes** | 3 applied migrations, 16 tables | Re-created on our own Postgres |
| **GoTrue Auth** (password grant, JWT with `app_metadata`) | **yes** | `integrations/supabase/auth.py:40` (`/auth/v1/token`), `agents/researcher/provision.py:37,51` (admin API) | Must be replaced: password hashing + token minting + identity provisioning |
| **PostgREST** (`/rest/v1`, filters, representations) | **yes** | `integrations/supabase/client.py:46`; 401 `get`, 15 `post`, 7 `patch`, 1 `delete` call sites | Must be replaced by a data-access layer |
| **`auth.uid()` / `auth.jwt()` SQL helpers** | **yes — 80 and 23 call sites** | every RLS policy and several triggers | Must be provided as our own SQL functions, or all policies break |
| **Roles `anon`, `authenticated`, `service_role`** | **yes** | grants and policies in all three migrations | Roles must exist with equivalent privileges |
| **`service_role` admin credential** | provisioning only | `agents/researcher/provision.py:74` | Replaced by a local `auth.users` insert |
| **Supabase CLI** (`db query`, `db push`, `migration list`, `db dump`, `db advisors`, `link`) | dev tooling only | `scripts/rls_probe.py`, docs | Replaced by psql/psycopg + a migration runner |
| Storage, Realtime, Edge Functions, `supabase-js`, pgvector, Cron, branching | **no** | zero references in Python | nothing to replace |

**The answer to your question:** we use exactly two non-database features — **Auth (GoTrue)** and
**PostgREST** — plus the `auth.*` SQL helper functions and the three database roles that
Supabase pre-creates. Everything the application does flows through one 134-line HTTP client and
one 90-line auth client. That is why this migration is tractable rather than a rewrite.

---

## 2. Target architecture

```
docker compose
├── postgres:17            ← the database, our own volume, our own roles
│     ├── schema public    ← the 16 tables (same DDL as today)
│     └── schema auth      ← auth.users (email, password hash, app_metadata)
│         ├── auth.uid()   ← reads request.jwt.claims
│         └── auth.jwt()   ← reads request.jwt.claims
├── (no PostgREST)
└── (no GoTrue)
```

Application process (Python, unchanged in shape):

```
operations/cli  scripts/*  agents/*
        │
        ▼
integrations/postgres/client.py     ← same 4 methods as today's PostgrestClient
        │  psycopg 3, transaction per call:
        │      SET LOCAL ROLE authenticated
        │      SET LOCAL request.jwt.claims = '<claims json>'
        ▼
   PostgreSQL  ← RLS does exactly what it does today
```

### Why in-process psycopg instead of a PostgREST container

| Option | Change required | Security | Verdict |
|---|---|---|---|
| **A. Run PostgREST ourselves** | none in app code; new container + config + JWT secret | same as today (RLS + claims) | fastest, but keeps a third-party HTTP layer whose semantics we do not control, and the reason you want to leave Supabase is precisely to stop depending on a vendor-shaped stack |
| **B. psycopg adapter behind the existing client interface** (recommended) | one module rewritten; **zero** changes at the 424 call sites | **better**: one connection, one transaction, explicit `SET LOCAL ROLE`, no HTTP surface, no exposed REST endpoint | chosen |

Option B keeps the security model intact (RLS is still the boundary and still reads claims), removes
the entire HTTP layer, and makes the six currently-skipped database tests runnable.

---

## 3. Decisions I need from you

| # | Decision | My recommendation |
|---|---|---|
| D1 | **Data access**: psycopg adapter behind the current interface (B), or self-hosted PostgREST (A)? | **B** |
| D2 | **Identity**: keep minting JWTs (HS256, local secret, same claim shape) or pass claims to the DB without a token? | **Keep JWTs** — the claim contract is what the policies read; a token keeps one identity artifact and leaves room for an HTTP API later without redesigning auth |
| D3 | **Password hashing**: argon2id (recommended) or bcrypt? | **argon2id** |
| D4 | **Where does Postgres live**: Compose on this workstation only, or Compose file written to be deployable to a server too? | **Both** — one `compose.yaml` with profiles; development defaults to a local volume |
| D5 | **Old Supabase project**: leave as-is (read-only, still billing) or pause it after cutover? | Leave it; pausing is your call in the dashboard, and I will not touch it |
| D6 | **Database name/roles**: one app role per identity class (`anon`, `authenticated`, `migrator`) as today, or a single role with RLS? | Keep the three roles — the policies are written against them |

Nothing below runs until you approve D1–D6.

---

## 4. Phases

Sizes are relative (S ≈ a few hours, M ≈ a day, L ≈ several days), not dates.

### Phase 0 — Decisions and safety net (S)
- Confirm D1–D6.
- Tag the current `main` (`pre-selfhost`) so the Supabase-shaped state is recoverable.
- Snapshot the applied schema with `pg_dump --schema-only` from Supabase into
  `supabase/baseline/schema-2026-11-20.sql` as a cross-check artifact (no data).
- **Exit:** decisions recorded in this file; tag exists; baseline dump committed.

### Phase 1 — Postgres up, schema applied (M)
- `compose.yaml`: `postgres:17` pinned, named volume, healthcheck, `POSTGRES_*` from `.env`,
  port `5432` bound to `127.0.0.1` only.
- `scripts/db_migrate.py`: applies `supabase/migrations/*.sql` in order inside one transaction per
  file, records `(version, name, sha256, applied_at)` in `public.schema_migrations`. Dry-run mode.
- `db/0000_foundation.sql` (new, applied first): roles `anon`, `authenticated`, `migrator`; schema
  `auth`; `auth.users`; `auth.uid()`; `auth.jwt()`; grants mirroring today's.
- Move the three existing migrations under `db/migrations/` unchanged, except where they reference
  `supabase_migrations` or `service_role` (both are comments or provisioning-only).
- **Exit:** `docker compose up -d` healthy; `python scripts/db_migrate.py` applies 4 files; `psql`
  lists 16 tables + `auth.users`; a smoke insert as `authenticated` with claims respects RLS.

### Phase 2 — Data-access adapter (L — the real work)
- `integrations/postgres/client.py`: `get/post/patch/delete` with the **same signatures** as
  `integrations/supabase/client.py`; `select`/`order`/`limit`/`offset` and `eq.`/`in.` filters
  translated to parameterised SQL; identifiers validated against the introspected column list
  (no string interpolation of user input); `RETURNING *` for representations.
- `PostgrestError` kept as an alias of `DatabaseError` so the 424 call sites and their tests do
  not change. Same attributes (`status_code`, `code`, `message`) so error handling keeps working —
  SQLSTATE supplies `code`, so existing `42501`/`23514` expectations still hold.
- Transaction wrapper: `SET LOCAL ROLE <role>` + `SET LOCAL request.jwt.claims = %s` per call;
  no superuser connection in any application path.
- Contract test suite: a matrix comparing the adapter against the semantics we rely on
  (`eq.`, `in.`, `order`, `limit`, `offset`, `select`, empty result, unique violation, RLS denial)
  run against a real container; plus the existing 442 tests unchanged.
- **Exit:** full suite green against Docker Postgres; the six `tests/db` tests un-skipped and green.

### Phase 3 — Identity (M)
- `integrations/postgres/auth.py`: `sign_in_with_password` verifies argon2id and mints HS256 with
  the identical claim shape (`sub`, `role`, `app_metadata.user_role`, `subagent_type`, `agent_id`,
  `exp`); refusal messages keep the current wording so operator UX does not change.
- `scripts/create_identity.py` replaces `agents/researcher/provision.py`: creates/updates
  `auth.users` rows locally (admin action, run by you, password entered at a prompt — never in a
  file, never logged).
- Recreate the three identities we actually need: operator, discovery agent, qualifier.
- **Exit:** `python -m operations.cli whoami` works against local Postgres; an agent claim is still
  refused for a human action and vice versa.

### Phase 4 — Cutover of entry points and guards (M)
- `contracts/environment.py`: guard becomes `ERYNDEX_DB_URL` + `ERYNDEX_ENV ∈ {development,
  production}` with a host allow-list; the Production **project ref** refusal is replaced by "refuse
  any host that is not explicitly allow-listed, refuse `production` unless the caller opts in".
- `operations/session.py`, `qualification/*`, `discovery/*`, `outreach/*`, `scripts/*`: swap the
  import from `integrations.supabase` to `integrations.postgres` (one line each) — the rest is
  unchanged because the interface is identical.
- `scripts/rls_probe.py`: stops shelling out to the Supabase CLI; opens a psycopg connection and
  runs the same 11 cases in `BEGIN … ROLLBACK`. The pre-flight guard becomes a DB-host guard.
- Delete `supabase/` (config, `.temp`, `link` state) and the CLI dependency; keep the SQL under
  `db/migrations/`.
- **Exit:** `pytest` green with no Supabase imports (`grep -r integrations.supabase` empty);
  `operations.cli` Gate 1 loop passes live against local Postgres; never a Production host in any
  default.

### Phase 5 — Verification at parity (M)
- The six policy tests (H-01…H-05 + Refokus immutability) run and pass — for the first time.
- `scripts/rls_probe.py` 11/11 against Docker Postgres.
- Gate 1 live loop: synthetic candidate → approve → company → re-run creates nothing.
- Qualification read: a company with findings scores deterministically.
- Backup/restore rehearsal: `pg_dump`/`pg_restore` of the local volume, restored into a scratch DB.
- **Exit:** every claim in this list has a captured command and output in a report.

### Phase 6 — Documents and cleanup (S)
- Rewrite `supabase/migrations/README.md` → `db/README.md` (authority = `schema_migrations`).
- Update roadmap §"Development integration requirements", the progress board's baseline, and ADR-0002
  (which described extracting Supabase infrastructure) with a new ADR-0007 recording this decision.
- Record what was deliberately not migrated and what to do with the old project.

---

## 5. What changes, file by file

| File | Change |
|---|---|
| `integrations/supabase/client.py` | deleted → `integrations/postgres/client.py` (same interface) |
| `integrations/supabase/auth.py` | deleted → `integrations/postgres/auth.py` (argon2 + HS256) |
| `integrations/supabase/qualification_repository.py` | moved, **logic unchanged** |
| `agents/researcher/{auth,client}.py` | shims repointed |
| `agents/researcher/provision.py` | replaced by `scripts/create_identity.py` |
| `contracts/environment.py` | guard rewritten for a DB URL |
| `operations/*`, `qualification/*`, `scripts/*` | import line only |
| `tests/db/*` | un-skipped, hermetic scratch DB per session |
| `supabase/` | removed after cutover |
| `compose.yaml`, `db/`, `scripts/db_migrate.py` | new |

**Not changing:** all 16 table definitions, every RLS policy, all triggers, the IPP rubric, the
human-gate model, the discovery/qualification/outreach logic. The security model is the same model;
only its host changes.

---

## 6. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Re-implementing PostgREST filter semantics subtly wrong (`in.`, ordering, NULLs) | wrong rows read or written | contract matrix against the real DB; every call site already covered by tests; `select`/`order` identifiers validated against introspected columns |
| Claims not set on a connection → RLS sees `anon` and denies (or worse, allows) | silent denial or data exposure | one transaction per call, always `SET LOCAL ROLE` + claims; a test asserting that a missing claim is denied, not allowed |
| `auth.uid()` semantics differ from Supabase's | policies that bound a reviewer to the signer stop working | `auth.uid()` reads `request.jwt.claims->>'sub'`; the migration that binds Gate 1's reviewer ships with a probe proving it |
| Docker unusable on this machine (daemon was wedged; ~27 GB free, WSL disk) | Phase 1 cannot start | check `docker info` and free space first; if the daemon needs a restart I will ask, not do it |
| Losing the audit trail of who changed what during cutover | unclear provenance | Phase 0 tag, per-file sha256 in the migration ledger, and a report per phase |
| Password handling regressions | credential exposure | argon2id, prompt-only entry, no passwords in argv or files, redaction shared with `outreach/gate.py` |

---

## 7. What we leave behind

- **Data:** not migrated, by your instruction. Companies, findings, candidates, scorecards stay in
  the Supabase project. Only the synthetic Gate 1 fixtures and the Refokus research exist there, and
  the research is reproducible from `reports/research/refokus-company-research.md`.
- **The Supabase project itself:** untouched. Pausing or deleting it is a dashboard action on your
  side; I will not do it.
- **CLI state:** `supabase/.temp`, `linked-project.json` removed with the directory.

## 8. Approval

Reply with the six decisions (or "your recommendations") and I will start Phase 0 immediately,
committing each phase separately on `main` with its own verification report. I will not start
Phase 2 (the adapter) until Phase 1 is verified healthy, and I will stop and report if the Docker
daemon cannot run a container on this machine.
