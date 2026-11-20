# QA audit — Directive 03.2 readiness documents

**Auditor:** QA-03.2 (independent; the four documents under audit were authored by four other agents)
**Date:** 2026-11-14 15:59–16:25 local (12:29–12:55Z) · **Branch:** `feat/eryndex-platform-transform` @ `1c31d43eff026aa0ed23c5d5903a293250417fc5`
**Documents audited**

1. `reports/eryndex/migration-approval-packet.md` (581 L)
2. `reports/eryndex/outreach-schema-hardening-proposal.md` (636 L)
3. `reports/eryndex/database-readiness.md` (233 L)
4. `reports/eryndex/docker-restart-plan.md` (928 L)

**Mandate:** falsify. Every claim that can be re-derived from a file was re-derived; every claim that needed a live server was either reproduced or marked unmeasurable.

**Constraints honoured.** No Docker restart, no container/image/volume/network stop/start/prune/rm, no `wsl --shutdown`, no image pull, no `supabase db push` in any form, no SQL executed, nothing directed at Production `vqnamszywsuqjxuqseas`, no credential printed. Every `docker`/`supabase` call wrapped in `timeout`. Only this file was written.

---

## 1. Repository state, measured

| Fact | Measured | Method |
|---|---|---|
| Branch | `feat/eryndex-platform-transform` | `git rev-parse --abbrev-ref HEAD` |
| HEAD | `1c31d43eff026aa0ed23c5d5903a293250417fc5` | `git rev-parse HEAD` |
| Tracked tree | **clean** — no modified, staged, or deleted paths | `git status --porcelain --untracked-files=no` (empty) |
| Delta vs HEAD | exactly the four audit subjects, all untracked | `git status --short` |
| Tests | **336 collected / 330 passed / 6 skipped / 0 failed / 0 errors**, 7.99 s, rc 0 | `python -m pytest` (summary line present) |
| Collection | 336 = 6+14+6+5+11+29+8+10+7+3+45+134+27+21+10 per file | `python -m pytest --collect-only -q` |
| `tests/db` | 6 skipped, no DSN | same run |
| `hardening_batch1.sql` | 224 L, sha256 `57f3b233…421246` | `sha256sum` — **matches the packet exactly** |
| `discovery_candidates.sql` | 165 L, sha256 `d3c8544e…d9a71d` | `sha256sum` — **matches the packet exactly** |
| `supabase/migrations/` | one file, `20261107174129_core_crm_schema.sql` (2089 L) | `ls` |
| `crm/schema.sql` | 2090 L — a **one-line offset** vs the applied migration (BOM/header), so `crm/schema.sql:2086` ≡ `20261107174129:2085` | `diff`, `grep -n` |

**Note on the offset.** The packet cites `crm/schema.sql`; the readiness report cites the applied migration. Both are correct for their own file. Every citation I checked resolved to the right object once the offset is applied. No citation defect found on this axis.

**No file outside the four reports was modified** — confirmed by the empty tracked diff.

---

## 2. Verdicts

| Document | Verdict | One-line reason |
|---|---|---|
| `migration-approval-packet.md` | **OVERSTATED** | The SQL survives scrutiny — 8 statements, atomic, non-destructive, every object exists — but two lock claims are **factually wrong** (§6.1 rows 5–6, §6.1 closing sentence) and the "two behavioural changes" the owner must accept is really three. |
| `outreach-schema-hardening-proposal.md` | **CONFIRMED, one defect** | Every proposed DDL statement is legal and the four technical questions I was asked to falsify all check out. One CHECK constraint contradicts its own stated rationale and permits a recipient-less `email` draft. |
| `database-readiness.md` | **PARTIALLY CONTRADICTED** | Its central insight (H-03 passes for the wrong reason) is **correct and important**. Its H-01 and H-05 per-test predictions are wrong, and its "1 true failure (H-01)" is actually a false red. |
| `docker-restart-plan.md` | **CONFIRMED** | Daemon-wedged reproduced 4/4 now (and has been wedged ~4 h). Volume claim independently corroborated from its own captured `inspect` dump. Memory/disk numbers have drifted but every directional claim holds. |

---

## 3. Findings

Severity: **B** = blocks a truthful claim · **H** = makes a stated verification wrong · **M** = materially incomplete/incorrect · **L** = cosmetic or stale.

| ID | Sev | Doc | Claim under test | What I measured | Evidence |
|---|---|---|---|---|---|
| **F-01** | **H** | readiness | `test_h01_operator_can_register_suppression` would fail pre-migration with `42501`, and that red *is* the finding | **It cannot reach the trigger.** The test does `update companies set is_suppressed = true` and nothing else. `chk_companies_suppression_logic` requires `suppression_reason IS NOT NULL AND suppression_date IS NOT NULL` whenever `is_suppressed = TRUE`. No trigger sets either column. CHECK constraints are enforced before AFTER-row triggers fire ⇒ **23514 `CheckViolation`**, not 42501. It fails identically **before and after** the migration. | `tests/db/test_security_fixes_db.py:81`; `crm/schema.sql:369-372` (no writer of `suppression_date` anywhere: `grep -n suppression_date` → only 360/370/371); `crm/schema.sql:911-914` |
| **F-02** | **H** | readiness | H-05's test fails pre-migration with `DID NOT RAISE`, and would be true information | **It dies one statement before its assertion, in both states.** The new company is inserted at `:167` with `primary_domain = <an existing domain>`. `companies.primary_domain` is `UNIQUE` ⇒ 23505 on the insert itself (uncaught). If the sampled row happens to be an *alternate* domain, the AFTER trigger fires instead: pre-migration it silently re-parents, post-migration it raises `Duplicate domain` — either way **outside** the `pytest.raises` at `:172`. Post-migration this test can never pass. | `tests/db/test_security_fixes_db.py:159-173`; `crm/schema.sql:340`, `:852-855`; `20261113_hardening_batch1.sql:198-215` |
| **F-03** | **H** | readiness | H-03 "**PASS — for the wrong reason**" pre-migration | **CONFIRMED, and worse than stated.** The mechanism is exactly right (see §4), but the test's INSERT omits `contact_id` and `personalization_rationale`, both `NOT NULL`, so even with a task *and* an approved qualification it could never succeed — there is no positive control anywhere. | `tests/db/test_security_fixes_db.py:123-128`; `crm/schema.sql:600`, `:606` |
| **F-04** | **M** | readiness | H-04 "SKIP; even with data it would FAIL" on the unique index | **It would fail on the approval gate first.** The test seeds no JWT claims, so `is_human_operator()` is false and `fn_enforce_lead_qual_rules` raises *"Only authenticated human operators can approve lead qualifications"* before any index is consulted. Post-migration the test still raises, so it can never certify H-04. | `tests/db/test_security_fixes_db.py:135-150` (no `_claims`); `crm/schema.sql:1354-1369`, `:63-71` |
| **F-05** | **M** | packet | `DROP POLICY` / `CREATE POLICY` take **SHARE ROW EXCLUSIVE**; "No statement in this file takes `ACCESS EXCLUSIVE`" | **Both are `ACCESS EXCLUSIVE`.** Verified in the PostgreSQL 17 source, not from memory: `CreatePolicy` opens the table `AccessExclusiveLock`, `RemovePolicyById` likewise. `CREATE TRIGGER` (SHARE ROW EXCLUSIVE) and `CREATE UNIQUE INDEX` non-concurrent (**SHARE**) are correct as stated. Consequence the owner was not told: the batch also blocks **reads** on `outreach_messages`, so with `lock_timeout = '5s'` a single concurrent `SELECT` can abort the batch. | `migration-approval-packet.md:275-276`, `:279`; `postgres/REL_17_STABLE src/backend/commands/policy.c:648` (`CreatePolicy`), `:368` (`RemovePolicyById`); `trigger.c:211-213`; `indexcmds.c` (`lockmode = concurrent ? ShareUpdateExclusive : ShareLock`) |
| **F-06** | **M** | packet | "Accept **two** behavioural changes in the operator's day-to-day" | **There is a third, and it is larger.** H-03 admits drafts only when `lifecycle_stage IN ('qualified','outreach_queued','in_outreach')`. Any company at `active_conversation`, `negotiating`, `partner_active`, `partner_dormant`, `churned`, `identified` or `researching` becomes **undraftable by any agent** — including sequence follow-ups (`outreach_messages.sequence_step`, `parent_message_id` exist precisely for that). Operators retain access via `human_operator_all_outreach_messages`, so it is a capability loss, not a lockout. The packet never mentions these stages. | `20261113_hardening_batch1.sql:171-175`; `crm/schema.sql:153-165`, `:620-621`, `:1784-1785`; `migration-approval-packet.md:578` |
| **F-07** | **M** | proposal | "a person-addressed channel cannot be recipient-less … it is wrong in the safe direction (it refuses rather than permits)" | **It permits.** The CHECK is `contact_id IS NOT NULL OR channel IN ('contact_form', 'email')` — so a `contact_id IS NULL` row with `channel = 'email'` is **legal**, and the table carries no address column at all, so such a draft has nowhere to be sent. The prose (§4.1: only `contact_form` can address a company without a person) and the DDL disagree. Correct form: `contact_id IS NOT NULL OR channel = 'contact_form'`. | `outreach-schema-hardening-proposal.md:382-384` vs `:344`, `:389`; `crm/schema.sql:596-646` (no email/address column) |
| **F-08** | **L** | packet | "this environment suppresses the summary line" (explaining why 330/6 had to be read from progress dots) | It suppresses nothing. `pytest.ini` sets `addopts = -q`, and the packet ran `-q` as well — **double quiet** drops the summary. `python -m pytest` (single quiet) prints `330 passed, 6 skipped in 7.99s`. The count is right; the explanation is not. | `pytest.ini:5`; `migration-approval-packet.md:45` |
| **F-09** | **L** | readiness | R7 "clean except the pre-existing untracked packet"; R1 "inserted rows would leave permanent ID gaps" | R7 is stale: **four** untracked reports now exist. R1's mechanism is off: every inserted row uses `gen_random_uuid()`, so there are no ID gaps; the only IDENTITY column is `audit_logs.id` (L784), which the H-01 path would advance through `trg_audit_suppression`. The caution is right; the stated mechanism is wrong. | `database-readiness.md:214`, `:202`; `crm/schema.sql:784`, `:1673-1675` |
| **F-10** | **L** | packet | §2 "0 rows read or written at apply time" | Its own §6.2 correctly says the index build scans `lead_qualifications`. Internal inconsistency; harmless at 0 rows. | `migration-approval-packet.md:81` vs `:288` |

### 3.1 Checks that passed (recorded so they are not re-litigated)

- **8-statement inventory: correct.** Ten semicolons, two of them `BEGIN`/`COMMIT`; the eight substantive statements are the four `CREATE OR REPLACE FUNCTION`s, `CREATE TRIGGER`, `DROP POLICY`, `CREATE POLICY`, `CREATE UNIQUE INDEX`. No top-level DML — every `INSERT`/`UPDATE` sits inside `$$ … $$`.
- **Trigger-name collision: none.** `trg_enforce_agent_task_transitions` / `fn_enforce_agent_task_transitions` appear nowhere in `crm/schema.sql` or the applied migration.
- **Helper functions exist under exactly those names:** `is_agent()` (`schema.sql:83`), `is_agent_type(text)` (`:93`), `current_agent_identity()` (`:104`), `is_human_operator()` (`:63`). All three policies H-03's sub-selects depend on exist: `agent_select_assigned_tasks` (`:2046`), `agent_select_lead_qualifications` (`:1953`), `agent_select_companies` (`:1810`).
- **Trigger fire order: correct.** Same-timing triggers fire in `tgname` ASCII order; `trg_enforce_agent_…` < `trg_enforce_task_…` < `trg_tasks_…`, and `trg_audit_…` < `trg_sync_…` on `lead_qualifications`.
- **`ON CONFLICT (identifier) DO NOTHING`:** `identifier` is `TEXT NOT NULL UNIQUE` with no `target_type` in the key (`schema.sql:319`) — the packet's "first writer wins, permanently" is right.
- **H-05 for a company whose domain row belongs to itself:** the new body is behaviourally identical (insert no-ops, `existing_company = NEW.id`, no raise, `is_primary` set). The only change is the cross-company case, which now raises. No regression.
- **Rollback:** per-section statements are correct; re-executing `schema.sql:861-887`, `:894-909`, `:841-850`, `:1977-1999` reverts attribute + body. The claim that `CREATE OR REPLACE` resets `SECURITY DEFINER` and `proconfig` is right.
- **Re-runnability:** the file is single-shot, exactly as claimed — statement #4 (`CREATE TRIGGER`, L125) fails `42710` on a second run and rolls back safely before `DROP POLICY` is reached.
- **Packet's rollback of H-04** correctly warns that plain `DROP INDEX` takes `ACCESS EXCLUSIVE` — which makes F-05 an internal inconsistency, not an oversight of fact.
- **`fn_enforce_outreach_human_approval` does allow `rejected → draft`** (`schema.sql:1084-1086`), so the proposal's §2 premise (a rejected row keeps the slot and can be reworked by `UPDATE`) holds.

---

## 4. The H-03 false-green: analysis holds, fix is larger

**Verdict: the readiness report is right on the mechanism.** PostgreSQL checks RLS `WITH CHECK` **before any other constraint**: "For `INSERT`, `UPDATE` and `MERGE`, `WITH CHECK` expressions are enforced after `BEFORE` triggers are fired … `WITH CHECK` expressions are enforced before any other constraints" (CREATE POLICY, PG 17). The test creates no task, the deployed policy's task `EXISTS` (`schema.sql:1990-1998`) is unsatisfied, so the insert is rejected with `new row violates row-level security policy` → SQLSTATE 42501 → `psycopg.errors.InsufficientPrivilege` → the assertion passes **pre-migration**. A green here proves nothing about H-03. That is a genuine defect and the readiness report deserves credit for it.

**But the test is weaker than the report says, in three ways.**

1. The INSERT at `:123-128` supplies no `contact_id` and no `personalization_rationale`. Both are `NOT NULL` (`schema.sql:600`, `:606`). Because RLS fires first, nobody notices today — but the row is malformed, so **even with a task and an approved qualification the insert cannot succeed**. There is no positive control anywhere in the module.
2. Even after a correct RLS rejection, the module cannot distinguish *which* predicate rejected: all three `EXISTS` clauses and the task clause produce the same 42501. Distinguishing them requires a positive control, not a better assertion on the negative.
3. The company the test picks is chosen for the *absence* of an approved qualification only — nothing guarantees it is in a lifecycle stage where H-03 would otherwise allow the insert, so the lifecycle clause is an uncontrolled variable.

**Exact fix — what the test must set up and assert.**

Setup (as the fixture connection, before switching role):
- company **C**: `lifecycle_stage = 'qualified'`, `is_suppressed = false`, `qualification_tier = 'tier_1_priority'`;
- contact **K** in C (`insert into contacts (company_id, first_name, role_type) values (C, 'Probe', 'other')` — no email needed, keeps the suppression trigger inert);
- campaign **P** (optional; use `NULL` to exercise the NULL-campaign path);
- task **T**: `assigned_actor_type='agent'`, `assigned_actor_identity='hermes-drafter-v1'`, `company_id=C`, `contact_id=K`, `campaign_id=NULL`, `status='in_progress'`;
- **no** `lead_qualifications` row for C.
Then `_as_role(cur,'authenticated')` + `_claims(cur,'agent','hermes-drafter-v1','drafter')`.

Assertions, in this order:
1. **Negative:** the insert — with `contact_id=K`, `personalization_rationale='probe'`, `company_id=C`, `channel='email'`, `approval_status='sending_status'='draft'`, `proposed_commercial_model='project_based_wholesale'` — raises `InsufficientPrivilege`;
2. assert `exc.diag.sqlstate == '42501'` **and** `'row-level security'` in the message (not merely the psycopg class);
3. **Positive control (this is the whole point):** insert an `approved` `lead_qualifications` row for C as an operator (`_claims(cur,'operator')`, `reviewed_by_human_id = auth.uid()`), retry the **identical** insert, and assert it **succeeds** and that `outreach_messages` for C now has exactly 1 row;
4. roll back.

Step 3 is what makes step 1 mean something: with every other predicate demonstrably satisfiable, the only remaining difference is the qualification clause. Steps 1–3 must live in one test so no other test can leave state behind (`conn.rollback()` in a `try/finally`).

The same discipline applies to F-01 and F-02: a test that asserts a rejection must be paired with a positively-verified adjacent insert, or it cannot distinguish "policy refuses" from "row was invalid anyway".

---

## 5. Schema proposal — the four questions I was asked to falsify

| Question | Answer | Evidence |
|---|---|---|
| Does `NULLS NOT DISTINCT` apply to that index shape? | **Yes, and the syntax is right.** The clause sits after the column list and before `WITH`/`WHERE` in the PG grammar; it applies to the whole index. Every other indexed column is `NOT NULL` (`company_id` L599, `channel`/`sequence_step` NOT NULL), so the only nullable key component is `campaign_id` — exactly the column the proposal wants it for. Unique-index inference for `ON CONFLICT (company_id, campaign_id, channel, sequence_step)` still works because it is a column list, not an expression. | `outreach-schema-hardening-proposal.md:86-91`; CREATE INDEX synopsis, PG 17; `crm/schema.sql:596-646` |
| Is the composite FK legal? | **Yes.** `(qualification_id, company_id) → lead_qualifications (id, company_id)` requires a unique constraint on exactly that column list, and the proposal adds it first (`uq_lead_qualifications_id_company`, L191-192). Same for the evidence table's two composite FKs (L223-228) — both referenced pairs get their uniqueness constraint (L194-198). Ordering inside one transaction is fine. | proposal:191-229 |
| Would a nullable `contact_id` break `fk_outreach_contact_company`? | **No.** The FK has no `MATCH` clause ⇒ `MATCH SIMPLE` ⇒ if any referencing column is NULL the row is not checked; with `contact_id` present the composite guarantee is unchanged. The proposal states this correctly. | proposal:346-355; `crm/schema.sql:626-627` |
| Is "an array FK is impossible in Postgres" correct? | **Yes.** `REFERENCES` targets a column or column list; there is no element-wise FK, and `CHECK` cannot read another table. The delete-side argument (a second trigger on `research_findings`, unindexable) is also correct. | proposal:166-168 |

**Would any proposed DDL fail?** No statement fails. `ADD CONSTRAINT … UNIQUE (id, company_id)` on a table whose `id` is already the PK is legal (redundant, as the proposal admits). `ALTER COLUMN … DROP NOT NULL` + `ADD COLUMN` in one `ALTER TABLE` is legal. Rollback order (child table before parents) is correct. `REVOKE` before `GRANT` on the new table is correct and necessary under Supabase's default privileges.

**The one defect is semantic, not syntactic** — F-07: `chk_outreach_recipientless_channel` (`:382-384`) admits `channel='email'` with no contact, which is the exact case the section exists to prevent, and contradicts the proposal's own prose at `:344` and `:389`. As written, a resolver bug that fails to find a contact can legally land an emailless-but-`email`-channel draft with `no_recipient_reason='company_contact_form'`.

---

## 6. Database readiness — what I reproduced

| Claim | Reproduced? | Result |
|---|---|---|
| `supabase projects list` authenticated, both refs, Development `linked:true` | **Yes, byte-for-byte in substance** | rc 0; `vqnamszywsuqjxuqseas` = "development-partnership" `linked:false` PG `17.11.0.018`; `vyfqmrdznmocnmzyjjcj` = "Puriatc10's Project" `linked:true` PG `17.11.0.003` |
| `supabase migration list --remote` reaches Postgres | **Yes** | rc 0, `Initialising login role...`, `{"migrations":[{"local":"20261107174129","remote":"20261107174129",...}]}`, target block `project_ref: vyfqmrdznmocnmzyjjcj`, `environment: "production"` (the trap it documented) |
| "5/5 CLI Postgres-path successes this increment" | **Corroborated in kind, not in count** | The same path works now (1 of the 5 reproduced). The four `inspect db table-stats` runs are not reproducible without re-running them, and their counts are not evidence — they are estimates. Not a defect. |
| "11/13 cumulative" | **Unverifiable** | Both prior failures are quoted from other reports, as the report itself discloses. |
| `supabase config` "if available" | **Exists** | rc 0, subcommands `diff|pull|push`, all remote-scoped and read-only except `push`. Not used by any report. |
| DDL permission **UNPROVEN** | **Confirmed unprovable here** | Distinguishing `cli_login_postgres`'s rights requires a statement; the report refuses to infer. Correct, and the correct call. |
| H-03 passes for the wrong reason | **Confirmed** | §4 above. |
| `psycopg` absent from `requirements.txt`; no `conftest.py` in the repo | **Confirmed** | `grep -i psycopg requirements*.txt pyproject.toml` → nothing; `find . -name conftest.py` (excluding `.venv`) → nothing. |
| `dev_confirmed` may be unsatisfiable (`current_user` = `postgres`) | **Unresolved, and correctly flagged** | Its R2 is the single most likely cause of a six-test mass error on the first real run. Cannot be measured while no DSN exists. |
| H-01 / H-05 per-test predictions | **Contradicted** | F-01, F-02. |
| H-04 prediction | **Incomplete** | F-04. |

**Net effect on the report's own verdict table (`:131`):** "1 true failure (H-01), 1 true failure (H-05), 2 uninformative skips, 1 false green (H-03), 1 data snapshot" should read **"0 discriminating results: 2 false reds (H-01, H-05), 1 false green (H-03), 2 unreachable assertions (H-04, H-05), 1 data-dependent skip (H-02), 1 data snapshot — plus a possible mass error at the identity guard."** The headline ("not yet safe to enable") still stands, and the DDL/credential analysis is the strongest part of the document.

---

## 7. Docker plan — what I could and could not confirm

**Reproduced now (15:59–16:02 local), all with `timeout 20`:**

| Probe | rc | stdout bytes | stderr bytes |
|---|---|---|---|
| `docker version` | **124** | 0 | 0 |
| `docker info` | **124** | 0 | 0 |
| `docker ps` | **124** | 0 | 0 |
| `docker inspect trading_bot_postgres` | **124** | 0 | 0 |

**Daemon wedged: CONFIRMED**, and it has now been wedged for ~4 h (the plan's 16/16 matrix ran at 12:06Z). 4/4 timeouts with zero bytes is the same signature the plan recorded.

> **Measurement trap worth recording.** The brief I was given said `docker ps` and `docker inspect` "returned EMPTY output with exit 0". That is an artefact of `cmd | head`/`| tail`: `$?` after a pipe is the exit status of the *last* command in the pipe, not of `timeout docker …`. Re-run with redirection and the real status is 124. **Anywhere in these reports where an exit code was read after a pipe, the exit code means nothing.** The plan's own probe ledger uses redirection and is therefore sound.

**Volume claim: CONFIRMED, independently.** I did not rely on the prose — I read the plan's captured artefact `C:/Users/puria/AppData/Local/Temp/pginspect.json` (7.9 KB) and found `"Type":"volume"`, `"Name":"crypto-trading-bot_postgres_data"`, `"Source":"/var/lib/docker/volumes/crypto-trading-bot_postgres_data/_data"`, `"Destination":"/var/lib/postgresql/data"`, `"Driver":"local"`, `"Mode":"z"`, `"RW":true`. With `PGDATA` supplied by the image and pointing inside that path, a restart destroys neither the container nor the volume. `docker rm -v` / `volume rm` / `volume prune` / factory reset remain the only destructive paths, and the plan forbids all four.

**"Container unhealthy, FailingStreak 1161": UNMEASURABLE now.** The daemon cannot answer; the only evidence is the plan's own 11:54Z capture. Its arithmetic is internally consistent (1161 × 10 s = 3 h 13 m 30 s before 11:54Z ⇒ onset ≈ 08:41Z, matching `StartedAt 2026-11-13T19:45:34Z`). I neither confirm nor contradict it — I record that it is now second-hand.

**Numbers, re-measured fresh:**

| Quantity | Plan (11:54–12:19Z) | Now (15:59Z) | Direction |
|---|---|---|---|
| Free physical | 131–237 MB / 16,109 MB | **332 MB** / 16,109 MB | same order, slightly better, still far below the plan's own 4 GB threshold |
| Commit | 36,763 / 37,321 MB (558 MB head) | **42,579 / 43,504 MB** (925 MB head) | limit grew ~6 GB (pagefile growth) — commit pressure remains |
| Disk `/c` free | 10.93 GiB (11.7 GB), 98 % | **12 GB**, 98 % (465 G used of 476 G) | ~0.3 GB freer; the "21 GiB → 11 GiB" collapse is not continuing |

The plan's memory and commit figures are **stale, not wrong**: the host is still in the condition the plan diagnosed (three orders of magnitude short of the headroom it requires). Its disk figures are also stale but more favourable.

**What the plan gets right that matters:** `Live Restore=false` ⇒ every restart is a hard stop; `restart: unless-stopped` + unchanged container id ⇒ the container returns without compose; the compose label points at a non-existent `code-workshop` path ⇒ the compose file must not be used to recreate the container. All three are load-bearing for a safe restart and all three are correct.

---

## 8. Secret scan

Scanned all four documents for JWTs (`eyJ…`), `service_role`/`sb_secret` values, `postgres://user:pass@`, bearer tokens, PEM blocks, cloud/API key shapes (`sk-`, `ghp_`, `xox…`), and long base64 runs.

| File | Result |
|---|---|
| `migration-approval-packet.md` | **Clean.** Only sha256 digests of the two migration files (L4, L5, L490). `service_role` appears twice as prose *asserting its absence* (L554-555). |
| `outreach-schema-hardening-proposal.md` | **Clean.** No match on any pattern. |
| `database-readiness.md` | **Clean, one redacted DSN.** `:156` shows `postgresql://postgres.vyfqmrdznmocnmzyjjcj:***@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres` — password already `***`. The username carries the **Development** ref, not Production. `.env` keys are named, never valued (`:70`). |
| `docker-restart-plan.md` | **Clean.** Only container/image digests (L122-123, L768). `POSTGRES_PASSWORD` is listed as an environment *name* only (L364). |
| Repo delta | Only the four reports are new; nothing else added or changed. |

**No secret, token, password, or credentialed DSN appears in any of the four documents or in the repo delta.** The one near-miss (`:156`) is correctly redacted. No action required.

---

## 9. Not knowable in the current host state

1. Whether `trading_bot_postgres` is still `unhealthy`, and its current `FailingStreak` — the daemon cannot answer.
2. Whether Postgres inside that container is serving, hung, or dead (`pg_isready`, `psql`).
3. Whether the data directory on `crypto-trading-bot_postgres_data` is intact — the volume path is known; its contents are not.
4. Whether the rebuilt compose file still matches the running container (only the captured label says otherwise).
5. Whether `cli_login_postgres` can execute DDL — needs a statement.
6. What `current_user` a pooler DSN reports (readiness R2).
7. Row counts in Development (`companies` 1, `contacts` 0, `lead_qualifications` 0, `outreach_messages` 0, `suppression_registry` 1) — the CLI table-stats estimates are the only source and they are estimates; P2/P3/P5 remain unrun.
8. Whether the six DB tests behave as predicted on a real connection — every prediction in §6 is reasoning over source, not a run. **Four of them are wrong as written (F-01…F-04).**
9. Whether the 13-row Refokus snapshot still holds.

---

## 10. What the owner should be told before approving the migration

1. **The SQL itself is fit to apply.** Eight statements, one transaction, atomic, no DML, no table/column/grant changes, every referenced object re-verified against `crm/schema.sql` under its exact name, no trigger-name collision, per-section rollback defined. The READY verdict on the *content* survives this audit.
2. **Approve the two files separately**, as the packet asks. `discovery_candidates.sql` shares nothing with the batch and must not ride on its approval.
3. **Two lock claims in the packet are wrong, and you should apply with that in mind.** `DROP POLICY` + `CREATE POLICY` take `ACCESS EXCLUSIVE` on `outreach_messages`, not `SHARE ROW EXCLUSIVE`; that blocks **reads** too, so keep `lock_timeout = '5s'` and expect a clean abort (not a wait) if any session is reading. At 0 rows the window is sub-second.
4. **H-03 costs more than the packet says.** It also stops *agents* drafting for companies at `active_conversation`, `negotiating`, `partner_active`, `partner_dormant` or `churned` — i.e. no agent-authored follow-ups after a prospect replies. Operators keep access. If follow-ups are in scope, this is a product decision to make now, not a bug to discover later.
5. **Do not accept "H-0x verified" from `tests/db` as written.** H-01 fails on a CHECK constraint before it reaches the trigger, H-05 errors one statement before its assertion, H-04 raises on the approval gate before touching the index, and H-03 passes pre-migration for the wrong reason. Four of six tests cannot discriminate. Fix them (§4) before using them as closure evidence.
6. **Verification on the day must come from the packet's own SQL checks, not the test suite:** P1–P7 before, V1–V9 after, and V6 (the six-boolean single query) is the only authoritative "did it commit" test.
7. **Record the version in the same sitting.** Dashboard/`psql` record nothing; `supabase migration repair --status applied <version>` or the repo will claim a state the database does not have.
8. **The Docker situation is independent of this approval and remains blocking for anyone else's work** — the daemon has been wedged ~4 h and the owner's Postgres container has been unhealthy since ≈08:41Z. The plan's restart is data-preserving by construction, but the restart is an owner action, not something the migration decision covers.

---

## Appendix — commands run (all read-only, all `timeout`-wrapped where network/Docker)

```
git rev-parse --abbrev-ref HEAD; git rev-parse HEAD
git status --short; git status --porcelain --untracked-files=no
python -m pytest                                  → 330 passed, 6 skipped, 7.99s
python -m pytest --collect-only -q                → 336 (summed per file)
sha256sum supabase/migrations/pending/*.sql       → both match the packet
wc -l crm/schema.sql supabase/migrations/*.sql pending/*.sql
df -h /c ; Get-CimInstance Win32_OperatingSystem / Get-Counter Memory\*
timeout 20 docker version|info|ps|inspect         → 124,124,124,124 (0 bytes each)
timeout 90 supabase projects list                 → rc 0
timeout 90 supabase migration list --remote       → rc 0, one version
timeout 60 supabase config                        → rc 0 (help; exists)
grep / sed over crm/schema.sql, the pending files, tests/, all four reports
curl raw.githubusercontent.com postgres REL_17_STABLE:
   src/backend/commands/policy.c   (CreatePolicy:648, RemovePolicyById:368 → AccessExclusiveLock)
   src/backend/commands/trigger.c  (CreateTrigger:211-213 → ShareRowExclusiveLock)
   src/backend/commands/indexcmds.c (non-concurrent DefineIndex → ShareLock)
```

No SQL was executed. No Docker state was changed. No file outside this one was written.
