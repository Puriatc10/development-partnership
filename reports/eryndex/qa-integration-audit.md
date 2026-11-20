# QA & Integration Audit — Directive 03.1

**Date:** 2026-11-14 (IRST, UTC+03:30)
**Auditor:** independent QA/integration auditor. Not the author of `outreach/`, not the author of the adversarial review, not the author of the Crawl4AI probe, not the author of the Supabase matrix.
**Scope audited:** the uncommitted Directive 03.1 increment on `feat/eryndex-platform-transform` @ `c80c44b`.
**Method:** re-measure every claim from source, the working tree and bounded live probes; treat the three self-reports as hypotheses. No repository file was edited by this audit; scratch scripts live in `C:/Users/puria/AppData/Local/Temp/` (`qa_probe.py`, `mutplug.py`). Nothing was staged, committed or pushed. No SQL was applied. Production `vqnamszywsuqjxuqseas` was never contacted.
**Database touched:** none. `supabase db push` was run only with `--dry-run`.
**Docker:** every call wrapped in `timeout`; the single mutating probe was `docker network create eryndex-qa-probe` (10 s budget overrun → killed at 30 s, nothing created). No pull, no prune, no rmi, no restart, no `wsl --shutdown`. `trading_bot_postgres` was read (`docker ps`) and never touched.

---

## 1. Verdict per workstream

| Workstream | Verdict | One-line basis |
|---|---|---|
| **A — Outreach closure + adversarial hardening** | **CONFIRMED** (two numeric nits) | 134/134 tests pass; the two critical attacks are blocked by my own probes, not by theirs; 5 independent guard-removal mutations each produce failures; suppression fails closed on all three attack shapes the brief named. The only errors are a stale full-suite count (§6) and a residual that the report already discloses (§7.4). |
| **B — Crawl4AI live verification** | **OVERSTATED / PARTLY CONTRADICTED** | The daemon write-path wedge **reproduces exactly** (network create hangs, dockerd lease errors 6×, matched to the second). The *secondary blocker* — "Docker Hub 403s this network", "tag/size unconfirmed", "files.pythonhosted DNS fails" — **does not reproduce**: registry answers a normal 401 challenge, an anonymous pull token is issued, `hub.docker.com` returns 200 listing tag `0.9.5`, and the 0.9.5 wheel downloads (494,036 bytes). The verdict `failure_class=docker` survives; the evidence sentence and one owner action do not. |
| **C — Supabase readiness** | **CONFIRMED** | Every number re-measured identically: linked ref `vyfqmrdznmocnmzyjjcj`, remote history is exactly `20261107174129`, `db push --dry-run --remote` → `upToDate:true, migrations:[]`, Production unlinked, and both pending files carry 8-digit prefixes (`20261113_`) and sit in `pending/`, so "0 pending" is invisibility, not convergence. DDL remains unproven and unattempted. |

---

## 2. Working tree — file-by-file, and what is out of scope

At the end of the audit (`15:0x`), `git status --short`:

```
 M docs/architecture/integrations/provider-facts.md
 M reports/eryndex/migration-review-batch1.md
 M supabase/migrations/pending/20261113_hardening_batch1.sql
 M tests/test_security_sql_static.py
?? outreach/  (4 × .py, plus __pycache__ which .gitignore:10 ignores)
?? tests/test_outreach_gate.py
?? reports/eryndex/{crawl4ai-blocker-analysis,outreach-adversarial-review,outreach-workflow,security-closure-matrix,supabase-access-matrix}.md
?? reports/eryndex/evidence/{crawl4ai-live-response.json,crawl4ai-live-verification.json}
```

`git diff --stat` = 1 file / +25 lines at audit start, 4 files at audit end. `git diff --check` → **clean, exit 0** (no whitespace errors, no conflict markers).

| Path | Δ | In declared scope? | Assessment |
|---|---|---|---|
| `docs/architecture/integrations/provider-facts.md` | +25 | ✅ yes | Structure good, two factual sentences contradicted (§9, findings F3/F4) |
| `outreach/{__init__,ports,gate,dispatch}.py` | new, 1,570 lines | ✅ yes | Audited line by line (§5, §7); no transport, no credential, no import edge to `dispatch` |
| `tests/test_outreach_gate.py` | new, 1,431 lines, 134 tests | ✅ yes | Every test name cited in the closure matrix exists; 134/134 pass |
| `reports/eryndex/*.md` (5 new) | new | ✅ yes | Numbers re-derived where falsifiable; see §9, §10 |
| `reports/eryndex/evidence/*.json` (2 new) | new | ✅ yes | Referenced watchdog log exists (3.4 K); request capture exists; the *registry* claim stores only a status code, no headers (F12) |
| `reports/eryndex/migration-review-batch1.md` | +14 | ✅ path, but the **work is a 4th workstream** | Documents the migration correction below |
| **`supabase/migrations/pending/20261113_hardening_batch1.sql`** | **+5/−1** | ❌ **NO — out of scope** | **F1.** Changes `suppression_registry.source` for the company trigger from `'opt_out_request'` to `'manual_blacklist'` |
| **`tests/test_security_sql_static.py`** | **+19** | ❌ **NO — out of scope** | **F2.** Adds `test_h01_preserves_suppression_source_semantics` |

**On F1/F2 — substance vs process.** The change is technically *correct*: `crm/schema.sql:903` really does write `'manual_blacklist'` for `fn_sync_company_suppression`, while `crm/schema.sql:871` writes `'opt_out_request'` for the contact trigger, and the amended pending file now matches both (`:83` and `:50/:59`). So a reviewer fixed a genuine semantic bug — an unapplied permission fix would have re-labelled every operator suppression in the audit ledger. But it is a modification of an **unapplied, already-reviewed migration artifact** by a workstream that was not declared, and it changes the sha256 the Supabase matrix publishes (`57f3b2…`, which I re-computed and confirmed matches the *current* file). It must be its own reviewed commit, not swept into an outreach commit.

**On F1 timing — the tree was not quiescent when this audit started (F11).** My first `git status` showed only `provider-facts.md` modified; ~6 minutes later three more paths were dirty (mtimes 14:40:53 / 14:41:13, a `node` process started 14:40:54). Content was then stable across a 25 s re-check (`CONTENT-STABLE/STATUS-STABLE`) and stayed stable for the rest of the audit. Consequence: these figures describe the tree from 14:50:27 onward, and **`git add -A` must never be used for this increment** — it would silently capture a 4th workstream.

---

## 3. Secret scan of all changed/untracked files

Method: regex sweep of all 16 non-`.pyc` files in `git status --porcelain -uall` for Supabase/`eyJ` JWTs, `service_role`, `SUPABASE_SERVICE`, `sb_secret`, `Bearer <token>`, `password=`/`password:`, PEM private keys, credentialed connection strings, AWS keys, `sk-` keys, generic `api_key=`. **Result: no secret-shaped match in any changed or untracked file.**

Two near-misses, both benign, both redacted here:

* `tests/test_outreach_gate.py:1210` — `bearer = "eyJhbG...NiJ9" + "." + "payload" + "." + "signature"`. A deliberately fake three-segment string with a literal `...`; the adjacent lines build a fake `apikey` and spell `"Bea"+"rer"` to avoid tripping scanners. It is a *fixture for the redaction test*, not a credential.
* `reports/eryndex/phase-0-baseline.md:99` — the words "service_role API key" inside a quoted PostgREST error message. Prose, no value. (Tracked file, unchanged in this increment.)

`reports/eryndex/supabase-access-matrix.md` names project refs and org id only — both are non-secret identifiers already in `.temp/linked-project.json`.

---

## 4. Test numbers (measured here, not quoted)

```
python -m pytest tests/test_outreach_gate.py -o addopts="" -q --no-header   -> 134 passed
python -m pytest -o addopts="" -q --no-header                              -> 330 passed, 6 skipped
python -m pytest --collect-only -q                                         -> 336 collected
```

| Metric | Value |
|---|---|
| Collected (whole suite) | **336** |
| Passed | **330** |
| Failed | **0** |
| Errors | **0** |
| Skipped | **6** — all `tests/db/test_security_fixes_db.py`, reason `ERYNDEX_DB_URL not set: database policy tests need a Development Postgres connection` |
| Collected in `tests/test_outreach_gate.py` | **134** (matches the claim exactly) |

Per-file collection (sums to 336): researcher slice 6, runner validation 14, db security 6, architecture boundaries 5, bridge/briefs 11, discovery 29, environment guard 8, human gates 10, idempotency 7, ipp coverage 3, ipp engine 45, **outreach gate 134**, provider contract 27, qualification persistence 21, security SQL static 10.

**The 6 skips are not passes.** Nothing in this run executed a Postgres policy, constraint or trigger; the "DB-enforced" rows in the closure matrix (`fk_outreach_contact_company`, `chk_outreach_mandatory_human_gate`, `agent_insert_outreach_messages`) are *read from `crm/schema.sql`*, never exercised.

**The claim "329 passed + 6 skipped" is one low.** 329 was true when it was measured; the tree then gained `tests/test_security_sql_static.py::test_h01_preserves_suppression_source_semantics` (+1) from the migration-review workstream, so the tree that is about to be committed runs 330. This is a stale number, not a padded one — but it is wrong for the artifact being committed (F6).

---

## 5. Dispatch guarantee — proved independently

| Check | Result |
|---|---|
| Network-capable imports anywhere in `outreach/` (`socket`, `http`, `urllib`, `requests`, `httpx`, `smtplib`, `ssl`, `aiohttp`, `ftplib`, `telnetlib`, `asyncio`) | **none** — `grep -E '^\s*(import|from)\s+(socket|http|...)' outreach/` exits 1; the only textual mentions are the words "socket" and "httpx" inside comments/docstrings |
| `import outreach.gate` then `create_draft(...)` with fakes, then `'outreach.dispatch' in sys.modules` | **False** |
| `outreach.dispatch.DISPATCH_AVAILABLE` | **False** (a module constant; no env var reads it) |
| `dispatch()` / `DispatchChannel()` / `queue_for_dispatch()` | each raises **`DispatchDisabled`** — none returns |
| Module source scan of `dispatch.py` for transport imports | **none** |
| Repo-wide send path outside `outreach/`+`tests/`: `smtp`, `smtplib`, `sendgrid`, `resend`, `mailgun`, `twilio`, `sendmail`, `postmark`, `ses.send`, `send_email` across `*.py`, `*.ts`, `*.sql` | **0 hits** |
| Production implementation of `DraftWriter` / `QualificationReader` / `SuppressionReader` outside `tests/` | **none** |
| Anything outside `tests/` importing `outreach.gate` / `outreach.ports` | **none** |

Conclusion: "nothing here can send" is a property of the import graph, verified three ways (no transport symbol, no import edge, no repo-wide send path). The corollary matters for committing: `outreach/` is **currently unreachable at runtime** — dead until a composition root imports it — so committing it cannot cause a send or a write (F10).

---

## 6. Re-attacking the two critical findings (my code, my fakes)

Probe: `C:/Users/puria/AppData/Local/Temp/qa_probe.py`, standard library only, run from the repo root with `PYTHONPATH=.`. Baseline control first: a genuinely approved, evidence-backed company A + `clear`/named-source suppression → `created`, 1 payload written.

| Attack | Observed |
|---|---|
| (a) `get_qualification("B")` returns company A's approved record | `rejected`, `['qualification_company_mismatch']` |
| (a2) forged `EligibilityDecision(codes=(), qualification=A_record)` passed to `build_draft_payload` for a company-B request | `DraftRejected ['qualification_company_mismatch']` |
| (a3) reader returns a raw PostgREST dict | `rejected`, `['qualification_lookup_failed']` (no `AttributeError`) |
| (b) forged decision + body "We propose a revenue share of 20%." | `DraftRejected ['revenue_sharing_prohibited']` |
| (b2) forged decision + `pending_human_review` scorecard | `DraftRejected ['qualification_pending_review']` |
| (b3) forged decision + **no** qualification | `DraftRejected ['provenance_incomplete']` |
| (b4) forged decision + evidence refs that are not UUIDs (`F-12`) | `DraftRejected ['provenance_incomplete']` (the empty-`UUID[]` hole is closed) |
| Suppression reader raises `RuntimeError("pgbouncer down")` | `rejected`, `['suppression_lookup_failed']` |
| Suppression reader raises `SystemExit(3)` | propagates out of `create_draft` — **no draft written** (crash, never a pass) |
| `SuppressionReport(state="clear", sources_checked=("", "   ", "•"))` | `rejected`, `['suppression_ambiguous']` |
| `SuppressionReport(state="clear")` (empty tuple) | `rejected`, `['suppression_ambiguous']` |
| `state="ambiguous"` | `rejected`, `['suppression_ambiguous']` |
| `state="clear"` **with** findings | rejected as ambiguous (matrix claim reproduced) |
| Writer returns an existing row belonging to company B | `DraftPersistenceError` |
| Writer returns the row with `sending_status="queued"` | `DraftPersistenceError` |

**Residual, observed and honestly documented:** `SuppressionReport(state="clear", sources_checked=("suppression_registry", "contacts"))` **creates the draft** even if RLS filtered those tables to zero rows. That is not a bug in the gate — a port cannot re-derive a suppression read — and `outreach/ports.py:290-301` states the obligation and the reason explicitly, and the closure matrix §6.6 restates it. It belongs on the "cannot verify without a database" list, not on a defect list. The report does not overclaim it.

### Are the guards load-bearing? (independent mutation run)

I did not take "each fix was proved by reverting it" on trust. Using a pytest plugin that patches the already-imported module object (`mutplug.py`, `MUT_MODE`), no repository file touched:

| Guard removed | `tests/test_outreach_gate.py` |
|---|---|
| *(none — control)* | **134 passed** |
| C-01 company-mismatch rejection | **3 failed**, 131 passed |
| C-02 payload-builder re-derivation (trust `decision.eligible`) | **4 failed**, 130 passed |
| M-03 `_names_a_source` (any non-empty tuple counts) | **4 failed**, 130 passed |
| H-05 `_verify_persisted` read-back | **9 failed**, 125 passed |
| L-06 `DraftRequest` structural type checks | **7 failed**, 127 passed |

Every removal is noticed by the suite. The authors' revert artifacts also exist on disk (`…/Temp/outreach_revert/revert_matrix.json`, 16 entries, each with a failing-test tail). Their counts differ slightly from mine where a mutation is broader than theirs (my C-01 patch also disables the payload-path reuse, hence 3 vs 2) — the direction is the same and the conclusion holds.

---

## 7. Crawl4AI — where the claim reproduces and where it does not

### 7.1 Reproduced

| Probe (bounded) | Result |
|---|---|
| `timeout 30 docker info` | exit **0**, 82 lines of output (Docker Desktop 25.0.3, engine 25.0.3, linux/amd64) |
| `timeout 20 docker version` / `docker ps` / `docker images` / `docker volume ls` / `docker image inspect postgres:16-alpine` | all **exit 0** — container, image and volume reads answer |
| **`timeout 30 docker network create eryndex-qa-probe`** | **exit 124** — hung, killed at 30 s, network never created (nothing to remove) |
| `timeout 25 docker network ls` and `timeout 25 docker network inspect bridge` | **exit 124 both** — *read-only* network calls hang too (see F5) |
| `grep -c "temporary lease" …/Docker/log/vm/dockerd.log` | **6** — at 07:42:58, 07:43:05, 07:54:03, 08:13:06, 08:41:43, **10:21:07**; message `Handler for POST /v1.44/images/create returned error: error creating temporary lease: context canceled` — identical to the six timestamps the evidence JSON lists, to the second |
| `docker images` | **no `crawl4ai` image**, no `hello-world` → "image never pulled" is true |
| TCP `127.0.0.1:11235` | **refused** (`connect_ex` → 10061) → "no container, endpoint unreachable" is true |
| `timeout 30 df -h /c` | `476G / 455G used / 21G avail / 96%` |

**So: `failure_class=docker` stands, and the "daemon write path wedged" claim reproduces.** The evidence is unusually good here: the lease-error timestamps in `dockerd.log` match the report exactly, and the wedge is observable in 30 s.

### 7.2 Contradicted

| Claimed (evidence JSON note 60, `provider-facts.md:367-368`) | Measured 2026-11-14 11:12Z |
|---|---|
| `registry-1.docker.io/v2/` → **403** `RBAC: access denied` | **`HTTP/1.1 401 Unauthorized`** with `www-authenticate: Bearer realm="https://auth.docker.io/token",service="registry.docker.io"` — the *normal, correct* challenge for an anonymous `/v2/` request. A 403 here would be abnormal; 401 is what a healthy registry returns. |
| `auth.docker.io/token` → **403** (Cloudflare) | **200** with a real JWT in the body (`{"token":"…"}`) — a pull token is issued |
| `hub.docker.com/v2/repositories/unclecode/crawl4ai/tags/` → **403** | **200 OK**, JSON: `count 40`, first tags `latest, 0, 0.9, 0.9.5, 0.9.4` — **the 0.9.5 tag that the doc calls "unconfirmed" is confirmed present**, and the same doc already lists it at `:236` |
| "files.pythonhosted.org also fails DNS … the PyPI wheel could not be used" | **200**, wheel `crawl4ai-0.9.5-py3-none-any.whl`, `Content-Length: 494036`. I downloaded it. |
| "Disk was not a factor: 21.18 → 21.17 GiB free, **~0 bytes consumed**" (`provider-facts.md:368`) | The report's **own** JSON says `disk_free_gb_after: 20.92` — ~250 MB of host-side drift, which the JSON discloses honestly. The conclusion (disk was not the blocker) is right; the sentence in the committed doc is not (F4). |

I did **not** pull an image (forbidden), so I cannot say a pull would succeed now. What I can say is narrower and sufficient: **the registry is reachable and the tag exists**, so the doc's "Docker Hub 403s this network … no image bytes are obtainable even with a healthy daemon" and the owner action "**add a Docker Hub mirror/proxy**" are unsupported. The plausible readings are a transient Cloudflare/WAF block during their window (10:16–10:55Z) or a measurement artifact; the artifacts cannot settle it because only a status code was stored, with no headers, no `x-trace`, no timestamped log for that specific call (F12). Either way the document must not present it as a standing fact of the network.

### 7.3 Adapter defects — confirmed at source, and confirmed against the real 0.9.5 wheel

The report called the wheel unreachable. It is not, so I fetched it and checked the claims directly:

| Claim | Verified? |
|---|---|
| `adapter.py:70` sends uppercase `cache_mode` (`"BYPASS"`/`"ENABLED"`) | **Yes** — `_call` body is `{"urls":…, "crawler_config": {"cache_mode": "BYPASS" if … else "ENABLED"}}` |
| `crawl4ai/cache_context.py:16-20` values are lowercase | **Yes** — `ENABLED = "enabled" … BYPASS = "bypass"` in the extracted wheel |
| `adapter.py:88-94` treats `markdown` as a `str` | **Yes** — `item.get("markdown") or item.get("raw_markdown") or …` |
| `models.py` sets `result["markdown"] = self._markdown.model_dump()` (nested dict) | **Yes** — `crawl4ai/models.py:281` in the wheel |
| `adapter.py:57-63` validates `extract`, `_call` never sends it | **Yes** — body contains no `extract` key |
| `adapter.py:5` docstring promises `browser_config`, `_call` never sends it | **Yes** |
| `contracts/provider.py:41-70` `Evidence` has no provider-issued id | **Yes** — fields are `url, excerpt, provider, provider_version, retrieved_at, content_hash, title` |

These five defects are real and latent. They are *not* causal for this failure, as the report says.

---

## 8. Supabase — every number re-measured

| Claim | Measured |
|---|---|
| Two projects, only Development linked | `supabase projects list` → `vqnamszywsuqjxuqseas` (name `development-partnership`, `"linked":false`, ACTIVE_HEALTHY) and `vyfqmrdznmocnmzyjjcj` (name `Puriatc10's Project`, `"linked":true`, ACTIVE_HEALTHY, pg 17.11.0.003, ap-southeast-2) — **exact match** |
| `supabase/.temp/linked-project.json` ref | `{"ref":"vyfqmrdznmocnmzyjjcj","name":"Puriatc10's Project","organization_id":"ktdpfeytpkqnggejhscb",…}` — **match** |
| Remote history = only `20261107174129` | `supabase migration list --linked` → `{"migrations":[{"local":"20261107174129","remote":"20261107174129","time":"2026-11-07 17:41:29"}]}` — **match**, and both pending files are absent from remote history |
| `db push --dry-run --remote` sees 0 pending | `{"upToDate":true,"dryRun":true,"migrations":[],…}` — **match**; no SQL applied |
| Pending files break the 14-digit convention | `supabase/migrations/pending/20261113_discovery_candidates.sql`, `…20261113_hardening_batch1.sql` — **8-digit date prefixes**, both would collapse to version `20261113` |
| …therefore the CLI ignores them | Confirmed by the two mechanisms the matrix names: they live in `pending/`, which the CLI does not scan (only `supabase/migrations/*.sql`), and the 14-digit form is the version. "0 pending" is **invisibility, not convergence** — the correct reading |
| `migrations/` still only the core schema | Yes: `20261107174129_core_crm_schema.sql` (2,089 lines, sha256 `d1dbb0fd…`, applied) plus `pending/` |
| Both pending files still in `pending/` | Yes, plus `README.md`; sha256 `57f3b233…` (hardening, post-correction) and `d3c8544e…` (discovery) — **identical to the matrix's published hashes** |
| Connectivity 6/8 cumulative | Not re-derivable (it aggregates a prior increment). My three calls this audit all succeeded (projects list, migration list, dry-run) → consistent with "intermittent, currently working" |
| DDL unproven / unattempted | **Confirmed** — no `CREATE`/`ALTER`/`DROP` was sent; `upToDate:true` proves nothing about the schema |

Two caveats a reader must not skip: the CLI labels the linked project `environment: "production"` in every target block — that is branch semantics on the project's primary database, **not** the Production project, and no claim in the matrix leans on it. And `supabase/config.toml` sets `project_id = "development-partnership"`, which is the *Production project's name* while the repo is linked to Development — a live naming trap the matrix correctly flags and correctly does not "fix" in this increment.

**One confirmed drift the matrix also caught:** `supabase/migrations/pending/README.md`'s files table lists only `20261113_hardening_batch1.sql`; `20261113_discovery_candidates.sql` is absent from the index. An owner following the README applies one of two files (F8).

---

## 9. Nothing historical or protected changed

| Check | Result |
|---|---|
| `git diff --stat 72a1886..HEAD -- reports/research/ reports/phase-3.1-refokus-research-execution-report.md agents/qualification/ business/` | **empty** ✅ |
| `git status --short` on the same paths | **clean** ✅ |
| `git diff --stat f30d88e..HEAD -- intelligence/ipp/ crm/ supabase/migrations/*.sql` | **not empty** — 2 files, +385: `pending/20261113_discovery_candidates.sql`, `pending/20261113_hardening_batch1.sql`. **Pathspec artifact, not damage:** git's `*` crosses `/`, so `supabase/migrations/*.sql` matched `pending/`. Both files were *added* by `771c1b2` (prior directive, documented in its message); no **applied** migration and no `crm/` or `intelligence/ipp/` file changed. `/intelligence/ipp/` and `/crm/` are genuinely empty. |
| `git status --short -- intelligence/ipp/ crm/` | **clean** ✅ (IPP and the CRM design document untouched — the tier guard imports `tier_for_score` rather than editing it) |
| Core schema history | `git log -- supabase/migrations/20261107174129_core_crm_schema.sql` → one commit (`3262c4e`), never rewritten ✅ |
| `supabase/migrations/` contents | only `20261107174129_core_crm_schema.sql` + `pending/` ✅ |
| Both pending files still in `pending/` | ✅ |
| `docs/brand/portfolio-fixes.patch` | `git diff` empty, `git status` clean — **unchanged** ✅ |
| `git diff --check` | **clean** ✅ |

The one thing that *has* moved since the directive's baseline is the working tree, not history: `pending/20261113_hardening_batch1.sql` is modified (F1) — this is an **uncommitted** delta, so history is intact but the file about to be committed differs from the one the migration review approved.

---

## 10. Findings

| # | Sev | Claim | Evidence | Conclusion |
|---|---|---|---|---|
| **F1** | **high** | "Nothing outside the declared scopes changed." | `supabase/migrations/pending/20261113_hardening_batch1.sql` `+5/−1`: `'opt_out_request'` → `'manual_blacklist'` in `fn_sync_company_suppression` | **Out of scope.** Substantively correct (`crm/schema.sql:903` = `manual_blacklist`), but it mutates a reviewed, unapplied migration and changes its published sha (now `57f3b233…`). Commit it deliberately, alone, citing `migration-review-batch1.md §4`. |
| **F2** | **medium** | ditto | `tests/test_security_sql_static.py` `+19`, new `test_h01_preserves_suppression_source_semantics` (passes) | Out of scope. Belongs with F1, not with the outreach commit. |
| **F3** | **medium** | `provider-facts.md:367` — "Docker Hub 403s this network (registry / auth / hub) → `RBAC: access denied`, so tag/size are unconfirmed"; `:379` reproduction "returns 403"; `:381` owner action "add a Docker Hub mirror/proxy" | registry `/v2/` → **401 + Bearer challenge**; `auth.docker.io/token` → **200 with a token**; `hub…/tags/` → **200**, `count 40`, `0.9.5` present; `files.pythonhosted` wheel → **200**, 494 KB | **Contradicted as a standing fact.** The tag *is* confirmed. Remove the 403 sentence and the mirror/proxy action, or reword to "transient block observed 10:16–10:55Z, not reproducible at 11:12Z". |
| **F4** | **low** | `provider-facts.md:368` — "Disk was **not** a factor: 21.18 → 21.17 GiB free, ~0 bytes consumed" | Its own JSON: `disk_free_gb_after: 20.92`, "~250 MB host-side drift" | Conclusion holds, number does not. Align with the JSON. |
| **F5** | **low** | "the daemon answers read-only calls but every mutating call hangs" (`provider-facts.md:366`) | `docker network ls` and `docker network inspect bridge` — both read-only — **also hang to 124** | Imprecise dichotomy. Correct statement: container/image/volume reads answer; **network-subsystem** calls hang whether they read or write; image-create fails on a lease. Fix the sentence so the next agent doesn't trust `docker network ls`. |
| **F6** | **low** | `security-closure-matrix.md §8` / claim A — full suite "**329** passed, 6 skipped" | Measured **330 passed, 6 skipped, 336 collected** | Stale by +1: a test was added to `tests/test_security_sql_static.py` after the measurement. Update before the number is quoted forward. |
| **F7** | **low** | `outreach-workflow.md` describes the pre-H-06 idempotency key, older evidence rule and rejection codes | Matrix L-05 classifies it "open — owner action" | Acknowledged, correctly disclosed, still the first doc a newcomer reads. Not a blocker; fix when convenient. |
| **F8** | **low** | `pending/README.md` indexes 1 of its 2 files | README lists only `20261113_hardening_batch1.sql` | Confirmed. Applying "per the README" would silently skip `discovery_candidates`. |
| **F9** | **info** | `outreach/__pycache__/*.pyc` exists (incl. `dispatch.cpython-312.pyc`) | `.gitignore:10` = `__pycache__/`; `git status -uall -- outreach/` lists only the four `.py` | Will not be committed. No action. |
| **F10** | **info** | "…the writer recomputes the key from the row" (a contract with no implementation) | `grep DraftWriter|find_existing|create_draft` over `integrations/ orchestration/ agents/ contracts/ intelligence/` → nothing; nothing outside `tests/` imports `outreach.*` | Confirmed and already disclosed (§7.3 of the matrix). It also means the commit is **runtime-inert** — a safety argument for committing it. |
| **F11** | **info** | "Three agents just finished; nothing is committed." | 3 paths appeared between two `git status` snapshots ~6 min apart (mtime 14:40:53/14:41:13, `node` started 14:40:54); stable from 14:50:27 | A 4th workstream landed while the audit opened. Re-check status immediately before any commit; never `git add -A`. |
| **F12** | **low** | The 403 claim | `crawl4ai-live-verification.json` stores only status codes for the registry calls — no header capture, no trace id, no per-call log, unlike the disk (81 samples) and lease (6 timestamps) claims | Evidence-quality gap: an unreproducible claim with no raw artifact behind it. Future probes should store headers (`-D`) for any status-code assertion. |

**No defect was found in `outreach/gate.py`'s decision logic, `outreach/ports.py`'s payload constraints, `outreach/dispatch.py`'s refusal, or in the 134 tests' relationship to the five guards I removed.** The two critical findings are genuinely closed; the suppression fail-closed behaviour is genuinely fail-closed for the three shapes tested (raising reader, unnamed source, ambiguous state) and the fourth (RLS-empty) is disclosed rather than hidden.

---

## 11. Cannot verify without a database

Everything below needs a live Development Postgres (and, for several, a provisioned `drafter` identity and the hardening batch applied). None of it was executed; every item is a *read* of `crm/schema.sql`.

1. **Every DB-enforced invariant the gate leans on:** `fk_outreach_contact_company` (`schema.sql:626-627`), `uq_contacts_id_company` (`:430`), `chk_outreach_mandatory_human_gate`, `chk_outreach_no_revenue_sharing` (`:630-632`), `chk_lead_qual_review_human_gate`, `fn_enforce_lead_qualification_rules` (`:1273+`), `fn_enforce_outreach_human_approval` (`:1231-1254`).
2. **That the readers actually read** (RLS, task scope, `agent_select_contacts` hiding opted-out rows). The gate verifies *shape*, never provenance — a hand-built but well-formed `QualificationRecord` passes every check (the matrix states this plainly in §7.1).
3. **The RLS-empty residual (my §6 result):** a `clear` report that names two sources but queried zero visible rows still mints a draft. Only a task-scoped reader that first proves it can see the `contacts` row closes this (`ports.py:290-301`, matrix §6.6).
4. **That a write can happen at all:** no `drafter` identity is provisioned, no `DraftWriter` exists, and `agent_insert_outreach_messages` is unapplied. The insert path is unexercised in every test.
5. **Two-writer idempotency (M-06):** needs `uq_outreach_messages_slot`; the test only pins that the race exists.
6. **Lifecycle-stage refusal (M-08):** needs H-03 applied plus a `CompanyReader` that does not exist.
7. **The hardening batch itself (F1/F2's subject):** every CLAIM about `fn_sync_company_suppression`, `fn_enforce_agent_task_transitions`, `fn_sync_company_primary_domain`, `agent_insert_outreach_messages` is static reading. DDL is unproven — as the Supabase matrix says in its own row 5.
8. **Docker pull success.** The registry is provably reachable now (§7.2), but a pull is a daemon write and the write path is wedged; no pull was attempted and none should be attempted until the daemon is restarted by its owner.
9. **Whether the 403 window was real.** Disposition unknown and unknowable from the stored artifacts (F12).

---

## 12. Safe to commit?

**Not in one sweep. Yes, conditionally, in three separate commits.**

**(i) Verified outreach/security work — safe to commit as its own commit.**
`outreach/` (4 files), `tests/test_outreach_gate.py`, `reports/eryndex/{outreach-adversarial-review,outreach-workflow,security-closure-matrix}.md`. Rationale: 134/134 tests pass; 5 independent guard-removal mutations are caught by the suite; both critical attacks blocked by my own probes; dispatch is absent from the import graph and no send path exists repo-wide; no secrets; `git diff --check` clean; and the package is imported by nothing outside `tests/`, so it is inert at runtime. One correction first: the §8 full-suite number should read **330 passed, 6 skipped** (F6).

**(ii) Blocked provider work — commit the docs only after correcting them.**
`reports/eryndex/{crawl4ai-blocker-analysis.md, supabase-access-matrix.md}` and `reports/eryndex/evidence/*` are honest artifacts and can commit. `docs/architecture/integrations/provider-facts.md` must **not** ship as written: strike or requalify the Docker Hub 403 claim (`:367`, `:379`), drop the "add a Docker Hub mirror/proxy" owner action (`:381`), fix the disk numbers (`:368`), and stop describing the daemon as "reads fine / writes hang" (`:366`) without noting that `docker network ls`/`inspect` hang too (F3/F4/F5). Crawl4AI itself stays `failed` / `endpoint_verified=false` — that part is correct and well-evidenced.

**(iii) Must not be committed with this increment — needs its own reviewed commit.**
`supabase/migrations/pending/20261113_hardening_batch1.sql` (F1) and `tests/test_security_sql_static.py` (F2), together with `reports/eryndex/migration-review-batch1.md` which documents them. Substance is correct and the new test passes, but this is a 4th workstream that was not declared, it changes a reviewed migration's content and sha, and it must not ride along with an outreach commit where no reviewer will look at DDL. Also do not commit: `outreach/__pycache__/` (already ignored — F9), and nothing under `supabase/.temp/`.

**One-line recommendation:** commit the outreach work plus its three reports, hold back the two `provider-facts.md` factual claims, and land the migration + its test as a separate reviewed commit — but never with `git add -A`, because a fourth workstream touched the tree mid-audit (F11).

---

### Appendix — commands used (all bounded; no mutating call beyond one network-create probe)

```
git status --short / --porcelain -uall ; git diff --stat ; git diff --check
python -m pytest tests/test_outreach_gate.py -o addopts="" -q --no-header
python -m pytest -o addopts="" -q --no-header ; python -m pytest --collect-only -q
PYTHONPATH=. python C:/Users/puria/AppData/Local/Temp/qa_probe.py
MUT_MODE={c01,c02,m03,h05,l06} … -p mutplug        # in-memory guard removal
timeout 30 docker info | timeout 20 docker version | timeout 25 docker ps/images/volume ls
timeout 25 docker network ls ; timeout 25 docker network inspect bridge
timeout 30 docker network create eryndex-qa-probe     # exit 124, nothing created
timeout 25 curl -sS -I https://registry-1.docker.io/v2/
timeout 25 curl -sS 'https://auth.docker.io/token?service=…' ; https://hub.docker.com/v2/…/tags/
timeout 30 df -h /c ; grep -c "temporary lease" …/Docker/log/vm/dockerd.log
timeout 60 supabase projects list ; cat supabase/.temp/linked-project.json
timeout 90 supabase migration list --linked ; timeout 90 supabase db push --dry-run --remote
sha256sum supabase/migrations/pending/*.sql supabase/migrations/20261107174129_core_crm_schema.sql
git diff --stat 72a1886..HEAD -- <protected paths> ; git diff --stat f30d88e..HEAD -- <protected paths>
```
