# Stage 2 — Discovery: Human Gate 1 and real policy verification

**Starting HEAD:** `447dc947166abed168ba0821f0f9c3314dc4524d`
**Checkpoints:** `722a4ff` (roadmap + reconciled board), this commit (Gate 1 + operator surface + policy probes)
**Environment:** Development `vyfqmrdznmocnmzyjjcj` only. Production never contacted.

**Verdict: Gate 1 is implemented and live-verified; the discovery-agent write path is policy-verified but not live-verified (OW-1).**

---

## 1. What did not exist before this stage

Three independent audits agreed on the same hole: Discovery could stage a candidate and nothing could ever move it out of `pending_review`. No review code, no registration with attribution, no `converted_company_id`, no operator entry point, and no way to see a candidate at all.

That is not a cosmetic gap. `uq_discovery_candidates_pending_domain` is partial on `status = 'pending_review'`, so every candidate that stays pending **permanently shadows its domain** for later runs. Discovery was writing rows that poisoned its own future batches.

## 2. What was built

### `operations/` — the human side (new package, 4 modules)

| File | Responsibility |
|---|---|
| `session.py` | Signs in with the operator credential, verifies `app_metadata.user_role ∈ {operator, admin, founder}` **before** any write, applies the Development environment guard, and refuses an agent identity outright. An explicit `environ` mapping is authoritative — it does not silently inherit `.env`, which is what makes the credential leak in the first test run impossible. |
| `candidates.py` | List (pending queue by default, `any` for history), show, approve, reject, mark duplicate, link company. Every transition carries `reviewed_by_human_id` + `reviewed_at` in one UPDATE, because `chk_discovery_candidate_review_pair` permits nothing else. Idempotent: a repeated decision returns the row unchanged and keeps the original reviewer. |
| `registration.py` | Approved candidate → `companies` row. Duplicate-checked against both `companies.primary_domain` and `company_domains.domain`; a match records `status='duplicate'` and links the existing company instead of creating a second one. Provenance (`candidate_id`, `batch_key`, discovering agent, reviewer, operator) is written into `discovery_details`. Idempotent through `converted_company_id`. Refuses to guess `primary_segment`. |
| `cli.py` | `whoami`, `candidates list\|show\|approve\|reject\|register`, `--json`. Refusals print a reason and the next action, never a traceback. |

### `scripts/gate1_fixture.py`

A synthetic candidate for verification, constrained to `*.example.com` (RFC 2606), `source_name='synthetic_fixture'`, `[SYNTHETIC]` name prefix. `--remove` refuses any row that does not satisfy all three.

### `scripts/rls_probe.py`

Eleven adversarial cases executed against Development, each in `BEGIN … ROLLBACK` with `request.jwt.claims` set to the claims the policies read and `SET LOCAL ROLE authenticated|anon`. Nothing persists, no policy is changed, no `service_role` is used.

### One correction in existing code

`discovery/ports.py` raised `StagingUnavailable` telling the operator to apply a migration from `pending/` — a directory that has said "Nothing is pending" since 2026-11-15. The message now names the applied migration and lists the plausible causes (grant, schema cache, wrong project).

## 3. Live verification — the Gate 1 loop, in Development

Real operator JWT, real PostgREST, real RLS, no `service_role`:

| Step | Command | Result |
|---|---|---|
| identity | `operations.cli whoami` | `operator@partnership.internal`, role `operator`, user `e1cd1ff2-…` |
| stage | `scripts/gate1_fixture.py` | candidate `f5339a1d-…`, `gate1-fixture-20261120161048.example.com` |
| queue | `candidates list` | 1 pending candidate |
| inspect | `candidates show <id>` | full row plus its one evidence record |
| approve | `candidates approve <id>` | `status=approved`, reviewer recorded |
| register | `candidates register <id>` | company `03ed5e71-…` created |
| idempotency | `candidates register <id>` again | "already registered … nothing created" |
| reject | `candidates reject <b4d77319-…> --reason …` | `status=rejected`, reason on the row |
| refusal | `candidates approve <b4d77319-…>` (already rejected) | refused, names the allowed source status |

Independent SQL read-back (not through PostgREST):

```
candidate f5339a1d  status=approved  reviewer=e1cd1ff2-…  company=03ed5e71-…
candidate b4d77319  status=rejected  reviewer=e1cd1ff2-…
company   03ed5e71  domain=gate1-fixture-…example.com  source=discovery_candidate
                      discovery_details.candidate_id=f5339a1d-…
domain    gate1-fixture-…example.com  is_primary=true  company=03ed5e71-…
counts    1 report / 13 findings · 2 companies · 2 candidates
```

Refokus history is unchanged (1 report, 13 findings). One synthetic company and two synthetic candidates now exist; §6 records how to remove them.

## 4. Policy verification — 11/11

| Case | Expected | Result |
|---|---|---|
| A agent stages its own candidate | allowed | PASS |
| B agent forges another identity | 42501 | PASS |
| C researcher stages a discovery candidate | 42501 | PASS |
| D agent approves its own candidate | row stays `pending_review` | PASS |
| E agent reads another agent's candidate | 0 rows | PASS |
| F anon reads candidates | 42501 | PASS |
| G operator approves a pending candidate | allowed | PASS |
| H operator approves without the review pair | 23514 | PASS |
| I agent attaches evidence to an approved candidate | 42501 | PASS |
| J agent writes to `companies` | 42501 | PASS |
| K operator visibility (control) | allowed | PASS |

**Case D was wrong the first time, and the correction matters.** It asserted that an agent UPDATE raises. It does not: RLS filters `UPDATE` rows rather than raising, so an agent's approval attempt silently affects zero rows and succeeds from the client's point of view. The probe now asserts the row is still `pending_review`. Had it been accepted as an "allowed" outcoming, it would have read as a critical vulnerability.

What this does **not** prove: real JWT issuance and PostgREST filter/representation semantics for the agent path, because no `subagent_type='discovery'` identity exists — the claims here are simulated inside the transaction. That remains OW-1.

## 5. Tests

```
python -m pytest                      411 passed, 6 skipped   (417 collected)
python -m pytest tests/test_operations_gate1.py   17 passed
```

The 6 skips are the `ERYNDEX_DB_URL` policy suite, unchanged. New coverage in `tests/test_operations_gate1.py` models the review-pair constraint, `companies.primary_domain` uniqueness and `company_domains.domain` uniqueness inside the fake client, so a test cannot pass by ignoring a rule the database enforces. Segment absence fails closed; an agent identity cannot open a session; a missing configuration names variables and never values.

## 6. Synthetic data left behind

| Row | Value | Remove with |
|---|---|---|
| candidate (approved, converted) | `f5339a1d-f609-481d-a686-f2d6de4eaadb` | `python scripts/gate1_fixture.py --remove f5339a1d-…` |
| candidate (rejected) | `b4d77319-3f6d-4973-8340-ed6e2a1302fb` | `python scripts/gate1_fixture.py --remove b4d77319-…` |
| company | `03ed5e71-e71a-4a75-aef0-545296dfdd27` | removed with its candidate (the script deletes the company that candidate created) |

Left in place deliberately: the directive forbids destructive cleanup without an approved plan, and these rows are the evidence for §3.

## 7. Remaining gaps in this workstream

| ID | Gap | State |
|---|---|---|
| C1 | A production runner that builds `SupabaseCandidateSink` from config and runs a real batch as the discovery agent | blocked on OW-1 |
| C3 | Dedupe fed from real CRM data | open: `existing_companies` is caller-supplied, and a discovery agent cannot read `companies` (task-scoped policy) |
| C5 | Gate 2/3/4 commands | not built; Gate 1 only |
| K1 | Cross-agent read policies (`research_reports`, `lead_qualifications`) and the missing `company_domains` suppression filter | proposals, not applied |
| B3 | Audit triggers on `companies` and discovery review | proposal, not applied — Gate 1 attribution is column-level only |

## 8. Compliance

No migration applied or edited, no SQL executed outside `BEGIN … ROLLBACK` probes and the two fixture inserts the directive's "controlled synthetic batch" allows, no Production contact, no Auth identity created, no `service_role`, no RLS or grant change, no Outreach dispatch, no Discovery provider run, no Docker operation.
