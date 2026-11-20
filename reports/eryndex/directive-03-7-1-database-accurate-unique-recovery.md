# Directive 03.7.1 — Database-Accurate Unique Conflict Recovery

**Starting HEAD:** `fb9babf5b0139ee9bb3e43c8f9917ae4bc77d9d6` (verified before editing; clean tree)
**Final commit:** the commit carrying this report (`git log -1`)
**Migration:** already applied in 03.7 — not modified, not reapplied, no SQL executed here.

**Verdict: `FIXED — READY FOR DEVELOPMENT INTEGRATION TEST`**

---

## 1. The index behaviour being modelled

Verified against the live Development database in Directive 03.7, not inferred from the
DDL:

```
uq_discovery_candidates_pending_domain  UNIQUE (domain)            WHERE status = 'pending_review'
uq_discovery_candidates_batch_domain    UNIQUE (batch_key, domain)
```

Probed in rolled-back transactions:

| Case | What fired |
|---|---|
| same batch, same domain, row still `pending_review` | **`…_pending_domain`** |
| same batch, same domain, row no longer pending | `…_batch_domain` |
| different batch, same domain, row no longer pending | nothing |

The partial index is **not batch-scoped**, so it fires first for an ordinary retry of the
writer's own rows — which is exactly the case the recovery path exists to handle.

## 2. Files changed

| File | Change |
|---|---|
| `discovery/ports.py` | `_recover_candidate()` → `_owned_candidate()`: returns the owned row or `None`, wraps read failures, validates id/domain/single-row/status. `write()` now performs the ownership read on **any recognised unique violation** and classifies from what it finds, not from the constraint name. |
| `tests/test_discovery_persistence.py` | fake client checks the partial index **before** the composite one (as PostgreSQL does); added `seed_owned_pending()`; 11 new regression tests; the foreign-actor test now asserts conflict semantics. |
| `reports/eryndex/directive-03-7-1-database-accurate-unique-recovery.md` | this report |

No other file touched. No refactoring of unrelated code.

## 3. Before / after classification

**Before** — the constraint name *was* the decision:

| Error | Classification | Consequence |
|---|---|---|
| `…_batch_domain` | recover | unreachable for the real retry (probe A): the partial index fires instead |
| `…_pending_domain` | foreign conflict | **the writer's own rows were reported as somebody else's**, so evidence was never written and a retry could never complete the batch |

**After** — the error only says *a* unique violation happened; the scoped read decides:

```
POST candidate
  └─ error
       ├─ missing table ................................. StagingUnavailable
       ├─ unrecognised .................................... CandidatePersistenceError(partial counts)
       └─ recognised unique violation
            GET discovery_candidates  WHERE batch_key=eq.<k>
                                      AND discovered_by_agent_identity=eq.<actor>
                                      AND domain=eq.<d>
              ├─ read fails ............................. StagingContractError ("ownership … unknown")
              ├─ exactly 1 row, id+domain, pending_review → RECOVER: reuse the id, finish evidence
              ├─ >1 row / wrong domain / no id ........... PartialRecoveryError / StagingContractError
              ├─ 1 row, status not pending_review ....... PartialRecoveryError
              └─ 0 rows
                   ├─ constraint = …_pending_domain → CONFLICT (domain_staged_elsewhere)
                   └─ constraint = …_batch_domain   → StagingContractError (fail closed)
```

The last branch is the directive's asymmetry, and it is deliberate: on
`…_pending_domain`, the violation itself proves a pending collision exists, so "no visible
owner" is *information* — a foreign batch owns it and RLS correctly hides it. On
`…_batch_domain`, the row that collides is **this** batch's own `(batch_key, domain)` yet
is invisible to the actor — that is a contradiction, not a conflict, so it fails closed
rather than being relabelled.

## 4. Evidence guarantees preserved

Unchanged and re-tested: association by verified domain → id (never array position),
existing `(candidate_id, url)` pairs read before inserting, only missing pairs written,
duplicate URLs collapsed, unreadable evidence table ⇒ failure, unique-race re-read, no
evidence for a conflicted or unverified candidate, recovery still continues to the
evidence stage. Conflicted domains remain an explicit skip set.

## 5. Regression tests

New (11) — each asserts behaviour, not just counters:

1. `test_partial_index_fires_for_a_same_batch_retry_like_postgres` — the fake raises the partial index for a same-batch pending row.
2. `test_retry_recovers_by_a_scoped_ownership_read_after_the_partial_index` — recovery works, and the GET carries all three `eq.` filters and follows the failing POST rather than pre-empting it.
3. `test_third_execution_of_the_same_batch_writes_nothing` — no duplicate candidate or evidence rows.
4. `test_foreign_pending_row_is_read_first_then_reported_as_a_conflict` — ownership is read before "foreign" is declared; reason `domain_staged_elsewhere`; no evidence on the foreign id.
5. `test_batch_constraint_without_a_visible_owned_row_fails_closed` — and is not downgraded to a conflict.
6. `test_ownership_read_failure_fails_closed` — an unreadable owner is never "no owner".
7. `test_owned_row_that_is_no_longer_pending_is_not_recovered` — `status 'approved'` refused, nothing re-inserted.
8. `test_ambiguous_ownership_fails_closed`, `test_owned_row_without_an_id_fails_closed`, `test_owned_row_for_another_domain_fails_closed`.
9. `test_staging_never_leaves_the_candidates_table_or_uses_update_delete` — verbs ⊆ {POST, GET}, tables = the two Discovery tables only.

Updated: `test_recovery_never_attaches_evidence_to_another_actors_candidate` now asserts the
directive's semantics — same `batch_key`, different actor, still `pending_review` ⇒ foreign
conflict with no adoption (it previously expected a raised contract error, which the
partial-index model shows was the wrong classification).

Partial-evidence recovery, mixed batches, existing-company matches, identity conflicts and
the no-service-role assertion remain covered by their existing tests.

### Exact results

```
python -m pytest tests/test_discovery_persistence.py tests/test_discovery.py
  71 passed in 0.22s

python -m pytest                  (full suite)
  396 passed, 6 skipped in 10.20s     (402 collected)

python -m pytest --collect-only -q  (static/security tests unaffected)
```

The 6 skips are `tests/db/test_security_fixes_db.py` (`ERYNDEX_DB_URL` unset) — skipped,
not passed.

## 6. Compliance

No SQL executed, no migration edited or reapplied, no Production access, no Auth identity
created, no service-role client, no RLS policy or grant change, no Outreach dispatch, no
Discovery cycle run, no IPP/Refokus/qualification/CRM change, no Docker operation. The
same injected authenticated PostgREST client is used throughout; `batch_key` and the
trusted actor are unchanged. Working tree left clean.

## 7. Remaining limitations

1. **Fake-client evidence only.** These tests do not prove live RLS behaviour or live
   PostgREST filter semantics. The `eq.`/`in.()` encoding, the scoped select list and the
   policy's own join are still assumptions until a Discovery agent identity exists.
2. **The read is not atomic with the insert.** A concurrent writer can change a row between
   the violation and the ownership read; the checks (single row, `pending_review`) make the
   outcome *safe* (fail closed) rather than *certain*.
3. **No cross-stage transaction** — candidates can exist without evidence; recovery makes
   that state completable, not atomic.
4. **The partial-index model is a model.** It now reproduces probes A/B/C, but a future
   index change must be re-probed against the live table rather than trusted from this file.
