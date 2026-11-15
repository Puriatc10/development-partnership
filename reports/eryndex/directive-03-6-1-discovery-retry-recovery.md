# Directive 03.6.1 — Discovery Retry Recovery & Idempotent Evidence Completion

**Starting commit:** `db502ed`
**Final commit:** the commit carrying this report (see `git log -1`)
**Migration applied:** **no.** No SQL was authored or executed; no Production contact; no Docker operation.

---

## 1. The defect

Directive 03.6 made the staging write two-stage, but a failure in stage 2 was
**unrecoverable**. On retry:

```
POST candidates → 23505 uq_discovery_candidates_batch_domain → AlreadyStaged → return
```

The retry exited before attempting evidence again, so a batch whose evidence write
failed could never be completed — the candidate rows existed and their citations
were permanently missing. Recovery is now part of the write instead of a dead end.

## 2. Files changed and why

| File | Change |
|---|---|
| `discovery/ports.py` | `StagingResult(created, recovered, evidence)`; `PartialRecoveryError`; the sink recovers a same-batch collision, verifies the persisted rows, and completes evidence idempotently |
| `discovery/service.py` | outcome gains `recovered` / `evidence_staged`; `staged` now means *newly created*; a cross-batch collision is reported as `domain_staged_elsewhere` with its domains |
| `tests/test_discovery_persistence.py` | rewritten around a **stateful** fake client that enforces both constraints and supports GET filters, so an interrupted write can be replayed |
| `tests/test_discovery.py` | `RecordingSink` returns `StagingResult`; the missing-table test now supplies a complete row |
| `reports/eryndex/directive-03-6-1-discovery-retry-recovery.md` | this report |

No schema, migration, IPP, Refokus, qualification or outreach file was touched.

## 3. Recovery flow, before and after

**Before**

```
1 POST candidates
2   on 23505 (batch)      → AlreadyStaged, staged = 0, evidence abandoned
3   on 23505 (pending)    → AlreadyStaged
4 evidence POST
```

**After**

```
1 POST candidates (Prefer: return=representation)
2   success → index id+domain → evidence
3   on 23505 (batch_key, domain)        [Scenario C — recovery]
     a GET discovery_candidates  WHERE batch_key = eq.<key>
                                 AND discovered_by_agent_identity = eq.<actor>
     b require rows to exist, every row to carry id+domain
     c require the returned domain set to EQUAL the expected set
     d require every row to still be status = pending_review
     e reuse the database ids → continue to the evidence stage
4   on 23505 (pending domain)          [Scenario D — conflict]
     → AlreadyStaged(domains=…) → outcome.already_staged, reason domain_staged_elsewhere
     → no evidence, no ownership change, staged = 0, recovered = 0
5 evidence: read what already exists for those candidate ids, insert only the
  missing (candidate_id, url) pairs; verify completeness before returning
```

## 4. Same-batch recovery vs another-batch conflict

The two unique indexes mean two different things, and they are now handled
differently:

| Signal | Meaning | Behaviour |
|---|---|---|
| `uq_discovery_candidates_batch_domain` | *this* batch (same `batch_key`) already wrote these rows | **recover**: read them back scoped by batch + actor, verify, reuse ids, finish evidence. Reported as `recovered`. |
| `uq_discovery_candidates_pending_domain` | a *different* batch/actor already staged this domain as pending | **conflict**: report the domain(s) in `already_staged` with `same_batch=False`, write nothing, attach no evidence |

A recovery is never assumed from the violation alone: the rows are fetched and
compared. If the fetched set is not exactly the expected domains, or a row is no
longer `pending_review`, the run fails (`PartialRecoveryError`) instead of
pretending the batch was recovered.

## 5. Evidence completeness and idempotency

Evidence is written by verified candidate id, never by array position.

- **All evidence already present** → zero inserts, `evidence_staged = 0`, success.
- **None present** → all inserted.
- **Some present** (an interrupted run) → only the missing `(candidate_id, url)`
  pairs are inserted; the existing rows are left alone.
- **Duplicate URLs within one input** → collapsed by `(candidate_id, url)` before
  any request.
- **Missing ids/domains in the insert response** → `StagingContractError`; nothing
  is guessed.
- **Response order** → irrelevant; association is by domain → id.
- **Rows that don't belong to the batch/actor** → invisible to the scoped GET, and
  if nothing is visible the run fails rather than adopting them.
- **Cannot tell whether evidence is complete** (the evidence GET fails) →
  `EvidencePersistenceError`, never success.
- The `(candidate_id, url)` unique index is the backstop: if a concurrent writer
  wins the race, the failed insert triggers a re-read and the run succeeds only if
  every desired pair is now present.

## 6. RLS and identity guarantees

- Every read and write goes through the **same injected client** with the same JWT
  as the original staging call; the sink holds no second credential and constructs
  no other client.
- Recovery is scoped by `discovered_by_agent_identity = eq.<actor>`, so an agent
  can only ever adopt its own rows. The actor comes from the runtime and is
  validated (`ACTOR_PATTERN`), never from provider data.
- The path performs INSERT and SELECT only. There is no UPDATE, no DELETE, and
  `service_role` appears nowhere in `discovery/ports.py` (asserted by test).
- Operators keep Human Gate 1: nothing in the write path sets `reviewed_*` or
  `converted_company_id`.
- **If RLS hides a legitimate row, the run fails closed** with
  "the batch unique constraint fired but no rows are visible for this batch_key +
  actor" rather than weakening the policy or bypassing the client.

## 7. Tests and results

```
python -m pytest tests/test_discovery_persistence.py tests/test_discovery.py
  51 passed in 0.16s

python -m pytest -q            (full project)
  382 collected → 0 failed, 0 errors, 376 passed, 6 skipped

python scripts/discovery_cycle.py
  ran end to end (offline), wrote reports/eryndex/evidence/discovery-cycle-sample.json
```

The 6 skips are `tests/db/test_security_fixes_db.py` (`ERYNDEX_DB_URL` unset) —
skipped, not passed. The regression tests demanded by §5 are all present:
initial success · evidence failure · second-run recovery · recovery completes
evidence · third run duplicates nothing · partial evidence completed · same-batch
violation still reaches evidence · other-batch collision reported as conflict ·
recovery never adopts another actor's rows · no service-role path · missing ids
fail closed · response order irrelevant · partial batch fails as partial · existing
company and identity conflict never written.

**Live behaviour is not verified.** These are fake-client tests: they prove the
sequence and the guards, not the RLS policies, the PostgREST filter semantics or
the real unique indexes. Those need the migration applied and a discovery agent
identity.

## 8. Remaining runtime limitations

1. **Migration unapplied** — the tables do not exist yet, so nothing here has run
   against a database.
2. **No discovery agent identity** in Supabase Auth, so the agent-scoped SELECT and
   the evidence ownership policy cannot be exercised end to end.
3. **PostgREST filter syntax (`eq.`, `in.(…)`) is assumed**, not executed. If the
   deployed API rejects these params the recovery path fails closed rather than
   silently misbehaving — but that has not been observed.
4. **Concurrency** is modelled by the constraint, not by real concurrent writers;
   the re-read-on-conflict branch is exercised only through the code path.
5. **No transaction across the two stages.** Candidates can exist without evidence
   between stage 1 and 2 — that is exactly the state this directive makes
   recoverable rather than eliminating. It cannot be eliminated without an RPC that
   writes both in one transaction, which would be a schema/API change and is out of
   scope.

## 9. Compliance

- No migration was applied; the pending Discovery migration, the applied hardening
  migration and the core schema are byte-identical to `db502ed`.
- No Production access, no Docker operation, no Auth identity created.
- IPP v2, Refokus history, qualification thresholds and the outreach gate are
  untouched.
- Working tree clean; commit pushed to `feat/eryndex-platform-transform`.

## 10. Verdict

**READY FOR OWNER MIGRATION APPROVAL.**

The interrupted-write state is now recoverable without duplicating candidates or
citations, a cross-batch collision is distinguished from a same-batch retry, an
incomplete recovery is always a failure, and no guard was weakened to achieve it.
