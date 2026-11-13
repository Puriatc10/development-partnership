# Directive 02 — Phase A Reconciliation

**Date:** 2026-11-13
**Branch:** `feat/eryndex-platform-transform`
**HEAD:** `28bbba3` (tree clean)
**Baseline commit verified:** `72a1886` is an ancestor; `c2571aa`, `9c032dd`, `f30d88e`, `28bbba3` all present.
**Diff since baseline:** 63 files, +8,812 / −249.
**Tests:** `python -m pytest -q` → **146 collected, 146 passed** (exit 0).
**Environment:** Docker **daemon now running** (engine 25.0.3, `docker run --rm alpine echo` succeeded). Supabase CLI still resolves only the `AuraVibe` project — no management access to the Eryndex project.

---

## 1. What the repository actually contains

| Layer | Present | Notes |
|---|---|---|
| `contracts/` | yes | provider contract, models, env guard, hashing |
| `integrations/` | yes | base machinery + crawl4ai / gpt_researcher / opengtm / supabase (auth, client, qualification repo) |
| `intelligence/` | yes | `ipp/` engine (rubric, models, engine) — 45 tests |
| `orchestration/` | yes | gates G1–G4, idempotency, stages |
| `discovery/` | **no** | this increment creates it |
| `outreach/` | **no** | drafting service not built; only DB policies exist |
| `supabase/migrations/pending/` | yes | `20261113_hardening_batch1.sql`, unapplied |
| Docs | yes | `docs/architecture/` (target arch, contracts, ADRs 0001–0006), `reports/eryndex/` (baseline, progress, calibration, security, integration verification), `docs/brand/portfolio-analysis.md` |

## 2. Discrepancies found against Directive 02's own description

| Directive claim | Actual | Handling |
|---|---|---|
| "Discovery, Qualification, and Outreach implementations" to inspect | Qualification exists; **Discovery and Outreach do not exist** | Discovery built in this increment; Outreach gate path next |
| "Docker is reported ready" | True now — it was down at Phase 0 | live provider work becomes possible |
| "Owner has Owner access to this Development project" | True for the owner, but **this machine's CLI is authenticated to a different account** (`AuraVibe` only); no DB password, no `DATABASE_URL`, no `.pgpass` | DDL still blocked here; exact requirement stated in §4 |
| Test count 146 | Confirmed exactly | — |

## 3. Reconciliation of the security findings (validated against SQL, not reports)

All five were re-checked against `crm/schema.sql` line by line in this session:

| Finding | Verdict | Anchor |
|---|---|---|
| Suppression write path broken | **Confirmed** — SECURITY INVOKER triggers insert into a table where `authenticated` has `SELECT` only | L861-914 vs L2070/L2086 |
| Agent task rewind/cancel | **Confirmed** — governance function has no transition whitelist | L1494-1566 |
| Outreach without qualification | **Confirmed** — policy requires a task, never a scorecard | L1977-1999 |
| Duplicate approved scorecards | **Confirmed** — no partial unique index | L1695 |
| Domain re-parenting | **Confirmed** — `ON CONFLICT (domain) DO UPDATE SET company_id = EXCLUDED.company_id` | L844-848 |

Two additional issues were confirmed while checking: composite FKs at L725/L729 would null a `NOT NULL` column on delete, and `config.toml` seeds `./seed.sql` which does not exist. Both remain open and documented.

## 4. Development access — exact blocker and least-privilege options

**Blocked operation:** applying `supabase/migrations/pending/20261113_hardening_batch1.sql` and creating `discovery_candidates`.

**What is missing (precisely):** a credential that can issue DDL against `vyfqmrdznmocnmzyjjcj`. Three independent ways exist, in ascending order of exposure:

1. **Supabase Dashboard → SQL editor** (owner-only, no credential leaves the browser). Paste the migration, run, verify with the queries in `reports/eryndex/security-findings.md` §3. **Least privilege, recommended.**
2. **Project database password** + `supabase link --project-ref vyfqmrdznmocnmzyjjcj` then `supabase db push`. Note: a personal access token alone does **not** grant direct Postgres access; the CLI needs the DB password (`supabase/.temp/pooler-url` holds no usable password).
3. **A PAT scoped to the org that owns the project**, added to this machine's CLI (`supabase login` as the owner). Broadest; gives management access to every project in the org.

No secret should be pasted into chat. Options 1 or 2 keep the blast radius to one database.

**Everything else in this increment works without DDL**: discovery logic, identity/dedupe rules, briefs, scoring bridge, SQL static checks, and the DB test suite that will run the moment a `DATABASE_URL` exists.

## 5. Working-tree and migration state

- Tree clean at `28bbba3`; nothing uncommitted.
- `supabase/migrations/` contains exactly one applied migration (`20261107174129_core_crm_schema.sql`).
- `supabase/migrations/pending/` contains the hardening batch + a README that forbids moving it into the applied directory before review.
- No schema change has been executed at any point in this session; Production has never been contacted.

## 6. What this increment will deliver

- **Phase B:** static SQL contract tests, a runnable DB test suite (skipped without a connection), and an owner-facing migration review.
- **Phase C:** the Eryndex-owned Discovery foundation — identity normalization, dedupe, domain-ownership guards, staging ports, IPP-anchored research briefs — with the OpenGTM route still deliberately unconfigured.
- **Phase D (partial):** discovery → IPP bridge producing a draft scorecard from a candidate's evidence package.
