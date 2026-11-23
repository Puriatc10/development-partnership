# Migration to self-hosted PostgreSQL — execution report

**Range:** `447dc94` → `HEAD` (tag `pre-selfhost` marks the Supabase-shaped state)
**Environment:** local container `eryndex-postgres` (PostgreSQL 17.11) on `127.0.0.1:5433`
**Decisions taken:** D1 psycopg adapter · D2 keep JWTs · D3 argon2id · D4 compose deployable ·
D5 old project untouched · D6 three roles

## What is true now, and how it was proven

| Claim | Evidence |
|---|---|
| The platform runs on our own PostgreSQL | `docker compose ps` → `eryndex-postgres healthy`; `psql` reachable on 127.0.0.1:5433 |
| All four migrations applied, in order, recorded | `python scripts/db_migrate.py --status` → 4 applied; ledger `0000_foundation`, `20261107174129`, `20261114194622`, `20261115212728` |
| The schema matches what the RLS model expects | catalogue read: 17 public tables (16 + ledger), 16 with RLS, 52 policies, roles `anon`/`authenticated`/`migrator`, `auth.jwt/uid/role` present |
| RLS still decides access | `scripts/rls_probe.py` → **11/11** in-process, including agent-cannot-approve its own candidate (row stays `pending_review`), agent cannot insert `companies` (42501), anon denied (42501) |
| Human Gate 1 works on the new stack | fixture → `candidates approve` → `candidates register` → company `f87394df`; second register reported "already registered"; both through the operator's own password sign-in |
| The data client behaves like the one it replaced | 12 contract tests against the real database: ownership-scoped reads, 42501 on policy denial, `23505` naming `uq_discovery_candidates_pending_domain`, `in./order/limit/offset`, JSONB round-trip, claims never leaking between calls |
| Supabase is gone from the runtime | `grep -r integrations.supabase` → no code references; `supabase/` removed; `requirements.txt` has psycopg/argon2/PyJWT and no vendor SDK |
| Production cannot be reached by omission | `tests/test_environment_guard.py` — local-only in development; a remote host needs `ERYNDEX_ENV=production` *and* an explicit allow-list |

**Suite: 432 passed, 4 skipped, 0 failed.**

## Phases

**0 — safety net.** Tag `pre-selfhost`. The schema-only dump was attempted and blocked:
`supabase db dump` → `451 Access from your region is restricted`. Recorded rather than worked
around; it is one of the reasons for the migration.

**1 — database.** `compose.yaml` (postgres:17-alpine, named volume, healthcheck, loopback-only
port, opt-in `psql` profile). `db/0000_foundation.sql` for roles + auth schema + ledger.
`scripts/db_migrate.py` applies files in order, honours files that carry their own `BEGIN/COMMIT`,
records checksums and refuses drift.

**2 — data access.** `integrations/postgres/client.py`: same `get/post/patch/delete` signatures,
transaction-scoped role and claims, identifier validation against the catalogue, parameterised
values, unknown filters refused.

**3 — identity.** `integrations/postgres/auth.py` (argon2id + HS256, same claim shape),
`scripts/create_identity.py`, and three identities created locally: operator, discovery agent
(`subagent_type=discovery`, `agent_id=hermes-discovery-v1`) and qualifier
(`subagent_type=qualifier`). The discovery-agent identity that was blocked on a dashboard action is
now a local row — **OW-1 is resolved.**

**4 — cutover.** `operations/session.py`, `qualification/service.py`, `scripts/gate1_fixture.py`
and the qualification repository now import `integrations.postgres`. The environment guard was
rewritten from a project-ref pin to a host policy. `scripts/rls_probe.py` no longer shells out to a
CLI.

**5 — verification.** See the table above. `tests/db/` executes for the first time: H-01 passes,
and the historical-research test was rewritten to assert the artefact (13 findings in
`agents/researcher/runs/refokus-research.json`) plus the absence of a half-imported copy.

**6 — cleanup.** `supabase/` deleted; `db/README.md` documents the ledger; ADR-0007 records the
decision; the retired researcher slice was removed (see below).

## Defects found and fixed during the migration

| Defect | Fix |
|---|---|
| `db_migrate` toggled `autocommit` inside an open transaction | connection is autocommit; files with their own `BEGIN/COMMIT` run as-is |
| Core migration carries a UTF-8 BOM → `syntax error at or near "\ufeff"` | executed as `utf-8-sig`; the checksum still hashes raw bytes so a BOM change counts as drift |
| `jsonb` write failed on `datetime` (`reviewed_at`) after leaving PostgREST, which had already stringified it | `_iso()` in registration converts timestamps before the insert |
| Contract-test cleanup could not delete rows | discovery agents have **no DELETE policy** — correct (agents must not remove evidence) — so cleanup runs as the owner and the test explains why |
| Two of my own test bugs (NOT NULL instead of unique; a "foreign" row carrying the agent's own `agent_id`) | tests fixed, not the product |

## Deliberately not done

- **No data migrated.** Left in the retired project, which was not touched. Refokus research
  survives as a repository artefact.
- **The retired researcher slice** (`agents/researcher/*.py` and its two test modules) was removed:
  it spoke PostgREST and GoTrue directly and never researched anything — it persisted a
  hand-authored payload. Its historical output is kept. The research executor (roadmap D2) will be
  rebuilt on the new layer; until then `qualify` on a company with no research fails at the loader,
  which is the honest state.
- **Four database tests still skip**, now for a different reason: they need prerequisite rows
  (`in_progress` tasks, an approved scorecard, a secondary domain) that a fresh database does not
  contain. Seeding them inside each test is the next piece of work; they are skips, not passes.
