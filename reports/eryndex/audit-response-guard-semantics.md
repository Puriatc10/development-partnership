# Audit response — the guards that never ran

**Trigger:** an independent adversarial audit of `c330deb..HEAD` returned **PIPELINE: REJECT** with
three blocking findings, and a second audit returned **CONSISTENCY: FAIL** on the same range. The
two overlapped on one root cause that neither the 660-test suite nor eleven RLS probes had caught.

## The root cause

Eleven guards in the core schema were written like this:

```sql
IF OLD IS NOT NULL AND NEW.approval_status != OLD.approval_status THEN
```

In PostgreSQL a row expression is NULL if **any** of its fields is NULL, and `OLD IS NOT NULL` on a
composite is true only when every column is non-null. Verified in this database:

```
select row(1,null) is not null  ->  false
select row(1,2)    is not null  ->  true
```

Every table here has nullable columns (`approved_by_human_id`, `sent_at`, `reviewed_at`,
`completed_at`, …), so the condition was false on exactly the transitions it was meant to police.
The blocks never ran. That is why:

- an approved message could jump `draft → sent` in one statement;
- a body could be rewritten, and an approver replaced, with approval intact;
- an approved scorecard could be edited without being demoted to `draft`;
- company lifecycle, task and conversation transition guards were inert.

The tests passed because the *policies* and the *other* triggers were doing real work; the missing
layer was never exercised by a case that would notice its absence.

## What was fixed

| # | Finding | Fix | Verified |
|---|---|---|---|
| 1 | Eleven `OLD IS NOT NULL` guards inert | `20261123140000`: the same function bodies with `TG_OP = 'UPDATE'`, the standard way to say "an old row exists" | live probe A–C |
| 2 | Pre-dispatch suppression ignored secondary domains | `20261123140000` (alias clause) + `20261123150000` (correlation bug found while testing it) | live probe E2 |
| 3 | Operator could insert an already-approved scorecard | the insert-is-draft rule is the first check in `fn_enforce_lead_qualification_rules` (`20261123150000`) | live probe D |
| 4 | Operator could insert an approved+queued message with no scorecard | `fn_enforce_outreach_insert_preconditions` (`20261123140000`): new messages are drafts, and the company must carry an approved, non-disqualified scorecard | live probe E |
| 5 | Citations were UUID-shaped, never referential | `PostgresDraftWriter._require_evidence` joins every cited id to `research_findings` for the same company | 2 new unit tests + the live e2e draft (6/6 real) |
| 6 | A suppressed company's draft could still be approved | approval re-checks company flag, opt-out and the registry, including secondary domains (`20261123160000`) | live probe |
| 7 | Dispatch unreachable in code, reachable in SQL | transport states (`sending`/`sent`/…) require a `dispatcher` claim; no identity carries one (`20261123160000`) | live probe A + control G |
| 8 | Candidate deletion left no audit row | `trg_discovery_candidates_audit` now fires on INSERT, UPDATE **and** DELETE | live probe F |
| 9 | `in.('o''brien')` silently mangled, `patch`/`delete` without a filter swept every visible row | `_split_top_level` raises on an unterminated quote; `patch`/`delete` require a filter unless `allow_all=True` | 4 new client tests |
| 10 | A bare JWT survived redaction | `_JWT_RE` added to `observability/redaction.py` | 1 new test |

Migration 20261123150000 exists because the first attempt at finding 2 referenced an alias
(`comp`) that had gone out of scope — the guard raised "missing FROM-clause entry", which the probe
caught immediately. That is the value of probing the fix rather than trusting it.

## What the fixing changed about the design

**Approval is a transition, never an insert.** A scorecard is created as a draft by whoever produced
it; a human approves it with an UPDATE that the database can attribute. The same is now true of an
outreach message. The rule is enforced by a trigger, not by a policy, so it holds for every role.

**Dispatch is disabled by identity, not by absence of code.** `outreach/dispatch.py` still raises,
and now the database also refuses to record a transport state for a session that is not a
`dispatcher`. Turning dispatch on is a deliberate migration that provisions that identity — not an
`UPDATE` anyone can type.

**A citation is a foreign key in spirit.** `personalization_evidence_ids` has no FK (it is an
array), so the writer performs the join. The gate still checks shape; the writer checks existence.

## Verification

```
python -m pytest              -> 671 passed, 0 failed, 0 skipped (11 of them are these cases, in tests/db/test_update_guards_db.py)
python scripts/db_migrate.py  -> development · 10 applied · 0 pending
python scripts/rls_probe.py   -> 11/11 cases behaved as the security model requires
python scripts/verify_update_guards.py -> A–G all refused/accepted as intended, transaction rolled back
```

`scripts/verify_update_guards.py` is the durable artifact: it reproduces each audit attempt against
the live database and asserts the refusal reason, inside one transaction that is rolled back.

## Honest residuals

- The suite that existed (660 tests) did not catch this class of defect. The eleven cases from
  `scripts/verify_update_guards.py` are now `tests/db/test_update_guards_db.py`, so the guards
  regress loudly if a future migration writes `OLD IS NOT NULL` again.
- `research_findings(report_id, statement)` still has no unique key: idempotent retry is
  read-then-insert, so two *concurrent* retries can double-insert. Sequential retry is verified.
- The audits' probe accidents (psycopg committing a `transaction()` block) left append-only
  `audit_logs` rows (1292–1294, 1355–1356) and a set of `[PROBE]` rows that were deleted. The ledger
  is meant to be immutable; those rows are the honest record of an audit that wrote to the database.
- Suppression rows cannot be deleted by design, which is why the verify script uses a second company
  for its positive control instead of un-suppressing the first.
