# Outreach Workflow — drafting stage (Workflow 4, gates G3/G4)

**Date:** 2026-11-14
**Scope:** `Qualified Lead -> Human Approval -> Suppression Check -> Draft Creation`
**Code:** `outreach/ports.py`, `outreach/gate.py`, `outreach/dispatch.py`
**Tests:** `tests/test_outreach_gate.py` (134 tests after the adversarial pass; no database, no network)
**Database touched:** none. No migration applied, no row read or written, no Supabase call made. Every "database-tested" column below is `no` by construction, not by omission.

> **Superseded in part.** This document was written with the first implementation. Two adversarial
> passes then changed the gate: the current closure state, every finding, and which guarantees are
> verified versus read-from-schema are in `reports/eryndex/outreach-adversarial-review.md` and
> `reports/eryndex/security-closure-matrix.md`. Where this file still describes an older design
> (idempotency key composition, provenance columns), the matrix is authoritative.

---

## 1. What this stage is and is not

It turns an already-approved qualification into **at most one internal draft**. It does not approve anything (that is G3, a human action in `orchestration/workflow.py`), and it cannot send anything (G4, a separate human action that does not exist yet).

The two human gates are reused, not re-implemented: `GATES["G3"]` moves `outreach_drafted -> outreach_approved` with `(message_id, reviewer, decision)`, `GATES["G4"]` moves `outreach_approved -> engaged` with `(message_id, reviewer, authorized_at)` and `separate_action=True`. This package produces the `OUTREACH_DRAFTED` state and stops.

```
                      ┌──────────────────────────────────────────────┐
 lead_qualifications  │ QUALIFIED  (companies.lifecycle_stage='qualified') │
 review_status=approved└──────────────────┬───────────────────────────┘
                                          │  evaluate_draft_eligibility()  ← pure, fails closed
                                          ▼
                       ┌──────────────────────────────────────┐
                       │ eligible?  ──no──► REJECTED (no row) │
                       └──────────────┬───────────────────────┘
                                      │ yes
                                      ▼
                       ┌──────────────────────────────────────┐
                       │ key = content_key(company, channel,  │
                       │        event, sequence_step)         │
                       └──────────────┬───────────────────────┘
                        existing? ─yes─┴─► ALREADY_EXISTS (no row)
                                      │ no
                                      ▼
                  outreach_messages  approval_status='draft'
                                     sending_status='draft'
                                     approved_*=NULL, sent_at=NULL
                                      │
                                      ▼
                       OUTREACH_DRAFTED ──► [G3 human] ──► OUTREACH_APPROVED
                                                             │
                                                             ▼
                                                     [G4 human + a sender
                                                      that does not exist]
```

### States and transitions owned here

| From | To | Trigger | Reversible? |
|---|---|---|---|
| `RESEARCHED` (scorecard drafted) | `QUALIFIED` | gate **G2**, human operator — not this package | n/a |
| `QUALIFIED` | `OUTREACH_DRAFTED` | `create_draft()` → one `outreach_messages` row, `draft/draft` | yes: a draft can be edited or rejected by a human (G3) |
| `QUALIFIED` | *unchanged* (rejection) | `evaluate_draft_eligibility()` returns codes | no state is written at all |
| `OUTREACH_DRAFTED` | `OUTREACH_DRAFTED` (no-op) | repeated identical request returns `already_exists` | yes |
| `OUTREACH_DRAFTED` | `OUTREACH_APPROVED` | gate **G3**, human operator — **not built here** | approval invalidates on material edit (DB trigger) |
| `OUTREACH_APPROVED` | `ENGAGED` | gate **G4** + a delivery integration — **not built, not reachable** | no |

---

## 2. Gate preconditions

Every row is checked before any write. `evaluate_draft_eligibility()` is pure: it reads through two ports and returns a decision.

| # | Precondition | Rejection code if unmet | Enforced in |
|---|---|---|---|
| 1 | `company_id`, `contact_id`, `outreach_event` non-empty; `channel` ∈ `outreach_channel_type`; `sequence_step >= 1`; model ∈ `commercial_model_type` | raises `DraftRequestError` (structural: the request cannot be evaluated) | app — `DraftRequest.__post_init__` |
| 2 | Body non-empty; subject non-empty for direct channels | `draft_content_invalid` | app |
| 3 | Commercial model is `project_based_wholesale` | `revenue_sharing_prohibited` (rev-share) / `commercial_model_premature` (`monthly_reserved_capacity`) | app — plus `chk_outreach_no_revenue_sharing` in DB |
| 4 | No revenue-share / equity / profit-split language in subject or body | `revenue_sharing_prohibited` | app (DB only blocks the enum value, not the prose) |
| 5 | No team-size, capacity, client-list or track-record claims | `unsupported_delivery_claim` | app only (AGENTS.md §1.2, §8, §11) |
| 6 | A persisted qualification exists for the company | `qualification_missing` | app; **pending migration H-03** adds it to `agent_insert_outreach_messages` |
| 7 | The qualification could be read at all | `qualification_lookup_failed` | app |
| 8 | `review_status = 'approved'` (not `draft`, not `pending_human_review`, not `rejected`, not an unknown value) | `qualification_pending_review` / `qualification_not_approved` | app; DB via the same policy (H-03) + `chk_lead_qual_review_human_gate` |
| 9 | `reviewed_by_human_id` present — "approved with `needs_human_review` and no reviewer" is **not** approval | `human_reviewer_missing` | app; DB via `chk_lead_qual_review_human_gate` (L557-560) |
| 10 | `reviewed_at` present when approved | `provenance_incomplete` | app; DB check constraint |
| 11 | `qualification_tier <> 'disqualified'` and no `disqualification_reasons` | `qualification_disqualified` | app; DB via H-03 policy + `fn_enforce_outreach_human_approval` (L1072-1079) |
| 12 | Tier is one of the four known values | `qualification_unmeasured` | app |
| 13 | Not flagged `insufficient_evidence` (unmeasured ≠ disqualified, ADR-0006) | `qualification_unmeasured` | app only — no column exists |
| 14 | Evidence coverage > 0 across the seven dimensions | `qualification_unmeasured` | app only — no column exists (derived from `dimension_evidence`) |
| 15 | At least one `research_findings` id cited; `qualification_id` present; score in 0..100 | `provenance_incomplete` | app only |
| 16 | No opted-out contact (`contacts.is_opted_out`) | `suppressed_contact` | app; DB re-checks at dispatch |
| 17 | No suppressed domain (`companies.primary_domain` / `company_domains` / `suppression_registry`) | `suppressed_domain` | app; DB re-checks at dispatch (L1245-1253) |
| 18 | Company not suppressed (`companies.is_suppressed`) | `suppressed_company` | app |
| 19 | The suppression check completed | `suppression_lookup_failed` | app |
| 20 | The suppression answer was unambiguous | `suppression_ambiguous` | app |
| 21 | No second draft for the same company/channel/event/step | `already_exists` (not a rejection — nothing is written) | app; **no DB support** (no key column, no unique index) |
| 22 | Persisted row came back as `draft/draft` with no approval or delivery metadata | `DraftPersistenceError` | app read-back; DB `chk_outreach_mandatory_human_gate` (L635-638) |

Rows 1-2 run first, then 6-15, then 16-20. Suppression is not queried once an earlier check has already rejected: the outcome is a rejection either way, and the decision should not depend on a flaky third party.

---

## 3. Rejection codes (closed set)

`outreach.gate.REJECTION_CODES` is the enumeration; `RejectionCode` is the only source. Callers branch on these values.

| Code | Meaning | Test |
|---|---|---|
| `qualification_missing` | no persisted scorecard for the company | `test_missing_qualification_is_rejected` |
| `qualification_lookup_failed` | the read raised (JWT, transport, RLS) | `test_unreadable_qualification_is_a_rejection_not_a_pass` |
| `qualification_unmeasured` | insufficient evidence, zero coverage, or unknown tier | `test_scorecard_without_any_evidence_is_unmeasured_not_deferred`, `test_insufficient_evidence_scorecard_is_unmeasured` |
| `qualification_disqualified` | `disqualified` tier or any disqualification reason | `test_disqualified_tier_is_rejected` |
| `qualification_pending_review` | `draft` or `pending_human_review` | `test_pending_review_qualification_is_rejected` |
| `qualification_not_approved` | `rejected` or an unrecognised status | `test_rejected_or_unknown_review_status_is_rejected` |
| `human_reviewer_missing` | "approved" with no `reviewed_by_human_id`, or still flagged `needs_human_review` | `test_approved_without_a_human_reviewer_is_not_approval` |
| `suppressed_contact` | opted out, or the contact's email/LinkedIn is on the ledger | `test_suppressed_contact_is_rejected` |
| `suppressed_domain` | a domain of the company is on the ledger | `test_suppressed_domain_is_rejected_with_its_own_code` |
| `suppressed_company` | `companies.is_suppressed = true` | `test_suppressed_company_is_rejected_with_its_own_code` |
| `suppression_lookup_failed` | the reader raised, timed out, or returned nothing | `test_suppression_lookup_failure_fails_closed` (×3), `test_missing_suppression_report_is_not_clean` |
| `suppression_ambiguous` | the reader could not attribute the answer, or contradicted itself | `test_ambiguous_suppression_is_rejected`, `test_clear_verdict_carrying_findings_is_ambiguous` |
| `provenance_incomplete` | no finding ids, no `qualification_id`, score out of range, missing `reviewed_at` | `test_evidence_without_finding_ids_is_incomplete_provenance` |
| `draft_content_invalid` | empty body, or empty subject on a direct channel | `test_empty_body_is_rejected_before_any_lookup` |
| `revenue_sharing_prohibited` | rev-share model or rev-share language | `test_revenue_sharing_commercial_model_is_rejected`, `test_revenue_sharing_in_the_body_is_rejected` |
| `unsupported_delivery_claim` | team size / capacity / client list / track record | `test_unsupported_delivery_claims_are_rejected` (×4) |
| `commercial_model_premature` | `monthly_reserved_capacity` in cold outreach | `test_monthly_reserved_capacity_is_rejected_as_premature` |

A decision carries **all** applicable codes (`EligibilityDecision.codes`); `code` is the primary one for logs. That is deliberate — an operator fixing a lead should see every reason, not the first.

### Dispatch (not a rejection code — an absence)

`outreach/dispatch.py` defines `DispatchDisabled`, `dispatch()`, `queue_for_dispatch()` and `DispatchChannel`, all of which raise unconditionally; `DISPATCH_AVAILABLE = False`; there is no HTTP client, no SMTP, no socket, no credential, no environment variable. `outreach.gate` does not import it, and a subprocess test creates a real draft and asserts `outreach.dispatch`, `smtplib` and `http.client` are still absent from `sys.modules`.

---

## 4. Application code vs. database policy

| Control | Today (deployed schema) | After `20261113_hardening_batch1.sql` (H-03) | In this package |
|---|---|---|---|
| Draft requires an approved, non-disqualified scorecard | **not enforced** (H-03) — a drafter could insert for an unqualified company | policy `agent_insert_outreach_messages` gains `EXISTS(... review_status='approved' AND tier <> 'disqualified')` | enforced (rows 6-12) |
| Approved scorecard has a human reviewer | `chk_lead_qual_review_human_gate` (L557-560) | unchanged | enforced (row 9) |
| Draft starts as `draft/draft` with no approval | RLS insert policy + `chk_outreach_mandatory_human_gate` | unchanged | enforced (`DraftPayload.__post_init__`, read-back) |
| No revenue sharing | `chk_outreach_no_revenue_sharing` + insert policy (enum only) | unchanged | enforced on the enum **and** the prose |
| No dispatch without G3 + G4 | trigger requires `approval_status='approved'` + `approved_by_human_id` + `approved_at`; agents cannot approve (L1177-1184) | unchanged | dispatch does not exist |
| Suppression re-checked before transmission | trigger checks the registry at any `sending_status` past `draft` (L1231-1254) | unchanged | checked at draft time too, fail-closed |
| One draft per company/channel/event | **nothing** — no column, no index | not addressed | enforced in code only (row 21) |
| Company must be past `qualified` lifecycle | **nothing** | policy adds `lifecycle_stage IN ('qualified','outreach_queued','in_outreach')` | **not checked** (see gap G3) |

Note on the DB's coverage of "approved but unmeasured": `fn_enforce_lead_qualification_rules` forces `tier='disqualified'` below 40 points, so an all-zero scorecard cannot be approved as `tier_1..3`. The database therefore blocks the extreme case via the tier band, but it cannot express "approved with thin evidence" — only this gate can.

---

## 5. Provenance carried by a draft

`personalization_rationale` (NOT NULL, L606) is the only free-text column that a draft may carry, so the provenance block is written there, and the structured copy travels on `DraftPayload.provenance`:

```
Qualified lead provenance — qualification_id=<uuid>; company_id=<uuid>;
score=72.0/100; tier=tier_2_prospective; reviewed_by_human_id=<uuid>;
reviewed_at=2026-11-13T10:00:00+00:00; rubric_version=2.0.1;
evidence_coverage=1.0; evidence_refs=[<research_findings.id>, ...]
```

* `rubric_version` defaults to `intelligence.ipp.rubric.RUBRIC_VERSION` (`2.0.1`) — the gate does not hard-code a version string.
* `personalization_evidence_ids` (UUID[]) receives only refs that are actually UUID-shaped; non-UUID refs stay in the rationale rather than being cast into an array that would reject them.
* Nothing about the prospect is asserted here that the scorecard did not contain. The rationale describes *our* evidence, not *their* business.

---

## 6. Deliberately not built

| Not built | Why |
|---|---|
| Dispatch / sending (email, LinkedIn, contact form) | G4 is a separate human action; no delivery integration exists; `outreach.dispatch` raises |
| Scheduling / sequencing / `sending_status='queued'` | `queued` is the first transmission-intent state; a drafting stage that can queue has left its mandate |
| Sending credentials, API keys, SMTP config | none exist in this package; a test asserts no credential-shaped string is present |
| Agent identity or Supabase Auth user creation | belongs to provisioning, not to drafting |
| Approval (G3) | human-only; `assert_gate` already encodes it and agents are forbidden |
| LLM drafting / templating | the caller supplies subject and body; the gate's job is to refuse, not to write |
| A Supabase implementation of the ports | that is `integrations/supabase` work and would put a client inside this package; the ports and their failure modes are fixed first |
| Any migration change | H-03 already exists and is unapplied; this package does not touch DDL |

---

## 7. Claim status

`database-tested` is `no` for every row: no test in this increment opened a connection.

| Claim | Implemented | Unit-tested | Contract-tested | Database-tested |
|---|---|---|---|---|
| Eligibility decision is pure and port-driven | yes (`outreach/gate.py`) | yes | yes — payload columns checked against `crm/schema.sql` | no |
| 17 rejection codes, closed set | yes | yes — one test per code | yes — `REJECTION_CODES` == enum | no |
| Missing / pending / disqualified / unapproved qualification rejected | yes | yes | yes — status + tier values read from the schema enums | no |
| "Approved" without a human reviewer rejected | yes | yes | yes — mirrors `chk_lead_qual_review_human_gate` | no |
| Unmeasured scorecard (no coverage) rejected | yes | yes | no — no column to compare against | no |
| Suppressed contact / domain / company rejected with distinct codes | yes | yes | partial — target types are documented, not in the schema | no |
| Suppression error / timeout / ambiguity / empty answer rejected | yes | yes (7 cases) | no | no |
| Draft carries score, tier, reviewer, evidence refs, rubric version | yes (`DraftPayload.provenance`, rationale text) | yes | yes — only real `outreach_messages` columns are written | no |
| `personalization_evidence_ids` holds only UUID-shaped refs | yes | yes | yes — column is `UUID[]` | no |
| Idempotent on `(company_id, channel, event, step)` via `contracts.keys.content_key` | yes | yes (4 cases) | yes — key recomputed independently in the test | no |
| Draft creation cannot reach dispatch | yes | yes — in-process and subprocess `sys.modules` check | yes — AST scan of the gate's imports | no |
| Dispatch raises instead of returning | yes | yes | yes — AST scan for transport/credential imports | no |
| Writer identity comes from the writer, not the payload | yes | yes | no — `drafted_by_agent_identity` documented in the policy | no |
| A writer returning a sent/approved row is an integrity failure | yes | yes | yes — mirrors `chk_outreach_mandatory_human_gate` | no |
| Revenue-share language and delivery claims are refused | yes | yes | no — prose guards have no schema counterpart | no |
| Enums match the deployed types exactly | yes | n/a | yes — parsed out of `crm/schema.sql` | no |

Test counts: `tests/test_outreach_gate.py` → **54 passed**; whole suite → **249 passed, 6 skipped** (the 6 skips are the pre-existing `tests/db` cases that need `ERYNDEX_DB_URL`).

---

## 8. Where the deployed schema falls short

These are the places where the workflow needed something the schema cannot express. Each is implemented as far as it safely can be, and the missing half is named rather than invented.

**S1 — provenance is text plus a UUID array, not a join.** There is no `qualification_id` FK, no `rubric_version`, no `evidence_coverage`. What exists: `personalization_evidence_ids UUID[]` (populated with the cited finding ids — validated as UUID-shaped, though the column has no FK to `research_findings`, so the reference is syntactic) and `personalization_rationale TEXT`. So a message cannot be joined back to the scorecard that justified it except by parsing that text. *Proper fix (needs owner approval):* nullable `qualification_id UUID REFERENCES lead_qualifications(id)`, `provenance JSONB`, and an index on `qualification_id`.

**S2 — no idempotency key column, no unique constraint.** Nothing in the database stops two identical drafts. *Current behaviour (supersedes the earlier `outreach_event` design, finding H-06):* the key is recomputed from columns that actually exist — `row_idempotency_key(company_id, campaign_id, channel, sequence_step)` — and `DraftWriter.find_existing` reads the row back and compares body/subject/contact, reporting `DraftOutcome.content_matches=False` when the stored text differs. The outreach event is recorded in `personalization_rationale` for audit, not in the key. This is still a read-then-write race under concurrency, and `CREATE UNIQUE INDEX uq_outreach_messages_slot ON outreach_messages (company_id, campaign_id, channel, sequence_step)` (NULLS NOT DISTINCT on PG15+) is the migration that would close it.

**S3 — `needs_human_review`, `insufficient_evidence`, `evidence_coverage` are not persisted.** They live on `QualificationScorecard` in memory. `QualificationRepository.persist_draft` refuses to persist an inconclusive card, so a persisted row *should* be measured — but the gate cannot verify that from columns, so it derives coverage from `dimension_evidence` and treats a reader-supplied flag as authoritative when present. `QualificationRecord.needs_human_review = None` means "not exposed", never "false".

**S4 — `contact_id` is NOT NULL with a composite FK to `contacts(id, company_id)`.** A draft always needs a named contact; there is no company-level draft. The request therefore requires `contact_id`, and a company with no contact cannot be drafted to at all.

**S5 — the gate cannot verify that the contact belongs to the company.** No port reads `contacts`. The composite FK is the database's guarantee; `SuppressionReader.check(company_id, contact_id)` is contractually required to raise or return `ambiguous` when the pair does not exist. That obligation is documented on the Protocol and faked in tests, not provable here.

**S6 — an agent-scoped suppression read legitimately raises.** `is_identifier_suppressed` (SECURITY DEFINER, L930-1050) raises `Security Policy Violation: ... outside the authorized scope of active tasks` when the identifier is not inside the agent's active task scope, and also raises for unauthenticated callers. Fail-closed is correct but coarse: the gate reports `suppression_lookup_failed` and cannot distinguish "out of scope" from "service down". In practice the draft-time check should be run by a reader holding the drafter's task scope, or by an operator session.

**S7 — until H-01 is applied, a contact opt-out cannot reach the ledger at all.** `fn_sync_contact_suppression` is SECURITY INVOKER and `authenticated` holds `SELECT` only, so `contacts.is_opted_out = TRUE` raises `42501`. The gate reads the `is_opted_out` flag *and* the registry, so a flag-only opt-out is still caught — but any implementation that checks only `suppression_registry` would currently miss every contact-level opt-out.

**S8 — the insert policy requires a `drafter` agent type.** `is_agent_type('drafter')` (L1979-1980) means the eventual `DraftWriter` needs a provisioned drafter identity. This package does not create identities and cannot test that path.

**G3 (app/DB divergence, accepted) — `lifecycle_stage`.** H-03 will require `lifecycle_stage IN ('qualified','outreach_queued','in_outreach')`. `fn_sync_company_qualification` sets `qualified` when a non-disqualified scorecard is approved, so the common path agrees. A company already at `active_conversation`, `negotiating` or `partner_active` would pass this gate and be refused by the policy. That direction is safe (a loud PostgREST 403, no row, no send) and is left as-is rather than adding a `CompanyReader` port for a case that cannot occur before outreach has started.

---

## 9. Running the tests

```bash
. .venv/Scripts/activate          # python 3.12
python -m pytest tests/test_outreach_gate.py -q   # 54 passed
python -m pytest -q                              # 249 passed, 6 skipped
```

No test opens a socket, a database connection or reads `.env`. The schema contract tests read `crm/schema.sql` as text.
