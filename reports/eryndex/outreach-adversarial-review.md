# Outreach gate — adversarial security review

**Date:** 2026-11-14
**Reviewer role:** independent adversarial review (not the author of `outreach/`)
**Scope reviewed:** `outreach/ports.py`, `outreach/gate.py`, `outreach/dispatch.py`, `outreach/__init__.py`, `tests/test_outreach_gate.py`, `reports/eryndex/outreach-workflow.md`, against `crm/schema.sql`, `supabase/migrations/pending/20261113_hardening_batch1.sql`, `orchestration/workflow.py`, `contracts/keys.py`.
**Database touched:** none. No connection, no credential, no migration applied. Every "DB-enforced" statement below is read from `crm/schema.sql`, not executed.
**Method:** read the implementation line by line, then attack it with probe scripts and pytest, then fix only defects inside `outreach/*.py` and `tests/test_outreach_gate.py`.

> Note on the review brief: it cited `orchestration/human_gates.py` and `crm/schema.sql:1690-1710`. Neither exists as described — there is no `orchestration/human_gates.py` (the gates live in `orchestration/workflow.py`), and `personalization_evidence_ids` / `personalization_rationale` are at `crm/schema.sql:605-606`. The facts themselves are correct and are used below.

---

## 1. Verdict

| | Count |
|---|---|
| Findings | **24** |
| Critical | 2 |
| High | 6 |
| Medium | 9 |
| Low / info | 7 |
| Fixed and tested in this pass | 14 |
| Accepted risk (needs a schema change or is inherent) | 8 |
| Out of scope for this pass | 2 |

The gate's *shape* is right: a pure decision function over injected ports, every uncertainty resolving to a rejection, no client, no sender, no import edge to `outreach.dispatch`. It was not, however, safe as written. The two critical defects both let a draft exist that must never exist — one by answering company B's request with company A's approved scorecard, the other by handing the payload builder a hand-built `EligibilityDecision`. Neither was covered by the 54 original tests, because the test fakes always answered honestly and always for the right company.

**No security control was weakened to make a test pass.** Every code change either adds a rejection, adds a verification of something the gate previously took on trust, or narrows what a caller can construct. Two behaviours changed by design and are called out explicitly: the idempotency key composition (§4, H-06) and the requirement that a suppression reader name its sources (§4, M-03).

---

## 2. Findings

Verification legend: **fixed-and-tested** = code change + a regression test that fails on the old code; **accepted-risk** = real gap, cannot be closed without a schema change or outside this increment; **out-of-scope** = real gap, belongs to another workstream.

### Critical

| ID | Finding | Severity | Reproduction | Impact | Fix | Verification |
|---|---|---|---|---|---|---|
| C-01 | The gate never compared `QualificationRecord.company_id` with `DraftRequest.company_id`. Any reader that answers with the wrong company's approved scorecard produced an eligible decision. | critical | `evaluate_draft_eligibility(req(company_id=B), QR(rec(company_id=A)), SR())` → before: `eligible=True codes=[]`; after: `codes=['qualification_company_mismatch']` | A draft for company B justified by company A's approved scorecard: wrong lead scored, wrong lead contacted, audit trail pointing at another company. This is exactly the "company A's approval for company B" attack. | `_qualification_codes(record, company_id)` now rejects a mismatch with the new code `qualification_company_mismatch`; `build_draft_payload` reuses the same check. | fixed-and-tested — `test_qualification_read_for_another_company_is_rejected`, `test_a_forged_decision_cannot_borrow_another_companys_scorecard` |
| C-02 | `build_draft_payload` trusted a caller-constructed `EligibilityDecision`. That class is a plain frozen dataclass: `EligibilityDecision(qualification=record)` has no codes, so it *claims* to be eligible, and the builder only checked `decision.eligible`. | critical | `build_draft_payload(req(body="Happy to do a revenue share on this."), EligibilityDecision(qualification=rec()), agent_identity="x")` → before: payload built with rev-share text; also built from `review_status='draft'`. After: `DraftRejected: revenue_sharing_prohibited` / `qualification_pending_review`. | The only function that can mint a write payload could be driven with prohibited content, an unapproved scorecard, or another company's record, bypassing every guard in `evaluate_draft_eligibility`. `create_draft` always evaluates first, so this was not reachable through `create_draft` — but it is a public function with a public dataclass argument. | `build_draft_payload` re-derives `_content_codes(request)` and `_qualification_codes(record, request.company_id)` and raises `DraftRejected` before building. | fixed-and-tested — `test_a_forged_decision_cannot_carry_prohibited_text_past_the_payload_builder`, `..._unapproved_scorecard_past_the_builder`, `..._borrow_another_companys_scorecard` |

### High

| ID | Finding | Severity | Reproduction | Impact | Fix | Verification |
|---|---|---|---|---|---|---|
| H-01 | The lexical content guards were evadable. All matches ran on `text.lower()`, so invisible characters, dash look-alikes, full-width forms and Cyrillic/Greek homoglyphs broke keywords: `revenue‑share` (U+2011), `revenue\u200bsharing`, `r\u0435venue sharing`, `ｒｅｖｅｎｕｅ sharing` all passed. Separately, whole phrasings had no pattern: "revenue split", "share of revenue", "percentage of the revenue", "split the revenue", "profit share", "our client Acme", "We built X for Y", "We delivered…", "start immediately", "ten developers", "our engineering bench". | high | see the 17-case parametrisations; before the fix 10/10 rev-share variants and 7/7 delivery-claim variants returned `eligible=True` | The two business rules that must never be broken (no revenue sharing, no capacity/team/client claims) were advisory. A model that rephrased rather than recited walked straight through. | `normalize_for_matching()` NFKC-normalises, strips zero-width/BOM/soft-hyphen, unifies 11 dash look-alikes and folds 29 Cyrillic/Greek confusables *before* matching; patterns extended (spelled-out headcount, `our client`, `we built/delivered/shipped/launched`, `start immediately`, `revenue split`, `share of revenue`, `profit share`, `bench`). | fixed-and-tested — `test_revenue_sharing_obfuscation_is_still_rejected` (10 cases), `test_delivery_and_client_claims_it_cannot_support_are_rejected` (7 cases) |
| H-02 | Subject text was not checked for CR/LF or control characters. `subject="Quick note\r\nBcc: victim@example.com"` was accepted and stored. | high | `evaluate(req(subject="Quick note\r\nBcc: victim@example.com"))` → before `eligible=True`; after `draft_content_invalid` | `outreach_messages.subject` is a TEXT column that a future sender turns into an SMTP header. A CRLF is header injection: silent BCC, header rewrite, or a second message body. The gate is the last place that sees the text before persistence. | subject rejected on `\r`/`\n` or `\x00-\x08\x0b\x0c\x0e-\x1f\x7f`; body allows `\n`/`\r`/`\t`, rejects the rest. Raw-text check, deliberately before normalisation. | fixed-and-tested — `test_subject_cannot_carry_a_header_break_or_control_character` (4 cases), `test_body_allows_newlines_but_not_other_control_characters` |
| H-03 | Tier and score were never compared. `tier_2_prospective` with `overall_score=12.0` (or `tier_1_priority` with 45.0) passed every check. | high | `evaluate(rec(qualification_tier="tier_2_prospective", overall_score=12.0))` → before `eligible=True`; after `qualification_tier_inconsistent` | `fn_enforce_lead_qualification_rules` (schema L1301-1319) forces `disqualified` below 40, so a persisted row cannot look like this — but the gate's whole premise is that it does not trust the row it was handed. A fabricated or corrupted record claiming a passing tier was accepted as a qualified lead. | `_qualification_codes` derives the expected tier with `intelligence.ipp.rubric.tier_for_score` (no threshold is re-implemented, IPP untouched) and rejects any disagreement. | fixed-and-tested — `test_a_tier_the_score_does_not_earn_is_rejected` |
| H-04 | `needs_human_review=True` was only rejected when no reviewer id was present. With a reviewer id, a scorecard that says of itself "a human must still review this" passed. | high | `evaluate(rec(needs_human_review=True))` → before `eligible=True`; after `qualification_pending_review` | `needs_human_review` is the in-memory scorecard's own verdict and has no column; the gate cannot see whether a human ever saw that card. Honouring it only when it was also unreviewed meant the flag was silently downgraded by an unrelated field. | `if record.needs_human_review:` rejects unconditionally (code `qualification_pending_review`); `None` still means "not exposed" and still passes. | fixed-and-tested — `test_needs_human_review_is_not_overruled_by_a_reviewer_id` |
| H-05 | `_verify_persisted` checked statuses, company and identity, but not the text it had just approved. A writer that stored a different body (or subject, recipient, evidence array) was reported as `created`. | high | `FakeDraftWriter(tamper={"body": "…Revenue sharing available on request."})` → before `status=created` with the mutated body; after `DraftPersistenceError` | Every content guard runs on the payload. Between the guard and the row there is a writer, a client and a database. A truncating template, a retry that re-renders, or a compromised adapter could persist text that never passed the checks while the gate certified it. Recipient substitution was equally invisible. | `_verify_persisted` now compares `body`, `subject`, `contact_id`, `channel`, `sequence_step` and `personalization_evidence_ids` against the payload. | fixed-and-tested — `test_a_writer_cannot_rewrite_the_body_after_the_guard`, `..._substitute_the_recipient_after_the_guard`, `..._drop_the_evidence_array` |
| H-06 | The idempotency key included `outreach_event`, which is **not a column** in `outreach_messages`. `DraftPayload.to_row()` cannot write it either, so no real writer could ever recompute the key — dedupe worked only because the test fake stores a non-existent `idempotency_key` column and an `outreach_event` value. | high | `row_idempotency_key(payload.to_row()) != draft_idempotency_key(request)` on the old code; the old test only passed because `FakeDraftWriter` indexes on `row["idempotency_key"]`. | The "repeat request creates no duplicate" behaviour was a property of the test double, not of the system. The workflow report simultaneously claimed the key is "recomputed from the row's own columns" and that the unique index "cannot be created because `outreach_event` has no column" — the contradiction is the defect. | Key is now `(company_id, campaign_id, channel, sequence_step)` — every element a real column, defined once in `row_idempotency_key(row)`, with `draft_idempotency_key(request)` delegating to it. The event is recorded in `personalization_rationale` (`outreach_event=…; sequence_step=…`), the only text column available. **Decision:** two different events aimed at the same step of the same campaign are the same slot and the second is refused, not duplicated — fail-closed, and a human moves it to another step. `sequence_step` *is* in the key, so a follow-up (step 2) is a distinct draft. | fixed-and-tested — `test_the_idempotency_key_can_be_recomputed_from_the_persisted_row`, `test_a_different_sequence_step_is_a_new_draft`, `test_a_different_event_in_the_same_slot_is_refused_not_duplicated`, `test_a_different_campaign_is_a_new_draft` |

### Medium

| ID | Finding | Severity | Reproduction | Impact | Fix | Verification |
|---|---|---|---|---|---|---|
| M-01 | The `already_exists` path trusted whatever `find_existing` returned: no check of company, contact, or that the row's own columns hash to the requested key. | medium | writer returning `{"id": X, "company_id": B}` → before `status=already_exists draft_id=X`; after `DraftPersistenceError` | A writer (or a bug) with a key confusion makes the gate report success for a draft that belongs to someone else; the requested company silently never gets a message, and the operator sees a `draft_id` that is not theirs. | `_verify_existing` requires a mapping, the same `company_id`, the four identity columns, and `row_idempotency_key(row) == key`; a different contact is reported in `detail` rather than raised (the slot is deliberately recipient-independent). | fixed-and-tested — `test_an_existing_row_from_another_company_is_an_integrity_failure`, `..._does_not_hash_to_the_key…`, `..._without_identity_columns…` |
| M-02 | A reader returning anything other than the port's type (a raw PostgREST dict is the obvious case) crashed with `AttributeError` instead of producing an auditable rejection. | medium | `QR({"review_status": "approved"})` → before `AttributeError: 'dict' object has no attribute 'review_status_known'`; after `qualification_lookup_failed` | Fail-closed in the sense that no draft is written, but a caller that handles `EligibilityDecision` does not handle `AttributeError`, and an operator sees a stack trace instead of a rejection code. | `isinstance` guards on both port returns, mapped to `qualification_lookup_failed` / `suppression_lookup_failed`. | fixed-and-tested — `test_qualification_reader_returning_a_row_is_a_rejection_not_a_crash`, `test_suppression_reader_returning_a_row_is_a_rejection_not_a_crash` |
| M-03 | A `clear` suppression report with no content was indistinguishable from a service that answered nothing (RLS-filtered query, wrong table, short-circuit). | medium | `SuppressionReport(state="clear")` → before `eligible=True`; after `suppression_ambiguous` | "Zero rows" is the normal result of an RLS-filtered `SELECT`: `agent_select_contacts` hides opted-out rows and rows outside the active task, and `suppression_registry` has no agent SELECT at all. A reader that maps `[]` to "clean" reports every hidden row as clean, and the gate could not tell. | `SuppressionReport.sources_checked` added; the gate refuses a `clear` verdict that names no source, and records the sources in the eligible decision's `detail`. The port docstring now states that zero rows must raise, not clear. | fixed-and-tested — `test_clear_without_naming_a_source_is_not_clean` |
| M-04 | Adapter exception text was interpolated verbatim into `decision.detail`, which every caller logs. A PostgREST/httpx error carries the request URL, and the URL carries `apikey`. | medium | `RuntimeError("GET https://…?apikey=<key> …")` → before: key echoed in `detail`; after: `apikey=***`, type name kept | Credential material in application logs and in whatever persists a rejection. The gate cannot fix the adapter, but it can refuse to copy the secret forward. | `_describe_exception` truncates to 300 chars, strips control characters, and redacts `apikey|token|password|secret|authorization=…` and `Bearer <token>`; a hostile `__str__` is contained. | fixed-and-tested — `test_a_provider_error_is_reported_without_its_credential`, `test_an_exception_whose_str_raises_is_still_a_rejection` |
| M-05 | `personalization_evidence_ids` could end up empty: the gate filtered refs through a UUID regex and silently kept only non-UUID ones in prose. Fabricated refs (`"finding-abc"`) satisfied the "has provenance" check. | medium | `dimension_evidence[dim]["finding_ids"]=["finding-abc"]` → before: payload built with `personalization_evidence_ids=()`; after `provenance_incomplete` | `personalization_evidence_ids UUID[]` is the only machine-checkable provenance a row can carry (S1 mitigation). An empty array plus a rationale string is exactly the "provenance is text only" state the design claims to avoid, and any string at all could satisfy the ref check. | Require at least one UUID-shaped ref (real `research_findings.id` is a UUID); the array is exactly the deduplicated UUID refs. | fixed-and-tested — `test_non_uuid_evidence_refs_cannot_produce_an_empty_evidence_array`, `test_evidence_array_carries_exactly_the_cited_finding_ids` |
| M-06 | Dedupe is a read-then-write in application code: two writers that do not share state both see "no existing row" and both insert. | medium | two `FakeDraftWriter` instances, same request → `created` + `created`, 2 rows | Duplicate cold emails to the same company. A duplicate outreach is a reputational and compliance problem, not a data problem. | **Not fixable in application code.** Needs the unique index in §5.1. The limitation is now pinned by a test so it cannot be mistaken for a guarantee. | accepted-risk — `test_two_writers_can_both_write_the_same_slot_until_a_unique_index_exists` |
| M-07 | The gate cannot prove the contact belongs to the company: no port reads `contacts`, and `DraftRequest` takes `contact_id` on trust. | medium | `req(contact_id=<contact of company B>)` → gate cannot distinguish; no code path reads the pair | A draft filed against another company's contact. The gate cannot see it; the database can. | None possible here. `fk_outreach_contact_company (contact_id, company_id) REFERENCES contacts(id, company_id)` (schema L626-627) rejects the INSERT with 23503. The minimal fix (a `ContactReader` port) would add a third read that the FK already makes redundant — not added, deliberately. | accepted-risk — DB-enforced (not executed: no credentials). The adversarial *test* of "reuse another company's contact" therefore proves the FK, not the gate. |
| M-08 | `companies.lifecycle_stage` is never read, so a company at `negotiating` / `partner_active` / `active_conversation` passes this gate. H-03 will refuse the insert. | medium | no code path; `QualificationRecord` carries no lifecycle stage | Divergence between app and (future) policy: the gate says yes, the database says 403. Safe direction (no row, no send) but noisy and confusing at exactly the moment an operator is drafting. | Out of scope: needs a `CompanyReader`/`stage` field and the H-03 policy to be the authority. Documented (§5.4). | out-of-scope — accepted, documented |
| M-09 | `DraftPayload` claimed "only draft-legal values are reachable from here" but accepted `monthly_reserved_capacity` and an empty body when constructed directly. | medium | `DraftPayload(..., proposed_commercial_model="monthly_reserved_capacity")` → before: constructed; after `ValueError` | A direct constructor (or a future caller that skips `create_draft`) could produce a payload for the later-stage offer the business rules defer, or an empty message. Second-order, but the class made a promise it did not keep. | `DraftPayload.__post_init__` accepts only `project_based_wholesale`, a non-empty body, and non-empty company/contact ids. | fixed-and-tested — `test_a_payload_cannot_be_constructed_for_the_later_commercial_model` |

### Low / info

| ID | Finding | Severity | Reproduction | Impact | Fix | Verification |
|---|---|---|---|---|---|---|
| L-01 | A rejection detail can contain a contact identifier (`suppression_registry` findings list, `report.detail` from the reader). | low | `SuppressionReport(state="clear", findings=(SuppressionFinding("domain","studio.example",…),))` → `detail` names the identifier | Prospect PII in a log line. The operator already sees the record, so the exposure is limited to log retention. | Left as-is: removing it would make the rejection undiagnosable. Log-retention policy is the control. | accepted-risk |
| L-02 | `evidence_coverage` counts a dimension as covered when only `finding_ids` are present, while `fn_enforce_lead_qualification_rules` requires `dimension_evidence -> k ->> 'evidence'` text. | low | `dimension_evidence[k] = {"finding_ids": [...]}` (no `evidence`) → gate counts it covered | The gate is slightly more lenient than the trigger about what "documented" means. It cannot produce an unapproved draft on its own (approval is still required), but it can report coverage 1.0 for a card the trigger would refuse to persist. | Documented, not changed: tightening would mean duplicating the trigger's JSON shape in a second place. | accepted-risk |
| L-03 | HTML in a body is passed through unchanged (no sanitisation, no entity encoding). | low | `<script>alert(1)</script>` in body → stored verbatim | Only matters if a renderer emits it as HTML. There is no UI in this repository; the body is plain text in a TEXT column and a future renderer must escape. | Not the gate's job; stripping markup would corrupt legitimate drafts and is not in the business rules. | accepted-risk / out-of-scope |
| L-04 | `campaign_id` is not validated as belonging to the company. | low | `req(campaign_id=<other company's campaign>)` → gate accepts | A draft associated with a campaign it has no business being in. | DB: `agent_insert_outreach_messages` requires a task whose `campaign_id` matches or is NULL. No application check added — it would need a campaign port. | accepted-risk — DB-enforced |
| L-05 | `reports/eryndex/outreach-workflow.md` is now stale: key composition (§ "company/channel/event/step" at L29-31, L86, L189, L207), the evidence-array rule (L154), and the new codes (`qualification_company_mismatch`, `qualification_tier_inconsistent`). | low | read the doc | An auditor reading the report would describe a control that no longer exists. | Not edited — outside this review's scope (`outreach-workflow.md` was not in the editable set). **Must be updated by its author.** | out-of-scope |
| L-06 | `sequence_step` accepts non-integers (`1.5` passes `int(...) < 1` and is truncated to 1 in the key and the row). | low | `DraftRequest(..., sequence_step=1.5)` → key/row use 1 | A step collision between 1 and 1.5 that the caller did not intend; harmless because both are the same slot, but surprising. | Not changed: rejecting non-ints would break callers that pass numeric strings from JSON. | accepted-risk |
| I-01 | "Dispatch cannot be turned into a sender" is true of the code, not of Python: `outreach.dispatch.dispatch` is a module attribute and can be rebound by anyone who already has a sender. | info | `dispatch_module.dispatch = <real sender>` | None on its own: rebinding requires the transport the package does not contain, and the gate never imports the module. The durable guarantee is the *absence* of transport code and the import edge, both verified. | No code change. Subclassing `DispatchChannel` while skipping `__init__` still raises from every method (tested); flipping `DISPATCH_AVAILABLE` changes nothing (tested). | accepted-risk — verified `test_dispatch_cannot_be_turned_into_a_sender` |

---

## 3. What was attacked, and what happened

Every row below was executed against this checkout. "Before" is the code as delivered; "after" is after the fixes in §2.

Counts in the "Before" column are distinct phrasings checked against the pre-fix patterns (probe output for the ones marked; the rest are unambiguous by inspection of the old regexes).

| Attack | Before | After | Evidence |
|---|---|---|---|
| Use company A's approved scorecard for company B | **succeeded** — `eligible=True` | rejected | `qualification_company_mismatch` |
| Forge `EligibilityDecision` and build a payload with rev-share text | **succeeded** — payload built | rejected | `DraftRejected: revenue_sharing_prohibited` |
| Forge a decision around an unapproved (`draft`) scorecard | **succeeded** — payload built | rejected | `DraftRejected: qualification_pending_review` |
| Claim a tier the score does not earn (`tier_2`, score 12) | **succeeded** | rejected | `qualification_tier_inconsistent` |
| `needs_human_review=True` but a reviewer id present | **succeeded** | rejected | `qualification_pending_review` |
| Smuggle rev-share via U+2011 / zero-width / Cyrillic / em-dash / full-width | **succeeded** (5/5) | rejected (5/5) | `revenue_sharing_prohibited` |
| Smuggle rev-share by rephrasing ("revenue split", "share of revenue", "percentage of the revenue", "split the revenue", "profit share") | **succeeded** (5/5) | rejected (5/5) | `revenue_sharing_prohibited` |
| Claim "our client Acme", "we built…", "we delivered…", "start immediately", "ten developers", "our engineering bench" | **succeeded** (6/6) | rejected (6/6) | `unsupported_delivery_claim` |
| CRLF header injection in `subject` | **succeeded** | rejected | `draft_content_invalid` |
| NUL/ESC control characters in subject/body | **succeeded** | rejected | `draft_content_invalid` |
| Reader raises inside the suppression check | rejected (fail-closed) | rejected | `suppression_lookup_failed` |
| Reader raises inside the qualification check | rejected (fail-closed) | rejected | `qualification_lookup_failed` |
| Suppression reader returns `None` | rejected | rejected | `suppression_lookup_failed` |
| Suppression reader returns a `dict` | **crashed** (`AttributeError`) | rejected | `suppression_lookup_failed` |
| Qualification reader returns a `dict` | **crashed** (`AttributeError`) | rejected | `qualification_lookup_failed` |
| `state="clear"` with no sources (empty == unavailable) | **succeeded** | rejected | `suppression_ambiguous` |
| `state="clear"` with findings | rejected | rejected | `suppression_ambiguous` |
| `state="suppressed"` on contact / domain / company | rejected | rejected | three distinct codes |
| Writer rewrites the body after the guard | **succeeded** — reported `created` | `DraftPersistenceError` | body differs from the approved text |
| Writer substitutes the recipient | **succeeded** — reported `created` | `DraftPersistenceError` | contact differs |
| Writer drops the evidence array | **succeeded** — reported `created` | `DraftPersistenceError` | evidence ids differ |
| Writer returns an approved/sent row | rejected | rejected | `DraftPersistenceError` |
| `find_existing` returns another company's row | **succeeded** — `already_exists`, no write | `DraftPersistenceError` | foreign company |
| `find_existing` returns a row that does not hash to the key | **succeeded** | `DraftPersistenceError` | key mismatch |
| Two writers, same slot | **succeeded** — 2 rows | **still succeeds** | §5.1 (needs a unique index) |
| Reuse another company's contact row | gate cannot see it | gate cannot see it | DB composite FK (not executed) |
| Duplicated / reordered requests through one writer | no duplicate | no duplicate | 5-call sequence: created, already_exists, created, already_exists, already_exists |
| `KeyboardInterrupt` / `SystemExit` inside a reader | propagates | propagates (never swallowed into "clean") | `pytest.raises(SystemExit)` |
| Credential in an adapter exception | **succeeded** — key echoed in `detail` | redacted (`apikey=***`) | `_describe_exception` |
| Import `outreach.gate`, then `create_draft`: is `outreach.dispatch` loaded? | no | no | subprocess `sys.modules`, twice (`import` only, and after a real draft); also `socket`, `ssl`, `http.client`, `urllib3`, `requests`, `httpx`, `smtplib`, `email`, `ftplib` absent |
| Construct `DispatchChannel` (incl. a subclass that skips `__init__`) / call `dispatch` / flip `DISPATCH_AVAILABLE` | raises | raises | `DispatchDisabled` on every entry point |

---

## 4. The twelve required behaviours, verified against the implementation

| # | Behaviour | Verified how | Result |
|---|---|---|---|
| 1 | No qualification → no draft | `_qualification_codes` is only reached with a record; `record is None` returns `qualification_missing` | PASS |
| 2 | Pending review → no draft | `review_status in ('draft','pending_human_review')` → `qualification_pending_review` | PASS |
| 3 | Disqualified tier → no draft | tier `disqualified` **or** any `disqualification_reasons` → `qualification_disqualified` | PASS |
| 4 | "Approved" without a human reviewer → no draft | `has_human_reviewer` on `review_status='approved'` → `human_reviewer_missing`; also `needs_human_review` unconditionally | PASS |
| 5 | Suppressed contact → no draft | `contact_email`/`contact_linkedin` findings → `suppressed_contact` | PASS |
| 6 | Suppressed domain → no draft | `domain` finding → `suppressed_domain` (distinct from company) | PASS |
| 7 | Suppression lookup raising → fail closed | `except Exception` around the port, mapped to `suppression_lookup_failed`; `BaseException` propagates rather than passing | PASS |
| 8 | Ambiguous suppression → no draft | `state='ambiguous'`, contradictory `clear`+findings, and sourceless `clear` all rejected | PASS |
| 9 | Valid + clean → exactly one draft | `create_draft` → `created`, one row, `personalization_evidence_ids` non-empty and equal to the cited ids | PASS |
| 10 | Repeat request → no duplicate | `find_existing` hit → `already_exists`, no write; reordered/duplicated 5-call sequence holds | PASS (single writer) |
| 11 | Draft creation never dispatches | no import edge (AST scan + subprocess `sys.modules` before and after a draft); `outreach.dispatch` raises from every entry point | PASS |
| 12 | Missing provenance → rejected/held | no finding ids, no `qualification_id`, out-of-range score, no UUID-shaped ref, tier/score disagreement all rejected | PASS |

A scripted re-check of these twelve ran 13 assertions, all PASS (`verify_behaviours.py`, scratch, not committed). The authoritative evidence is the test suite below.

---

## 5. What the application layer cannot enforce without the pending migration

Each item names the exact change. None of these were applied; this review writes no DDL.

**5.1 One draft per slot (S2).** The gate's dedupe is a read-then-write; two writers race (M-06). The deployed table has no unique constraint on any candidate key, so there is no backstop.
```sql
-- idempotency slot, matching outreach.gate.row_idempotency_key
CREATE UNIQUE INDEX uq_outreach_messages_slot
  ON outreach_messages (
      company_id,
      campaign_id,
      channel,
      sequence_step
  ) NULLS NOT DISTINCT;          -- PG15+; on PG14 use the expression form below
-- PG14 equivalent:
-- CREATE UNIQUE INDEX uq_outreach_messages_slot ON outreach_messages (
--   company_id, COALESCE(campaign_id, '00000000-0000-0000-0000-000000000000'::uuid),
--   channel, sequence_step);
```
If the outreach *event* must be part of the identity (the alternative to H-06's decision), it needs a column first:
```sql
ALTER TABLE outreach_messages ADD COLUMN outreach_event TEXT NOT NULL DEFAULT 'unspecified';
-- then include outreach_event in the index above
```

**5.2 Provenance columns (S1).** `outreach_messages` has no `qualification_id`, no `rubric_version`, no `evidence_coverage`. `personalization_evidence_ids` gives row → finding links, but not row → scorecard, and nothing enforces that those finding ids belong to this company.
```sql
ALTER TABLE outreach_messages
  ADD COLUMN qualification_id UUID REFERENCES lead_qualifications(id),
  ADD COLUMN rubric_version TEXT,
  ADD COLUMN evidence_coverage NUMERIC(4,3);
CREATE INDEX idx_outreach_messages_qualification ON outreach_messages (qualification_id);
```
(The gate already builds this data in `DraftPayload.provenance`; it has nowhere to put it.)

**5.3 The scorecard's own verdicts (S3).** `needs_human_review`, `insufficient_evidence` and `evidence_coverage` live only on the in-memory `QualificationScorecard`. The gate now honours `needs_human_review` unconditionally, but it can only see the flag when the caller passes it; a persisted-row reader passes `None`, and `None` means "not exposed", never "false". To make the control durable:
```sql
ALTER TABLE lead_qualifications
  ADD COLUMN needs_human_review BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN insufficient_evidence BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN evidence_coverage NUMERIC(4,3) NOT NULL DEFAULT 0;
ALTER TABLE lead_qualifications
  ADD CONSTRAINT chk_lead_qual_measured_when_approved
  CHECK (review_status <> 'approved' OR (needs_human_review = FALSE
                                         AND insufficient_evidence = FALSE
                                         AND evidence_coverage > 0));
```

**5.4 Lifecycle stage precondition (G3).** Authored in `20261113_hardening_batch1.sql` H-03, **not applied**. The gate cannot read the column (no company port) and H-03 is the only thing that will refuse a draft for a company at `negotiating`/`partner_active`. Nothing to add here — it needs the migration applied, plus (optionally) a `CompanyReader` so the gate reports the refusal as a code instead of the policy producing a 403.

**5.5 Contact ↔ company (S5 / M-07).** Already enforced by `fk_outreach_contact_company`; no migration. The gate will never be able to check it without a third read that duplicates the FK.

**5.6 Opt-out reaching the ledger (S7).** Until H-01 is applied, `fn_sync_contact_suppression` is SECURITY INVOKER while `authenticated` holds only `SELECT` on `suppression_registry` (schema L2086), so `contacts.is_opted_out = TRUE` raises 42501 and no registry row is written. The gate reads the flag *and* the ledger, so a flag-only opt-out is still caught — but a `SuppressionReader` that checks only the ledger would miss every contact-level opt-out today. Fix is H-01 as written; no application change.

**5.7 Zero rows vs. no rows (M-03 residual).** `sources_checked` makes the reader state what it consulted, but only the reader can tell an RLS-filtered empty set from a clean one. The durable fix is a reader that verifies it can see the `contacts` row for the pair before answering, which needs the drafter's task-scoped session (S8) — i.e. an applied H-01 plus a provisioned `drafter` identity.

---

## 6. Trust boundaries

What this gate must trust, and why — stated so the next reader does not mistake a verified shape for a verified fact.

**The injected readers.** `QualificationReader` and `SuppressionReader` are the trust boundary of the whole module. The gate verifies the *shape* of their answers (`QualificationRecord` instance, company match, tier/score agreement, reviewer, evidence refs; `SuppressionReport` instance, named sources, consistent verdict) but it cannot prove a read happened, was scoped to the right task, or was not fabricated by the caller who built the reader. A hand-built `QualificationRecord` with `review_status='approved'` and a real-looking reviewer id passes every check — C-01 closes the "wrong company" variant, nothing can close the "invented row" variant from inside a pure function. The compensating controls are outside: RLS on `lead_qualifications` (agent SELECT only for `approved` rows within task scope), `chk_lead_qual_review_human_gate`, and the fact that the reader is constructed by the composition root, not by the model.

**Persisted row contents.** `DraftWriter.write` / `find_existing` return mappings the gate inspects (`approval_status`, `sending_status`, absence of approval/delivery metadata, company, contact, channel, step, body, subject, evidence ids, identity). That is a shape check on a value the writer returned, not a read-back from the database: the gate cannot prove the row in Postgres is the row it was shown, and it cannot prove `chk_outreach_mandatory_human_gate` fired. A writer that returns a clean mapping and stores something else defeats M-01 and H-05 verification. The database constraints and the RLS insert policy are what make that a bug rather than a breach.

**Draft text.** The subject and body are authored by the agent, not by this package. The content guards are lexical and deliberately shallow: they catch the phrasings in §2 H-01, they cannot catch a claim expressed in words nobody has written a pattern for. `normalize_for_matching` raises the cost of obfuscation but is not a semantic classifier. **G3 (human draft approval) remains the authority on what the message says**; the gate exists so that a human is never asked to approve a draft that already breaks a hard rule, not to replace the human.

**The writer identity.** `drafted_by_agent_identity` is copied from `draft_writer.agent_identity`, never from the request. The gate trusts the writer to hold a *verified* identity (JWT claims at construction, like `QualificationRepository`). The database re-verifies it: the insert policy requires `drafted_by_agent_identity = current_agent_identity()` and `is_agent_type('drafter')`. No such identity is provisioned today (S8), so no insert can succeed yet — which is why the write path is unit-tested only.

**The suppression reader's negative answer.** Treated as untrusted in the only way the gate can: a `clear` must name its sources and must not contradict itself, and any exception, `None`, or foreign type is a rejection. Whether "clear" is *true* is the reader's claim, and the reader is where `is_opted_out`, `is_suppressed`, `company_domains` and the registry must all be consulted.

---

## 7. Test numbers (measured, not quoted)

```
python -m pytest tests/test_outreach_gate.py -o addopts="" -q
105 passed

python -m pytest -o addopts="" -q
300 passed, 6 skipped
```

Before this review: 54 passed (gate file), 249 passed / 6 skipped (whole suite).
The 6 skips are **not** passes: `tests/db/test_security_fixes_db.py` (6 cases) skips with `ERYNDEX_DB_URL not set: database policy tests need a Development Postgres connection`. Nothing in this review executed a database policy.

The gate file went from 48 test functions / 54 cases to 80 test functions / 105 cases: +32 test functions, +51 cases. Each new test was written to fail on the pre-fix code (the pre-fix behaviour is recorded in §3 as the probe output).

---

## 8. Verification of the author's own claimed gaps

| Claim | Verdict | Evidence |
|---|---|---|
| S1 no provenance columns (`qualification_id`, `rubric_version`, `evidence_coverage`) | **TRUE** | `grep qualification_id crm/schema.sql` → no hits; `outreach_messages` has no provenance column beyond `personalization_evidence_ids`/`personalization_rationale` (L605-606) |
| S2 no idempotency column, no unique constraint, read-then-write race | **TRUE**, and worse than stated | no such column/constraint; and the key could not be recomputed at all (H-06) |
| S3 `needs_human_review` / `insufficient_evidence` / `evidence_coverage` not persisted; `None` ≠ false | **TRUE**, but the gate did not fully honour the flag | no columns exist; `None` is treated as "not exposed" (correct); `needs_human_review=True` was ignored when a reviewer id was present (H-04) |
| S4 `contact_id NOT NULL` + composite FK, so no contact-less draft | **TRUE** | schema L600, L626-627 |
| S5 no port reads contacts; gate cannot prove the pair | **TRUE** | no such read exists anywhere in `outreach/` |
| S6 `is_identifier_suppressed` raises for out-of-scope agents, so "out of scope" looks like "unavailable" | **TRUE by reading** | schema L1036-1038; the gate maps both to `suppression_lookup_failed` — coarse but fail-closed |
| S7 until H-01, `is_opted_out` cannot reach the registry (42501) | **TRUE by reading** | `fn_sync_contact_suppression` has no `SECURITY DEFINER` (L861-889); `GRANT SELECT ON suppression_registry` only (L2086) |
| S8 insert policy requires `is_agent_type('drafter')`; no such identity exists | policy requirement **TRUE**; identity absence **unverifiable** | schema L1980; provisioning state is not visible from this repository |
| G3 (H-03 also requires `lifecycle_stage IN (...)`, gate does not read it) | **TRUE** | gate has no lifecycle input; H-03 policy L167-171 of the pending migration |

---

## 9. Change log

| File | Change |
|---|---|
| `outreach/gate.py` | +2 rejection codes (`qualification_company_mismatch`, `qualification_tier_inconsistent`); `_qualification_codes` now cross-checks company and tier-vs-score and rejects `needs_human_review` unconditionally; `normalize_for_matching` + 7 new/extended content patterns; subject/body control-character checks; `_describe_exception` redaction; `isinstance` guards on both port returns; `sources_checked` requirement; `row_idempotency_key` + re-keyed `draft_idempotency_key`; `build_draft_payload` re-validates content and qualification and requires a UUID evidence id; `_verify_existing` for the `already_exists` path; `_verify_persisted` now compares body/subject/contact/channel/step/evidence; `format_provenance` records event and step |
| `outreach/ports.py` | `SuppressionReport.sources_checked`; `SuppressionReader` docstring states the zero-rows and sources obligations; `DraftPayload` refuses any model but `project_based_wholesale`, an empty body, and empty ids |
| `tests/test_outreach_gate.py` | 80 test functions / 105 cases (+32 functions, +51 cases); idempotency tests updated to the new key; `clean()` now names its sources; subprocess import-graph test widened to 12 transport modules plus a second `import`-only case |
| `outreach/dispatch.py`, `outreach/__init__.py` | unchanged — nothing needed; the stub was already correct |
| `reports/eryndex/outreach-adversarial-review.md` | this file |

**Follow-ups this review could not do (outside its editable set):**

1. `reports/eryndex/outreach-workflow.md` must be updated for H-06 (key composition), M-05 (evidence array), and the two new rejection codes — it currently documents the old behaviour (L-05).
2. The migration in §5.1 is the only thing that turns "no duplicate" from a single-writer property into a guarantee.
3. `tests/db/test_security_fixes_db.py` covers none of this: the suppression-reader path, the FK and the insert policy remain unexecuted until a Development database is reachable.
