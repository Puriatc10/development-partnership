# Database Readiness — Development `vyfqmrdznmocnmzyjjcj`

**Date:** 2026-11-14 (local `15:35:27`, UTC `12:05:27Z`) · **Branch:** `feat/eryndex-platform-transform` @ `1c31d43`
**Question:** can `tests/db/test_security_fixes_db.py` (6 tests) be enabled safely yet?
**Short answer:** not yet. Connectivity exists *only* through the Supabase CLI's ephemeral login role; there is no DSN a `psycopg` process can use, the hardening migrations are **not applied**, and DDL permission is **unproven** — nothing here measures it.

**What this report did not do.** No `supabase db push` in any form (dry-run included), no SQL execution (`supabase db query`, `psql`, `psycopg` were all left unused), no DDL/DML, no Auth users created, no destructive operation, no secret read or printed. Every CLI/network call was wrapped in `timeout 90` (two `db dump` attempts at 120 s). No command targeted Production `vqnamszywsuqjxuqseas`.

---

## 1. Capability matrix — five separate things, measured separately

A successful `projects list` proves authentication. It proves nothing about linking, connectivity, or DDL rights.

| # | Capability | Exact command | Evidence (verbatim, secrets removed) | Confidence |
|---|---|---|---|---|
| 1 | **CLI authentication** | `timeout 90 supabase projects list` | `{"projects":[{"id":"vqnamszywsuqjxuqseas",…,"name":"development-partnership","region":"ap-northeast-1",…,"linked":false},{"id":"vyfqmrdznmocnmzyjjcj",…,"name":"Puriatc10's Project","region":"ap-southeast-2",…,"linked":true}],"message":""}` — an authenticated Management-API read of two projects. `supabase orgs list` → `{"organizations":[{"id":"ktdpfeytpkqnggejhscb","slug":"ktdpfeytpkqnggejhscb","name":"Puriatc10's Org"}],"message":""}` | **HIGH.** Authentication works now. Credential *storage location* not determined (no file at `~/.supabase/access-token` or `~/AppData/Roaming/supabase/access-token`; CLI v3.0.0 keeps it in its own profile store). Irrelevant to the decision. |
| 2 | **Project visibility** | `timeout 90 supabase projects list` | Both refs returned with org, region, Postgres version (`17.11.0.018` Production, `17.11.0.003` Development), status `ACTIVE_HEALTHY`, and the `linked` flag per project | **HIGH.** Both projects are visible; only Development reports `linked:true`. |
| 3 | **Project linking** | `timeout 90 supabase migration list --remote` (plus the two temp files) | `Target: project Puriatc10's Project (vyfqmrdznmocnmzyjjcj) · production` and `"target":{"kind":"linked","project_ref":"vyfqmrdznmocnmzyjjcj","project_name":"Puriatc10's Project","environment":"production"}`. `supabase/.temp/linked-project.json:1` → `{"ref":"vyfqmrdznmocnmzyjjcj","name":"Puriatc10's Project","organization_id":"ktdpfeytpkqnggejhscb","organization_slug":"ktdpfeytpkqnggejhscb"}`. `supabase/.temp/project-ref:1` → `vyfqmrdznmocnmzyjjcj` | **HIGH.** Direction of linking is Development. **Trap:** the CLI prints `· production` / `"environment":"production"` for *this* project — that is Supabase's environment classification of the project, **not** the Production project. Logs that read "production" here point at `vyfqmrdznmocnmzyjjcj`. |
| 4 | **Postgres connectivity** | `supabase migration list --remote`; `supabase inspect db table-stats --linked` ×4; `supabase db advisors --linked --type security`; `supabase db dump --linked --schema public` ×3 | 5/5 successes on the CLI's normal Postgres path, each preceded by `Initialising login role...` and returning real catalog data (e.g. `{"name":"public.companies","total_size":"136 kB","estimated_row_count":"1"}`, `research_findings` `13` rows). `db advisors` succeeded (control plane, no Postgres dial). `db dump` failed 3/3 — see §1.1 | **MEDIUM-HIGH** that Postgres is reachable *now, through the CLI's temporary `cli_login_postgres` role*. **ZERO** confidence that it is reachable by a durable client: no `psycopg`/`psql` connection has ever been demonstrated, because no DSN exists (§3). |
| 5 | **DDL permission** | *(none executed — forbidden)* | No `CREATE`/`ALTER`/`DROP`/`GRANT` reached the server. `cli_login_postgres` *suggests* elevated rights; a role name is not a measurement | **UNPROVEN.** See §4 for what would settle it and why it is unreachable today. |

### 1.1 Connectivity attempt log (verbatim, no secrets)

**This increment — Postgres path: 5 successes, 0 failures.**

| # | Command | Exit | Result line |
|---|---|---|---|
| 1 | `timeout 90 supabase migration list --remote` | 0 | `{"migrations":[{"local":"20261107174129","remote":"20261107174129","time":"2026-11-07 17:41:29"}],"message":"Migrations listed",…}` |
| 2–5 | `timeout 90 supabase inspect db table-stats --linked` | 0 | `{"rows":[…14 tables…],"message":"inspect db table-stats",…}` — identical payload each time, timestamps `15:25:45`, `15:26:04`, `15:26:27`, `15:26:49` |
| 6 | `timeout 90 supabase db advisors --linked --type security` | 0 | `{"results":[{"name":"function_search_path_mutable",…}],"message":"db advisors",…}` — Management API, no Postgres dial, not counted as a connectivity sample |

**This increment — `db dump` path: 0 successes, 3 failures.** All three reached the pooler (the banner `Dumping schemas from remote database...` prints only after the connection is established) and then produced nothing:

- attempt A (`timeout 120 … -f C:/…/remote_schema.sql`): exit 2, `{"code":"DbDumpOpenFileError","message":"failed to open dump file: NotFound: FileSystem.writeFile (C:\\tmp\\eryndex-dbprobe\\remote_schema.sql)"}` — my path bug (MSYS `/c/...` rewritten to `C:\tmp`); not a connectivity result.
- attempt B (`timeout 120 … > out.sql`): exit **124**, output truncated at `Dumping schemas from remote database...`, file size `0`.
- attempt C (`timeout 90 … > out.sql`, native path): exit **124**, last line `Dumping schemas from remote database...`, file size `220` bytes containing only the three banner lines.

Interpretation: the connection is fine, `pg_dump`-style session usage through the pooler is not. No error *text* was produced by these hangs — the process simply stopped emitting.

**Cumulative, with provenance.** Prior increments recorded **6/8** (`reports/eryndex/supabase-access-matrix.md:195`: "**3/3 this increment; 6/8 (75%) cumulative** with the prior increment (3/5, two failures: `Connection terminated unexpectedly`, `Connection timed out` — pooler-level, before authentication completed)"). The earlier 3/5 is `reports/eryndex/migration-readiness.md:14`. Those two failure strings are **quoted from the recorded reports, not reproduced by me this session** — my five Postgres-path attempts all succeeded. Cumulative: **11/13 (85%)**. Treat connectivity as available but not dependable; a single success does not guarantee the next statement survives.

---

## 2. Identity verification

| Fact | Value | Source |
|---|---|---|
| Organization | `ktdpfeytpkqnggejhscb` = "Puriatc10's Org" | `supabase orgs list` (live) |
| Development ref | `vyfqmrdznmocnmzyjjcj` — "Puriatc10's Project", `ap-southeast-2`, Postgres `17.11.0.003`, `ACTIVE_HEALTHY` | `supabase projects list`; `supabase/.temp/postgres-version:1` |
| Production ref | `vqnamszywsuqjxuqseas` — "development-partnership", `ap-northeast-1`, Postgres `17.11.0.018`, `ACTIVE_HEALTHY` | `supabase projects list` |
| Linked project | **Development only**: `"linked":true` on `vyfqmrdznmocnmzyjjcj`, `"linked":false` on `vqnamszywsuqjxuqseas` | `supabase projects list` |
| `supabase/.temp/linked-project.json:1` | `{"ref":"vyfqmrdznmocnmzyjjcj","name":"Puriatc10's Project","organization_id":"ktdpfeytpkqnggejhscb","organization_slug":"ktdpfeytpkqnggejhscb"}` | file read |
| `supabase/.temp/project-ref:1` | `vyfqmrdznmocnmzyjjcj` | file read |
| Remote migration history | `20261107174129` only (local = remote, one row) | `supabase migration list --remote` |

**No command in this assessment targeted Production.** Every `--linked`/`--remote` invocation resolved through `linked-project.json` to `vyfqmrdznmocnmzyjjcj`, and each printed that ref in its `target` block. Nothing was passed `--project-ref`, `--db-url`, or `--remote=<ref>`, so there was no code path that could have addressed `vqnamszywsuqjxuqseas`.

**Naming hazard (both directions).** `supabase/config.toml:5` reads `project_id = "development-partnership"` — the *Production project's name*, used as the local-stack identifier. And the CLI labels the Development project `environment: "production"`. Two independent ways for a reader to believe an operation hit Production when it hit Development, or vice versa. Neither affects the actual routing (that comes from `linked-project.json`), but both will mislead a reviewer.

---

## 3. Connection inventory — every legitimate source of a DSN

| Source | Present? | Detail |
|---|---|---|
| `ERYNDEX_DB_URL` (process env) | **NO** | unset in this shell. This is the only variable the test module reads — `tests/db/test_security_fixes_db.py:23` |
| `DATABASE_URL` (process env) | **NO** | unset |
| `SUPABASE_DB_URL` (process env) | **NO** | unset |
| `.env` | **NO DSN** | 13 lines, 8 keys: `SUPABASE_URL` (:2), `SUPABASE_PROJECT_REF` (:3), `SUPABASE_ANON_KEY` (:4), `RESEARCHER_EMAIL` (:7), `RESEARCHER_PASSWORD` (:8), `RESEARCHER_AGENT_ID` (:9), `OPERATOR_EMAIL` (:12), `OPERATOR_PASSWORD` (:13). Keys only — values were never printed. Zero matches for `DATABASE_URL`, `DB_URL`, `POOLER`, `POSTGRES_PASSWORD`. `SUPABASE_PROJECT_REF` equals the Development ref (checked as a boolean, not printed); the Production ref does not appear anywhere in `.env`. |
| `.env.example` | **NO DSN** | same 8 keys, no placeholder connection string |
| `supabase/.temp/pooler-url:1` | **PARTIAL** | `postgresql://postgres.vyfqmrdznmocnmzyjjcj@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres` — username present (and it names Development), **password segment absent**. Structurally incomplete: `psycopg` cannot connect with it. |
| `supabase/.temp/` secret files | **NONE** | directory holds only `cli-latest`, `gotrue-version`, `linked-project.json`, `pooler-url`, `postgres-version`, `project-ref`, `rest-version`, `storage-migration`, `storage-version`. No file matching `pass|secret|token`. |
| CLI temporary role `cli_login_postgres` | **EPHEMERAL** | minted per invocation; see below |

### Why the CLI's temporary role cannot become a DSN

1. It is created by the CLI at command start — every Postgres-touching command prints `Initialising login role...` before doing anything — and it exists for the lifetime of that process.
2. Its password is generated by the CLI and **never written to disk**: no credential file exists under `supabase/.temp/` (verified above), and `pooler-url` carries a username with no password segment.
3. Capturing it would mean intercepting the CLI's own handshake — i.e. extracting a credential, which this assessment is forbidden to do and which would still yield a value that dies with the process.
4. It is not reusable even in principle for `pytest`: the 6 tests hold one module-scoped connection across all six tests (`test_security_fixes_db.py:33-40`) and rely on transaction-local state (`set local role`, `set_config(..., true)` at `:54`, `:58-64`). A per-invocation role cannot serve a 30-second multi-test session.

**Net:** connectivity exists, but *only through the CLI*. Every DSN consumer — `psql`, `psycopg`, `python -m pytest tests/db` — is still blocked. This is a credential gap, not a network or permissions gap.

---

## 4. Can DDL permission be verified WITHOUT executing DDL?

Enumerated honestly. "Settles it?" is judged against what DDL actually needs here: `CREATE OR REPLACE FUNCTION`, `CREATE TRIGGER`, `CREATE UNIQUE INDEX`, `DROP POLICY`/`CREATE POLICY` — i.e. **ownership of the existing objects**, not merely schema `CREATE`.

| Non-destructive evidence | Would it settle it? | Reachable from a read-only connection today? |
|---|---|---|
| `select rolsuper, rolcreatedb, rolcreaterole, rolbypassrls from pg_roles where rolname = current_user` | **Partly.** `rolsuper` would be decisive; `rolcreatedb`/`rolcreaterole` are suggestive but not equivalent to owning `public` objects | **No.** Needs a live session. No DSN exists (§3); also it is SQL execution, excluded from this task |
| `select has_schema_privilege(current_user,'public','CREATE')` + object ownership (`pg_class.relowner`, `pg_proc.proowner`, `pg_namespace.nspowner` vs `current_user`) | **Yes** — this is the actual question. Schema `CREATE` alone is insufficient; the migrations replace existing functions and policies, so ownership (or superuser) is what matters | **No.** Same two blockers |
| `supabase db push --dry-run` | **No.** It prints a plan; it applies nothing and therefore proves nothing about grants. (Prior increments ran it; this one did not — forbidden.) | Forbidden, and would not answer it anyway |
| `supabase db query "select …"` — help text: `query  Execute a SQL query against the database  [local or remote]` | **Yes.** This is the one instrument that would run the two SELECTs above as the CLI's `cli_login_postgres` role, which is exactly the role whose rights are in question | **Technically yes, deliberately not used.** It is SQL execution, which this task excludes. Recorded as the first action of the next increment, subject to owner approval |
| `supabase db dump --linked --schema public` | **No.** `pg_dump` proves catalog `SELECT`, not `CREATE` | Attempted, unavailable: 3/3 failed (§1.1) — the pooler does not support `pg_dump`-style session usage |
| `supabase db advisors --linked` | **No.** Control-plane (Management API), privileged service path — says nothing about the CLI's Postgres role | **Yes, ran successfully.** Useful for policy lint, silent on grants |
| `supabase db diff` | **No** — measures schema drift against migrations, and needs a local shadow database | **No.** Local stack is down: `failed to connect to postgres: … dial error (connect ECONNREFUSED 127.0.0.1:54322)` |

**Conclusion: DDL permission remains unproven.** The only today-reachable instrument that would settle it is `supabase db query` with two read-only `SELECT`s, and this task's rules place SQL execution out of scope. I am not going to launder an inference ("the CLI mints `cli_login_postgres` specifically so `db push` can apply migrations") into a measurement. Until someone runs that query or a push succeeds, no statement of the form "we can apply migrations" is supported.

**What the read-only evidence *does* establish, and it is useful:** the hardening migrations are **not applied**. `supabase db advisors --linked --type security` reports `function_search_path_mutable` for `public.fn_sync_contact_suppression` and `public.fn_sync_company_suppression` — the pending batch fixes exactly this by adding `SET search_path = public, pg_temp` (`supabase/migrations/pending/20261113_hardening_batch1.sql:38-41`, `:67-70`). And `fn_enforce_agent_task_transitions` — created only by that batch (`:104-128`) — does not appear in the lint output at all, i.e. it is absent from the deployed schema. Combined with remote history showing only `20261107174129`, that is **high-confidence** for H-01 and H-02 being unfixed on the server. H-03/H-04/H-05 are **medium** confidence (no direct observation beyond history + the pending/ location; the reviewers' notes in `supabase/migrations/pending/README.md` agree).

---

## 5. The six DB tests — what each would do *today*

Today, with no DSN: the module is skipped wholesale. Verified by running it:

```
$ .venv/Scripts/python.exe -m pytest tests/db -q
ssssss                                                                   [100%]
```

6 skipped, 0 failed, exit 0. The skip comes from `pytestmark = pytest.mark.skipif(not DB_URL, …)` at `tests/db/test_security_fixes_db.py:27-30`; the reason string is `ERYNDEX_DB_URL not set: database policy tests need a Development Postgres connection`.

**A second, latent skip the DSN will not fix.** `psycopg = pytest.importorskip("psycopg")` at `:21` — and `psycopg` is **not declared in `requirements.txt`** (no `psycopg` anywhere in it; `pyproject.toml`/`setup.cfg` absent). It is installed in this venv by hand (`psycopg 3.3.9`, `…\.venv\Lib\site-packages\psycopg\__init__.py`). A venv rebuilt from `requirements.txt` would skip all six at line 21 even with a valid DSN. Two independent skip causes; providing the DSN fixes only one.

Per-test behaviour **with a correct Development DSN and the current, pre-migration schema**. The three error classes used by the assertions all exist in psycopg 3.3.9 (`RaiseException`, `InsufficientPrivilege`, `UniqueViolation` — verified by import).

| Test (`:line`) | Asserts | Today, pre-migration | Why | True information? |
|---|---|---|---|---|
| `test_h01_operator_can_register_suppression` (`:70-84`) | Operator inserts a company, sets `is_suppressed = true`, and `suppression_registry` gains exactly 1 row | **FAIL** (error, `InsufficientPrivilege` / SQLSTATE 42501) | The sync triggers are `SECURITY INVOKER` — `20261107174129_core_crm_schema.sql:893-912` (`fn_sync_company_suppression`, no `SECURITY DEFINER`) and `:860-890` (contact equivalent) — and `authenticated` holds **only `SELECT`** on the ledger: `GRANT SELECT ON suppression_registry, audit_logs TO authenticated` at `:2085`, after `REVOKE ALL ON ALL TABLES … FROM anon, authenticated, public` at `:2069`. The trigger's `INSERT` runs as `authenticated` → denied | **Yes.** A red result here is the finding itself, precisely | 
| `test_h02_agent_cannot_rewind_or_cancel_a_task` (`:90-103`) | An agent cannot move a task `in_progress → pending` (`pytest.raises(RaiseException)`) | **SKIP** if no `in_progress` task is assigned to `hermes-researcher-v1` (`:97-99`), otherwise **FAIL** (`DID NOT RAISE`) | `fn_enforce_agent_task_transitions` / `trg_enforce_agent_task_transitions` exist only in the pending batch (`:104-128`) and are absent from the deployed schema (advisors output, §4) | **Conditional.** A skip is no information; a fail is genuine. Data-dependent |
| `test_h03_drafting_requires_an_approved_qualification` (`:109-129`) | A drafter's `outreach_messages` insert raises `InsufficientPrivilege` | **PASS — for the wrong reason** | Pre-migration policy `agent_insert_outreach_messages` (`:1976-1995`) has no qualification precondition **but does** require an existing task for that agent+company (`EXISTS (SELECT 1 FROM tasks …)` at `:1986-1994`). The test creates no task, so RLS rejects the row → `new row violates row-level security policy` → SQLSTATE 42501 → `InsufficientPrivilege` → the assertion is satisfied without the fix existing | **NO — misleading noise.** A green here is a false positive and would be reported as "H-03 fixed" when H-03 is untouched. This is the single most dangerous cell in the table |
| `test_h04_second_approved_scorecard_is_rejected` (`:135-151`) | Duplicating an approved scorecard raises `UniqueViolation` | **SKIP** (`:139-140`, "no approved scorecard exists to duplicate") | `inspect db table-stats` reports `public.lead_qualifications` `estimated_row_count: "0"`. Even with data it would **FAIL**: the partial unique index is only in the pending batch (`:186-188`); the core migration declares no unique index on `lead_qualifications` | **NO** — structurally cannot run. Reports nothing about H-04 in either direction |
| `test_h05_domain_cannot_be_reparented` (`:157-174`) | Re-registering an existing domain under a new company raises `RaiseException` | **FAIL** (`DID NOT RAISE`) | Pre-migration `fn_sync_company_primary_domain` silently re-parents: `ON CONFLICT (domain) DO UPDATE SET company_id = EXCLUDED.company_id, is_primary = TRUE` at `:840-848` (line 845). The pending version replaces it with `DO NOTHING` + an explicit `RAISE EXCEPTION` (`:198-221`). Data exists to exercise it (`company_domains` `estimated_row_count: "1"`) | **Yes**, assuming the seeded domain row is present |
| `test_refokus_history_is_untouched` (`:180-190`) | Exactly 13 `research_findings` for company `1afd5547-…`, and task `cf41afa8-…` is `completed` with non-null `completed_at` | **expected PASS**, unverified per-company | `research_findings` `estimated_row_count: "13"` matches the expected total. This is a **data snapshot** assertion, not a policy assertion — it fails on Development data drift, not on code defects | **Partially.** Guards a regression, but will generate false alarms the moment the dataset changes |

**Verdict on enabling them before the migrations:** it would produce **1 true failure (H-01), 1 true failure (H-05), 2 uninformative skips (H-02 data-dependent, H-04 no data), 1 false green (H-03), and 1 data snapshot of dubious signal (Refokus)** — plus, per §7 R2, a plausible total failure at the identity guard `:48` before any test body runs. The green on H-03 is actively harmful: it is the exact shape of evidence that gets quoted as "verified" in a closure matrix.

### Recommended safe sequence

1. **Leave the module skipped.** The current skip is correct behaviour, not a defect — `pytest.ini:1-2` sets `testpaths = agents tests`, so `python -m pytest` from the repo root also collects `tests/db`; keep it hermetic until a DSN exists.
2. **Declare the driver:** add `psycopg[binary]` to `requirements.txt` (a repo change, owned by whoever owns the test slice) so the DSN is the *only* remaining skip cause.
3. **Obtain a Development-scoped DSN** per §6, session-mode pooler, verify identity read-only without printing it.
4. **Resolve the identity guard before trusting any result** (§7 R2): confirm what `current_user` returns for that DSN. If it is `postgres`, `dev_confirmed` (`:43-49`) will fail all six and the guard needs a decision from the owner (relax to `current_database()` / `inet_server_addr()`, or use a role whose name carries the ref).
5. **Run once pre-migration and *record it as baseline*, not as a pass.** Expect exactly: H-01 fail, H-05 fail, H-02 skip-or-fail, H-04 skip, H-03 **pass (false)**, Refokus pass. Write those expectations down *before* the run so a green H-03 cannot be mistaken for evidence.
6. **Apply the hardening migrations** (separate, explicitly authorised increment — not this one), then re-run and require H-01/H-02/H-03/H-05 to reproduce their asserted behaviour *for the stated reasons*.
7. **Seed H-04's precondition** (one approved scorecard) so H-04 can leave `skip`; otherwise it stays permanently uninformative.

---

## 6. Owner setup procedure — a Development DSN for the tests, without leaking it

**Variable:** `ERYNDEX_DB_URL` — the only name the module reads (`tests/db/test_security_fixes_db.py:23`).

**Where it must live — and why not `.env`:** the test module imports only `os`, `uuid`, `pytest`, `psycopg` (`:14-21`), there is **no `conftest.py` anywhere in the repo**, and nothing in the pytest path calls a dotenv loader (`contracts/environment.py:48` has one, but no test imports it). So a line in `.env` **will not be seen by `pytest` today**. Put it in:
- a **shell export** for the test session (simplest, safest), or
- a **local secret store** / OS keychain entry injected at run time (best for repeat use), e.g. a file *outside* the repo (`~/.config/eryndex/dev.dsn`, `chmod 600`) that the owner sources before running.

**Never:** in `.env` (it would be silently ignored *and* it is plaintext), in `reports/`, in a commit, in chat, in a shell history line typed inline.

**Getting the string:** Supabase Dashboard → project **Puriatc10's Project** (`vyfqmrdznmocnmzyjjcj`) → *Connect* → **Session pooler** (port `5432`). It will have the shape already recorded in `supabase/.temp/pooler-url`:
`postgresql://postgres.vyfqmrdznmocnmzyjjcj:<password>@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres`.
Session mode (5432), **not** transaction mode (6543): the tests keep one connection across six tests and depend on transaction-local state (`set local role`, `set_config(..., true)`), which transaction-mode pooling does not preserve. Add `?sslmode=require` if the driver demands it.

**Keeping it out of git:** `.gitignore:2-4` already covers `.env`, `.env.*`, `*.local`; `supabase/.gitignore:3` covers `.temp`. The DSN must never be written into a tracked file or a `*.example` template. If a secrets file is used, keep it outside the repository so there is nothing to ignore in the first place.

**Verifying it works without printing it:**

```bash
# 1. presence and length only — never the value
test -n "$ERYNDEX_DB_URL" && echo "set, ${#ERYNDEX_DB_URL} chars" || echo "MISSING"

# 2. right project? substring checks, no echo of the value
case "$ERYNDEX_DB_URL" in
  *vqnamszywsuqjxuqseas*) echo "STOP: points at Production" ;;
  *vyfqmrdznmocnmzyjjcj*) echo "OK: Development ref present" ;;
  *) echo "STOP: no Development ref in the DSN" ;;
esac

# 3. does it actually connect, and as what? prints identity only
.venv/Scripts/python.exe - <<'PY'
import os, psycopg
try:
    with psycopg.connect(os.environ["ERYNDEX_DB_URL"], connect_timeout=15) as c, c.cursor() as cur:
        cur.execute("select current_user, current_database(), inet_server_addr()::text, current_setting('server_version')")
        print(cur.fetchone())
except Exception as e:
    print("connect failed:", type(e).__name__)
PY
```

The heredoc form keeps no command line containing the URL; the exception handler prints only the class name, so a driver error cannot dump the credential into a report.

**Exact pytest invocation:**

```bash
ERYNDEX_DB_URL="$ERYNDEX_DB_URL" .venv/Scripts/python.exe -m pytest tests/db -q -rs
```

`-rs` surfaces the skip reasons (`ERYNDEX_DB_URL not set…` vs `no approved scorecard exists…` vs `no in_progress researcher task…`), which is what tells you whether a run produced evidence or nothing. Passing `tests/db` explicitly is deliberate: `pytest.ini:1-2` defaults to `testpaths = agents tests`, so a bare `pytest` also collects the agent suite. Single-test form: `… -m pytest tests/db -q -k h01`.

**Scoping:** the DSN must be the Development project `vyfqmrdznmocnmzyjjcj`, no exceptions. The test file carries two guards for the case where it is not — `:35-36` and `:43-49` — but see §7 R2 before relying on them.

---

## 7. Risks and unknowns

**R1 — A Production DSN would be mostly, not entirely, harmless.** Every write in the six tests sits inside a non-autocommit transaction that is explicitly rolled back (`:84`, `:103`, `:129`, `:151`, `:174`, and `connection.rollback()` in the fixture at `:40`), so rows would not persist. Two things do not roll back: **sequences** (`nextval` is non-transactional), so inserted rows would leave permanent ID gaps on Production, and **the attempt itself** — RLS/privilege probing against the live customer database, plus WAL. Today the thing that stops this is the ref guard: `pytest.fail("Refusing to run: ERYNDEX_DB_URL points at Production")` at `:35-36`, evaluated **before** `psycopg.connect` at `:37`. It is a substring test on the URL, so it catches any string containing `vqnamszywsuqjxuqseas` — the pooler username carries the ref, so the normal DSN forms are caught. It would **not** catch a DSN that reaches Production without the ref in it: an SSH tunnel to `127.0.0.1`, an IP literal, a DNS alias, a CNAME. The second guard (`:43-49`) then asks the *server* who it is, which is the right idea — but see R3.

**R2 — The identity guard may be unsatisfiable as written.** `dev_confirmed` asserts `DEV_REF in current_user` (`:46-48`). Per Supabase's connection docs, the shared-pooler username is `postgres.[PROJECT-REF]` while the underlying Postgres role is `postgres`; a direct connection uses `postgres` too. If the server reports `current_user = 'postgres'` — which I believe is the case but **could not verify without a session** — then a perfectly correct Development DSN fails at `:48` with `connected as 'postgres', expected the Development project role`, and all six tests error before touching any policy. This must be resolved before the first real run, otherwise the run tells you nothing. It also propagates: `reports/eryndex/migration-approval-packet.md:437` prescribes the same `current_user` check as its P1 wrong-database guard.

**R3 — `pg_dump` is effectively unavailable.** 3/3 failures, 2 of them 90–120 s hangs after a successful connect. Consequences: no schema dump for drift comparison, no logical backup by that route; backups must come from the Dashboard or an owner-supplied DSN with a direct connection. It also means §4's "read the deployed schema" path is closed today.

**R4 — `psycopg` is not a declared dependency**, so "the driver is installed" is a property of this machine, not of the repo.

**R5 — Data-dependent tests.** Development holds roughly 1 company, 1 domain, 1 task, 0 qualifications (per `inspect db table-stats` estimates). H-04 cannot leave `skip` until an approved scorecard exists; H-02 needs a specific `in_progress` researcher task. Skips here are indistinguishable from "nothing was tested" in a summary line, so a "6 tests, 0 failed" headline is not the same as "6 behaviours verified".

**R6 — Naming collisions invite a wrong-conclusion report.** `config.toml:5` `project_id = "development-partnership"` (Production's name) and the CLI printing `environment: "production"` for Development. Any future reader scrolling command output can invert the two projects. Worth renaming the local `project_id` in a separate, deliberate change.

**R7 — Untracked artifact present in the tree.** `git status --porcelain` reports `?? reports/eryndex/migration-approval-packet.md` (not created by this task). It carries the same `current_user`-based identity check (R2) and should be reconciled with this report before anyone acts on either.

**Unknowns I did not resolve:** whether `cli_login_postgres` can execute DDL (unmeasurable without a write or an authorised read-only `db query`); what `current_user` the pooler reports (R2); whether the Development data still satisfies the Refokus snapshot exactly.

---

## Appendix — commands run (all wrapped in `timeout`)

| Command | Exit | Purpose / result |
|---|---|---|
| `supabase --version` | 0 | `3.0.0` (update available to `3.1.0`, not installed) |
| `supabase orgs list` | 0 | org `ktdpfeytpkqnggejhscb`, "Puriatc10's Org" |
| `supabase projects list` | 0 | both refs, `linked` flags, PG versions |
| `supabase migration list` (no `--remote`) | 0 | correctly refused: local stack down (`127.0.0.1:54322` ECONNREFUSED) |
| `supabase migration list --remote` | 0 | remote history `20261107174129` |
| `supabase inspect db table-stats --linked` ×4 | 0 / 0 / 0 / 0 | catalog reads; row-count estimates for §5 |
| `supabase db advisors --linked --type security` | 0 | lint: H-01 trigger functions still `search_path`-mutable; `fn_enforce_agent_task_transitions` absent |
| `supabase db dump --linked --schema public` ×3 | 2 / 124 / 124 | unavailable (§1.1) |
| `.venv/Scripts/python.exe -m pytest tests/db -q` | 0 | `ssssss` — 6 skipped, no DSN |
| `git status --porcelain` | 0 | clean except the pre-existing untracked packet (R7); no repo file modified by this task |

---

## 8. Corrections after the independent audit (2026-11-14)

The QA audit found three of the six per-test predictions in §5 wrong, all for the same
reason: the test could not reach the assertion it claimed to make. The tests were fixed
rather than the prose.

| Test | Why the prediction was wrong | Current test |
|---|---|---|
| H-01 | `is_suppressed = true` alone violates `chk_companies_suppression_logic` (`crm/schema.sql` L369-372) with 23514 **before** the AFTER trigger runs — a false red | sets `suppression_reason` + `suppression_date`, then asserts the ledger row exists and that its `source` is the deployed `'manual_blacklist'` |
| H-03 | the insert had no `contact_id` and no task for the drafter, so RLS rejected it for reasons unrelated to H-03's qualification predicate — a false green | skips unless an `in_progress` drafter task and a company-with-contact exist; then asserts the rejection is an RLS violation of `outreach_messages` |
| H-04 | no JWT claims were set, so `fn_enforce_lead_qualification_rules` raised on identity before the partial unique index was ever consulted | sets operator claims before the insert |
| H-05 | it reused the owner's own `primary_domain`, colliding on `companies.primary_domain` UNIQUE (23505) before the trigger — and post-migration could never pass | uses a non-primary `company_domains` row and asserts the domain did not move |

The readiness verdict is unchanged: without `ERYNDEX_DB_URL` the module still skips, and
DDL remains unproven. What changes is that after the migration is applied, a green result
means what the test name says instead of passing for an unrelated reason.
