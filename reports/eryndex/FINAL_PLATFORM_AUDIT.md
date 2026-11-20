# Final Platform Audit — Eryndex

**Date:** 2026-11-20
**Range audited:** `447dc947166abed168ba0821f0f9c3314dc4524d` → `HEAD`
**Environment:** Development `vyfqmrdznmocnmzyjjcj`. Production never contacted.
**Method:** two independent auditors working only from code, tests and `git diff` (no access to the reports as authority), followed by a fix round and re-verification by the orchestrator. The auditors' findings are reproduced below whether or not they flatter the work.

## Verdict

**`PARTIALLY READY — EXPLICIT BLOCKERS REMAIN`**

Discovery → Register works on the real Development database, with a real operator JWT, and cannot be driven by an agent. Everything after Register is blocked by missing capability, not by missing documentation: no research executor exists, no qualifier identity can persist a scorecard, and Gates 2–4 have no operator command. Dispatch is unreachable in code and in the database, and remains so.

---

## 1. Verified capabilities

Grade means the strongest evidence that exists. Nothing here is graded on documentation.

| Capability | Grade | Evidence |
|---|---|---|
| Schema: 16 tables, RLS on all, anon denied at policy and grant layer | **live** | `20261107174129`, `20261114194622`, `20261115212728` applied; catalogue read-back in `directive-03-7` §4 |
| Candidate staging write path (per-row insert, ownership-scoped recovery, conflict isolation, idempotent evidence) | contract | `discovery/ports.py:229-543`; 71 tests; never executed as an agent |
| Human Gate 1: review → company registration | **live** | candidate `f5339a1d` staged, approved, registered as company `03ed5e71`, re-run created nothing; a pending candidate is refused registration with exit 2 (`stage-2-discovery-gate1.md` §3) |
| Gate 1 cannot be satisfied by an agent | **policy** | `scripts/rls_probe.py` case D: an agent's approval patch affects zero rows and the row stays `pending_review`; case J: agent insert into `companies` → 42501 |
| Reviewer attribution on Gate 1 | **live** | review pair written in one UPDATE; `reviewed_by_human_id` verified to equal the signing operator |
| Registration duplicate safety | **live + unit** | primary-domain and secondary-domain lookups, name conflict path, idempotency via `converted_company_id`; 20 Gate 1 tests |
| Qualification input from persisted research | **live** | Refokus: 32.0/100, disqualified, from its 13 findings, per-dimension breakdown matching the recorded calibration |
| Inconclusive scorecard cannot be persisted | contract | `qualification_repository.py:141-149`; refused at the application layer only (see §4) |
| Outreach eligibility gate (content → qualification → suppression, fail-closed) | unit | `outreach/gate.py:625,903`; 134 tests; adversarial review closed 2 critical / 6 high |
| Dispatch unreachable | unit + SQL | `dispatch.py:22,33-75`; approval gate at `20261107174129:1231-1237` |
| Production cannot be reached from an operator session | unit | `tests/test_environment_guard.py` — the guard runs before sign-in |
| `scripts/rls_probe.py` cannot target Production | unit | pre-flight reads the linked ref and asserts it is Development before any statement |

## 2. Blocking findings from the independent review — all fixed

| ID | Finding | Fix | Regression test |
|---|---|---|---|
| B1 | `rls_probe.py` had no pre-flight guard; it checked the *output* for the Production ref after executing, and its target is whatever `supabase link` last wrote | `assert_development_target()` runs first: reads `supabase/.temp/project-ref`, calls `assert_development_environment`, refuses when the env var disagrees, and exits with a reason rather than a traceback | `tests/test_script_guards.py` (6 cases, incl. Production, unknown ref, disagreement, nothing linked) |
| B2 | Case G (operator approves) was scored on exit code only. With RLS, a policy-filtered UPDATE raises nothing, so the case passed whether or not the approval happened | Allow-cases now assert a value from the statement: `allow:"status": "approved"`, and the control asserts `"visible": 1` | `test_probe_allow_cases_assert_a_value_not_just_exit_code` |
| B3 | `--remove` deleted whatever company `converted_company_id` pointed at, with no synthetic check | Company must be `discovery_source='discovery_candidate'` **and** its `discovery_details.candidate_id` must equal the candidate; company is deleted first (the FK is `ON DELETE SET NULL`, so candidate-first would orphan it); both rows re-read and the run fails if either survives | `test_fixture_removal_refuses_a_company_it_did_not_create`, `..._deletes_the_company_first_and_confirms_absence` |
| B4 | `link_company` had no precondition, and `register_candidate` short-circuited on `converted_company_id` before checking status — so an unreviewed candidate could report "already registered" while still shadowing its domain | `link_company` requires `approved`/`duplicate` **and** a reviewer, and filters its patch on the status; the early return rejects an inconsistent row | `test_link_company_refuses_a_candidate_that_was_never_approved`, `test_registration_refuses_a_link_on_an_unreviewed_candidate` |

## 3. Other findings addressed in the same round

| # | Finding | Resolution |
|---|---|---|
| 1 | Any string could be cited as a verified fact's source (`javascript:`, "see attached email") | `findings_from_rows` requires `http(s)://`, and refuses a `verified_fact` with no URL |
| 2 | Findings read was unpaged; above PostgREST's row cap the evidence set was silently truncated | `_get_all` pages by `limit`/`offset` until exhausted; page size read at call time so it is testable |
| 3 | Dedupe was domain-only, so one studio could be registered twice under two domains | Name match on a different domain now becomes `status='conflict'` with the matched company linked and named in the note |
| 4 | TOCTOU between the duplicate reads and the insert | Candidate is re-read after the insert; if it is no longer approved the company is left unlinked and the operator is told which company to reconcile |
| 6 | `discovery_details.reviewed_by` fell back to the registering operator | Registration refuses when the approval names no reviewer; the provenance records the reviewer and the operator separately |
| 8 | The fixture impersonated `hermes-discovery-v1` | Uses `synthetic-fixture`, which survives into the company record honestly |
| 9 | `--remove` reported success on zero deleted rows | Deletes are followed by a re-read; the run fails if either row is still visible |
| 10 | CLI printed tracebacks for `QualificationInputError`, `EnvironmentGuardError`, `PermissionError`, network errors | All caught with an actionable message and exit 2 |
| 12 | `headcount_min = 0` crashed the engine | Treats `<= 0` as unknown |
| 13 | A fabricated company row stood in for an unreadable one | Raises instead |
| 14 | `reused` was computed before persisting | Taken from the repository's own `already_executed` status |
| 15 | Agent-authored text could inject terminal escapes into the operator's screen | Control characters stripped before printing |
| 16 | A policy denial was reported as a race | Distinguished by re-reading the status |
| 17 | A test compared a list to itself | Fixed; it now asserts exactly one write |
| A3/M1 | The legacy `agents/researcher/operator_cli.py` had no environment guard and inserted companies blindly | Guarded against a non-Development ref, and refuses a domain already held by any company (the re-parenting hazard) |

## 4. Open — not fixed in this mission

| # | Item | Why it is open | Consequence |
|---|---|---|---|
| 1 | Gate 1's `reviewed_by_human_id` is not bound to `auth.uid()` by the schema | Needs a migration (`CHECK ... = auth.uid()` or a trigger); migrations require owner approval | An operator can record *another* human's id as reviewer. The code now verifies the row carries the signing operator, but only for writes made through this package |
| 2 | A scorecard's `excerpt` in `dimension_evidence` is the finding's statement, not the source excerpt | Fixing it changes the frozen IPP engine's evidence record | A reviewer sees agent prose labelled "excerpt". The source excerpt is preserved on the finding row and carried as `source_key`, but not into the scorecard |
| 3 | Inconclusive-scorecard refusal is application-level only | No column or trigger enforces it | An operator JWT could POST an inconclusive scorecard through PostgREST |
| 4 | Dedupe is still not atomic | The CRM cannot take a new unique constraint without a migration | Two simultaneous registrations of the same name under different domains can both succeed |
| 5 | The six DB policy tests never execute | No `ERYNDEX_DB_URL` exists; the CLI mints a per-invocation role | H-01…H-05 are proven by migration text and rolled-back probes, not by a repeatable suite |

## 5. Blockers on the rest of the lifecycle

| Blocker | Effect | Owner action |
|---|---|---|
| No discovery-agent identity (`subagent_type='discovery'`) | The real agent staging path has never run; only simulated claims | OW-1 |
| No qualifier identity, and `evaluator_type='agent'` must name the producer | No scorecard can be persisted; Gate 2 cannot happen | OW-1 |
| No research executor | `qualify` on a newly registered company fails at the first read: no report | D2 — buildable without owner input, next |
| Gates 2–4 have no operator command | Approval can only be done by hand-written SQL | H1 — buildable, next |
| No provider is live | Research cannot produce findings | OW-4, OW-5 |
| No `ERYNDEX_DB_URL` | The security suite is 6 skips | OW-2 |

## 6. Entry points a human can run today

| Command | Reality |
|---|---|
| `python -m operations.cli whoami` | Signs in, guards the environment, verifies the operator claim |
| `… candidates list\|show\|approve\|reject\|register` | The full Gate 1 loop, live-verified |
| `… qualify <company-id>` | Scores from persisted research; never writes |
| `python scripts/gate1_fixture.py [--remove <id>]` | Synthetic candidate for verification; removal is provenance-checked and verified |
| `python scripts/rls_probe.py` | 11 policy cases; refuses a non-Development target before running |
| `python -m agents.researcher.runner --payload-file …` | Persists a hand-authored research payload; researches nothing |
| `python -m agents.researcher.operator_cli …` | Legacy: now guarded and duplicate-checked, but not the sanctioned path |
| `python scripts/discovery_cycle.py` | Offline fixture cycle, JSONL only |

## 7. Test state

```
python -m pytest                448 collected → 442 passed, 6 skipped, 0 failed
tests/test_operations_gate1.py   20
tests/test_qualification_loader.py 15
tests/test_script_guards.py      10
```

The 6 skips are `tests/db/test_security_fixes_db.py`, all from the missing `ERYNDEX_DB_URL`.

## 8. Readiness statement

The platform is **not** production-ready and is **not** yet operationally ready even in Development, because two of the three loop stages a human can reach today end at a wall: register a company and there is no research to qualify. What is ready is the part that was most dangerous before this mission — the gate between an agent's suggestion and the CRM's system of record — and that part is now live, attributed, idempotent and agent-proof.

The honest next three moves, in order: a research executor that produces findings without a provider (fixture-backed, then live when OW-4/OW-5 land); Gate 2 approval in the operator CLI; and the migration that binds Gate 1's reviewer to `auth.uid()` together with audit triggers on `companies` and discovery review.
