# Directive 03.6 — Discovery Persistence Contract & Migration Readiness

**Date:** 2026-11-15
**Scope:** `discovery/{service,ports,identity,__init__}.py`, `tests/test_discovery_persistence.py`, the pending discovery migration
**Applied:** nothing. The migration is still unapplied and no database write was attempted in this increment.

---

## 1. Before / after persistence contract

| Field | Before | After | Why |
|---|---|---|---|
| `batch_key` | never sent (column exists, always NULL) | computed **before** the write, put on every accepted row | without it the unique `(batch_key, domain)` index could not fire, so a retry inserted a second copy |
| `discovered_by_agent_identity` | never sent → **NOT NULL violation** | taken from a required `actor` argument, validated against `^[a-z0-9][a-z0-9._:-]{2,63}$` | it is compared to `current_agent_identity()` in the RLS policy, so it must come from the runtime, never from provider data |
| `discovery_source` | invented column, absent from the table | removed; the real column is `source_name` (already populated) | the insert would have failed with `PGRST204 unknown column` |
| `status` | hard-coded `pending_review` | unchanged | matches the enum default and the policy's requirement |
| evidence rows | passed to `write()` and silently discarded | inserted in stage 2, keyed by the `candidate_id` the database returned | citations were being lost |
| evidence `domain` | part of the payload | an internal association key only, stripped before insert | the evidence table has no `domain` column |

The payload is now checked against an explicit column set (`CANDIDATE_COLUMNS`, `EVIDENCE_COLUMNS`); anything else raises `StagingContractError` instead of being sent or silently dropped.

## 2. Insertion sequence

```
1. validate directive (segments, sources, limit)          — pure
2. fetch candidates from the source port                  — no writes
3. normalize identity, dedupe in-batch                    — pure
4. resolve against existing companies → match / conflict  — pure, read-only
5. compute batch_key over {campaign, sorted sources, sorted domains}
6. attach batch_key + actor to every accepted row
7. STAGE 1  POST discovery_candidates  (Prefer: return=representation)
8. verify one {id, domain} came back per row → map domain → candidate_id
9. STAGE 2  POST discovery_candidate_evidence  (candidate_id, url, excerpt, retrieved_at, provider)
10. report staged = number of candidate rows the database confirmed
```

Step 8 is the contract boundary: if the response is missing ids, or is short a row, the write fails with `StagingContractError`. Evidence is never paired by array position, and never attached to a guessed id.

## 3. Idempotency and retry semantics

- **Key computed before writing** (`compute_batch_key`), deterministic over `campaign_id` + sorted `sources` + sorted `domains` — verified order-independent by test.
- **Same batch re-run** → `uq_discovery_candidates_batch_domain` fires → `AlreadyStaged(constraint, rows)` → the run reports `already_staged`, `staged = 0`. A conflict is never counted as a staged row.
- **Same domain, different campaign** → `uq_discovery_candidates_pending_domain` fires → also `AlreadyStaged`, with `same_batch = False` so the caller can tell the two apart.
- **Evidence failure after candidates** → `EvidencePersistenceError(staged_candidates=n, message)`; the run is not reported as success. Retry is safe because candidates are unique per `(batch_key, domain)` and evidence per `(candidate_id, url)` — a retry re-attempts only what is missing. That second uniqueness is **new in this directive** (see §5).
- The four outcomes stay distinct in `DiscoveryOutcome`: `duplicates_in_batch`, `existing_matches`, `conflicts`, `already_staged` — plus `rejected` for unidentifiable/excluded input.

## 4. RLS and execution identity assumptions

| Actor | May do | Source |
|---|---|---|
| Discovery agent (`is_agent_type('discovery')`) | INSERT candidates with `status='pending_review'`, own identity, and `reviewed_*`/`converted_company_id` NULL; SELECT its own rows; INSERT evidence for its own pending candidates | migration policies `agent_insert_discovery_candidates`, `agent_select_own_discovery_candidates`, `agent_insert_discovery_evidence`, `agent_select_discovery_evidence` |
| Human operator | everything, including review and conversion | `human_operator_all_discovery_candidates` / `…_evidence` (`FOR ALL`, `is_human_operator()`) |
| `anon` | nothing | `anon_deny_*` (`USING (false)`) |

Tests parse the migration and assert: no `agent_*` policy is `FOR ALL`, none is `FOR UPDATE`/`FOR DELETE`, and the candidate INSERT policy pins `status = 'pending_review'`, `reviewed_by_human_id IS NULL` and `converted_company_id IS NULL`. No service-role bypass was added anywhere; `service_role` appears in neither the migration nor the sink.

**Missing capability (documented, not worked around):** no discovery agent identity exists in Supabase Auth, so the agent path cannot be exercised live. Creating one requires an admin operation on the project, which this increment is not authorised to perform.

## 5. Migration review findings

Reviewed against the deployed core schema (`crm/schema.sql`), the **applied** hardening migration `20261114194622_hardening_batch1.sql`, and the corrected application payload.

| Check | Result |
|---|---|
| Referenced objects | `companies(id)` FK, `is_human_operator()`, `is_agent()`, `is_agent_type(text)`, `current_agent_identity()`, `update_updated_at_column()` — all present in the deployed schema (verified earlier at `crm/schema.sql:18,63,83,93,104,336`) and untouched by the hardening batch |
| Enum | `discovery_candidate_status` is created by this migration; the five values cover staged/approved/rejected/duplicate/conflict |
| Constraints | `chk_discovery_candidate_domain_format` matches what `normalize_domain()` produces (lowercase, no commas, non-empty); `chk_discovery_candidate_review_pair` makes a half-reviewed row impossible |
| Uniqueness / retries | `uq_discovery_candidates_batch_domain` + partial `uq_discovery_candidates_pending_domain` (pending only), both relied on by §3 |
| **Change made** | added `uq_discovery_candidate_evidence_url (candidate_id, url)`. Without it, the stage-2 retry in §3 would duplicate every citation; with it, a retry is exactly idempotent. Recorded here and in the migration comment |
| RLS | see §4; agent policies are INSERT/SELECT only |
| Timestamps | `created_at`/`updated_at` default `clock_timestamp()`, `trg_discovery_candidates_updated_at` reuses the existing function |
| Transactionality | single `BEGIN … COMMIT`; a partial application is impossible |
| Rollback | drop evidence table, candidate table, then the type (`L152-165`); staged rows are destroyed, so export before rolling back |
| Applied objects | the migration names no object created by `20261114194622`, and that batch names none created here — they are independent and order-agnostic |

The historical core migration and the applied hardening migration were **not** modified.

## 6. Tests

New file `tests/test_discovery_persistence.py` — **26 tests**, fake PostgREST client, migration parsed as text:

payload equals the migration's columns · evidence payload equals its columns · batch key computed before persistence and carried on every row · key order-independent · actor taken from the execution context even when the payload lies · five invalid identities fail closed with nothing staged · evidence associated with returned ids (and the domain key stripped) · missing ids fail instead of guessing · evidence-write failure is not success · both unique constraints map to `already_staged` with `same_batch` distinguished · existing-company match writes nothing · domain conflict writes nothing · no agent policy is `FOR ALL`/`UPDATE`/`DELETE` and the insert policy pins the review columns · operator gate policy retained · unknown column (`discovery_source`) rejected · JSONL sink still works.

```
tests/test_discovery_persistence.py + tests/test_discovery.py   50 passed
full suite                                                      381 collected → all pass, 6 skipped
```

The 6 skips remain the database tests (`ERYNDEX_DB_URL` unset) — skipped, not passed. `scripts/discovery_cycle.py` was updated for the mandatory `actor` and re-run successfully.

**Transient failure, reported rather than hidden.** One full-suite run during this increment errored on all six `agents/researcher/test_researcher_slice.py` cases. Those tests authenticate against the **live** Supabase project, so they fail when the pooler does; an immediate rerun was green (381 collected, all pass) with no code change in between. This is a pre-existing hazard in the suite — network-dependent tests reported as red/green rather than skipped — and it is not caused by this increment. Left as-is here (out of scope) and worth a follow-up: gate them on a reachability probe and skip with a reason.

## 7. Remaining blockers

1. **Migration still unapplied** — needs owner approval; this directive explicitly did not apply it.
2. **No discovery agent identity** in Supabase Auth, so the agent-scoped policies cannot be exercised end-to-end. A human operator can insert (the policy allows it), which is enough to prove the table works but not the agent path.
3. **No DSN**, so the six database tests remain skipped; the RLS assertions here are static (migration text), not executed.
4. **PostgREST behaviour is inferred from the client contract**: `integrations/supabase/client.py` already sends `Prefer: return=representation`, so stage 1 returns the rows — but that has never been executed against the new table.
5. **Evidence `provider`** is whatever the source declared; it is not validated against an allowlist.

## 8. Verdict

**READY FOR OWNER MIGRATION APPROVAL.**

The application payload now matches the migration column-for-column, the identity is trusted and mandatory, evidence is persisted against verified ids, retries cannot duplicate candidates or citations, and an evidence failure is reported as a failure. What is not proven is runtime behaviour: the table has never existed, so the RLS policies and the `Prefer: return=representation` round-trip are verified by reading, not by execution.
