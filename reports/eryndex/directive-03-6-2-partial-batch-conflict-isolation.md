# Directive 03.6.2 — Partial Batch Conflict Isolation

**Starting commit:** `e5a26bc`
**Final commit:** the commit carrying this report (`git log -1`)
**Migration/SQL:** none authored, changed or applied. No Production, Docker or Auth change.

---

## 1. Root cause

Staging inserted every candidate in **one** POST. PostgreSQL makes a multi-row INSERT
atomic, so a single `uq_discovery_candidates_pending_domain` violation — caused by
*another* batch owning one domain — rejected **all** rows. The service turned that
single violation into a whole-batch `AlreadyStaged`, so beta.com and gamma.com were
never persisted even though nothing was wrong with them, and `accepted` still listed
all three, which read like success.

The same atomicity also made partial same-batch recovery unsafe to model: the code
required the persisted set to equal the whole batch, because a partial batch could not
otherwise arise.

## 2. Strategy and rationale

**Per-candidate inserts, classified individually.** The sink now posts one row at a
time and maps each outcome:

| Insert result | Classification | Effect |
|---|---|---|
| success | staged | id captured from the returned row |
| `uq_…_batch_domain` | same-batch recovery | scoped GET, verified, real id reused |
| `uq_…_pending_domain` | cross-batch conflict | reported, nothing adopted, no evidence |
| missing table | `StagingUnavailable` | actionable error |
| anything else | `CandidatePersistenceError` | explicit failure carrying partial counts |

Why this and not something cleverer: a multi-row insert with per-row fallback doubles
the code paths and still needs the same classification; `ON CONFLICT DO NOTHING`
would hide which rows conflicted and would not distinguish "mine" from "someone
else's"; a transactional RPC does not exist and a schema change is forbidden. One row
per request is the simplest strategy whose outcome is *per candidate by construction*.
Cost: N requests for N candidates, with batches capped at 50 — acceptable for a
discovery run, and the honest alternative (one opaque error for the whole batch) is
what this directive exists to remove.

## 3. Files changed

| File | Change |
|---|---|
| `discovery/ports.py` | per-candidate `write()`; `_recover_candidate()` replaces `_recover_batch()`; `StagingResult.conflicts`; new `CandidatePersistenceError`; evidence skips conflicted domains explicitly |
| `discovery/service.py` | conflicts come from the result instead of an exception; `ok` redefined; imports tightened |
| `discovery/__init__.py` | exports the new types and constraint constants |
| `tests/test_discovery_persistence.py` | fake client honours the `domain` filter; new isolation/recovery/outcome tests |
| `reports/eryndex/directive-03-6-2-partial-batch-conflict-isolation.md` | this report |

## 4. Same-batch recovery semantics

Unchanged in guarantees, narrowed in scope: recovery is now **per candidate**.

- GET scoped by `batch_key` + `discovered_by_agent_identity` **and** `domain=eq.<domain>`.
- Exactly one row must come back; zero rows → `StagingContractError` ("no row is
  visible … refusing to attach evidence"), more than one → `PartialRecoveryError`.
- The row must carry `id` + `domain` and must still be `pending_review`, else
  `PartialRecoveryError`.
- The returned id is the only id evidence can use.

A batch may now legitimately be **partially** staged: the retry recovers the rows that
landed and stages the ones that did not, in one run. The all-or-nothing recovery
assumption is gone because per-candidate persistence makes partial state normal.

## 5. Cross-batch conflict behaviour

```
alpha.com  owned by another pending batch → reported `domain_staged_elsewhere`
beta.com   not staged                     → staged
gamma.com  not staged                     → staged
```

- The conflicting candidate appears **only** in `already_staged`, with its domain,
  `same_batch: false` and the constraint that fired.
- No evidence is attached to it: conflicted domains are passed to the evidence writer
  as an explicit skip set, so the exclusion is deliberate and visible rather than an
  accident of missing ids.
- Ownership is never overwritten: nothing updates the foreign row, and the actor is
  never rewritten.
- A race is handled the same way — the classification comes from the constraint that
  actually fired for *that* row, so a failed insert cannot mislabel unrelated
  candidates as conflicts.

## 6. Outcome and counter semantics

| Field | Meaning |
|---|---|
| `accepted` | candidates that passed discovery identity/dedup checks — **not** proof of persistence |
| `staged` | rows newly created this run |
| `recovered` | existing same-batch, same-actor rows verified and reused |
| `already_staged` | candidates owned by another pending batch (domain + reason + constraint) |
| `evidence_staged` | evidence rows newly inserted this run |
| `rejected` | input that could not be identified or was excluded by the directive |
| `duplicates_in_batch`, `existing_matches`, `conflicts` | unchanged discovery-level outcomes |

`ok` is now defined as: **no rejected input and every accepted candidate settled**
(`staged + recovered + already_staged == accepted`). It no longer means "we found
something", and it cannot imply success after an unknown persistence failure because
those raise `CandidatePersistenceError` instead of returning a result. Both directions
are tested: a mixed batch with one foreign conflict is `ok`, a batch with an
unidentifiable candidate is not.

## 7. Evidence guarantees (carried forward from 03.6.1)

Associated by verified domain → database id, never by position · existing
`(candidate_id, url)` pairs read before inserting · only missing pairs written ·
duplicate URLs within one input collapsed · unreadable evidence table means
completeness unknown ⇒ failure · unique-constraint race re-reads and succeeds only if
every desired pair is verified · evidence for a conflicted or unverified candidate is
never written.

## 8. Tests and results

```
python -m pytest tests/test_discovery_persistence.py tests/test_discovery.py
  60 passed in 0.20s

python -m pytest -q          (full project)
  391 collected → 385 passed, 6 skipped, 0 failed, 0 errors

python scripts/discovery_cycle.py
  ran offline end to end; regenerated reports/eryndex/evidence/discovery-cycle-sample.json
```

New/extended cases, mapped to §6 of the directive: one conflict + two unrelated
candidates staged · only the conflicting candidate reported · verified ids and evidence
for the staged pair · counters never mixing conflict with staged/recovered · no
evidence on the foreign candidate · partial same-batch state recovered per candidate ·
retry creates no duplicates · recovered is not counted as staged · same-batch collision
does not stop later candidates · foreign actor's row never adopted · missing id/domain
fails closed · unknown insert failure raises with partial counts and writes no evidence
· response order irrelevant · existing company and identity conflict stay outside the
persistence path · no UPDATE/DELETE/service-role (asserted by source scan and by the
injected-client identity check).

No 03.6 or 03.6.1 regression: payload/identity validation, retry recovery, evidence
idempotency and outcome serialization are all still covered and green.

## 9. Remaining unverified runtime assumptions

1. **No live database.** These are fake-client tests: the RLS policies, the real
   `(candidate_id, url)` index and PostgREST filter semantics (`eq.`, `in.(…)`) are
   read from the migration and client, never executed.
2. **N-request cost.** Per-candidate inserts mean one round trip per candidate plus
   one evidence read and one evidence write. For 50 candidates that is ~100 requests;
   acceptable now, worth batching only if a run ever gets slow.
3. **No cross-stage transaction.** A crash between the candidate and evidence stages
   leaves a partially evidenced batch — recoverable by design since 03.6.1, not
   eliminated.
4. **Concurrency** is modelled by constraints, not by real concurrent sessions.

## 10. Compliance

No migration, SQL or schema change. No Production access, no Docker operation, no Auth
identity. IPP v2, Refokus history, qualification thresholds and the outreach gate are
untouched.

## 11. Verdict

**READY FOR OWNER MIGRATION APPROVAL.**
