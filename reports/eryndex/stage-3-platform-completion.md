# Stage 3 — the loop runs end to end in Development

**Range:** `c330deb` → `28e83aa` (plus the audit fixes that follow)
**Database:** self-hosted PostgreSQL 17.11, `eryndex-postgres` on `127.0.0.1:5433`
**Suite:** 671 passed, 0 skipped, 0 failed

## What this stage added

| Layer | Before | Now |
|---|---|---|
| Research | no executor; the agent path received findings as arguments | `research/executor.py` + `scripts/research_run.py`: provider → validation → report + findings + activity → task completed, idempotent per report version |
| Evidence identity | five shapes, synthetic `evidence-{i}` ids, excerpts dropped | one bridge that preserves `research_findings.id`, carries excerpt and source URL, and marks synthetic ids so a caller can refuse them |
| Qualification | engine only; nothing could persist | Gate 2: `qualifications persist` (qualifier identity), `approve`/`reject` (human), reviewer bound by the database |
| Outreach | gate logic with fakes | three real ports (qualification, suppression, draft writer) + a deterministic draft generator that cites real finding UUIDs |
| Runtime | gates and idempotency only | `TaskSource`/`Runtime` contracts, `PostgresTaskSource` with a single-statement CAS claim, `ManualRuntime`, `RetryingRuntime` with bounded retries, timeout and escalation |
| Observability | nothing | run/trace ids, one-line JSON events, redaction shared with the gate, run summaries |
| Database | 4 migrations | 7: reviewer binding + Gate 1 audit triggers, integrity constraints (H-06, report version, one primary domain, indexes), and the fix the one-primary index forced |

## The live run

`scripts/e2e_lifecycle.py` — one pass, real identities, real RLS, no service role:

```
candidate.staged       14415d02…  e2e-6f113842.example.com
company.registered     e99255fa…  created=True duplicate=False
research.persisted     ec9f2549…  9 findings, task completed
qualification.persisted 071742b7… score 52.00, review_status=draft
qualification.approved review_status=approved, reviewed_by=c549b7f9…, tier=tier_3_deferred
company.qualified      lifecycle_stage=qualified, tier=tier_3_deferred
recipient.ready        contact 50dca23f…, campaign f6fe1110…
draft.created          54640348…  allowed_because: qualified, human-approved, evidence-backed, not suppressed
draft.approved         approved_by_human_id=c549b7f9…, dispatch=disabled
dispatch.authorized    activity a41aae03…, dispatch=disabled
dispatch.blocked       True
message.final          approval_status=approved, sending_status=draft, evidence_count=6
```

The last line is the one that matters: the loop advances to *authorised*, and the message still
cannot leave, because no sender exists and the database will not accept a sending state without an
approval it can attribute to a human.

## Defects the integration run found (and fixed)

| Defect | How it surfaced | Fix |
|---|---|---|
| Every Python list was bound as `jsonb`, so `text[]` and `uuid[]` columns failed with 42804 | first real scorecard insert (`disqualification_reasons`) | the client now reads column types from the catalogue and binds arrays natively |
| `overall_score` arrives as `Decimal`, and the outreach adapter demanded `int/float` | first real draft: `qualification_lookup_failed` on a perfectly valid row | the adapter accepts `Decimal` (NUMERIC is a number) |
| The one-primary-domain index broke changing a company's primary domain | the DB security suite, which is exactly what it is for | the sync trigger now demotes before it inserts; regression test added |
| A qualifier could not read a company without an active task | the e2e run: `company not visible` | not a bug — task-scoped RLS working; the run assigns the qualification task first, and the refusal message says so |
| `psycopg` returns `UUID` objects, which do not survive `argv` | the e2e CLI call | stringified at the boundary |
| The historical-research test asserted "the table is empty" | failed once real research existed | scoped to the Refokus company, which is what the claim was always about |

## Still open

| Gap | Consequence |
|---|---|
| **No live research provider** (Crawl4AI endpoint, GPT Researcher credentials) | research content is fixture-only; everything downstream is real |
| **No discovery-agent runner** (roadmap C1) | candidates are staged by the operator; the agent path is policy-verified, not executed |
| **No drafter identity** | drafts are written by the operator through the human policy, and the row says so |
| **Agents cannot run the suppression check** (`suppression_registry` has no agent policy) | operator is the only supported drafting identity |
| **Two CLIs** — `operations.cli` for G1/G2, `scripts/outreach_cli.py` for G3/G4 | works, but it is not yet one surface |
| **No scheduler / Hermes adapter** | tasks are claimed by a manual runtime, not by a daemon |
| **Production posture** | backups, TLS, `pg_upgrade` and a rehearsed restore are unowned |

## Verdict at the end of Stage 3

`PARTIALLY READY — EXPLICIT BLOCKERS REMAIN`, and materially closer than at the start: the
lifecycle now executes end to end in Development with every human gate recorded and every agent
restriction enforced by the database. What keeps it from "operationally ready" is research content
(no live provider), the discovery-agent runner, and operational posture — not the workflow code.
