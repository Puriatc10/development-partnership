# Outreach security closure matrix — independent verification

**Date:** 2026-11-14
**Verified by:** independent verifier (not the author of `outreach/`, not the author of the adversarial review)
**Scope verified:** `outreach/ports.py`, `outreach/gate.py`, `outreach/dispatch.py`, `outreach/__init__.py`, `tests/test_outreach_gate.py`, against `crm/schema.sql`, `contracts/keys.py`, `orchestration/workflow.py`.
**Database touched:** none. No connection, no credential, no migration applied. Every "DB-enforced" statement below is read from `crm/schema.sql`, never executed.
**Method:** read every line of the implementation and the review, then (a) attack the gate with independent probe scripts, (b) rebuild each claimed fix in a scratch copy and prove the named regression test fails without it, (c) fix the residuals that survived both. Scratch work lives under `C:/Users/puria/AppData/Local/Temp/`; the repository was never left in a reverted state.

## 1. Verdict

| Status | Findings |
|---|---|
| Closed (fix present, attack blocked, test load-bearing under revert) | **14** |
| Partially closed (residual now handled, but the class is schema-bound) | **1** (L-06) |
| Accepted risk (cannot be closed in application code) | **8** |
| Open (documentation drift, outside this scope) | **1** (L-05) |
| **Total findings in the review** | **24** |

New defects found by this pass: **9** (8 behavioural, 1 test-coverage). All 9 are fixed here with a regression test; each new test was proved to fail when its fix is reverted.

Two claims in `reports/eryndex/outreach-adversarial-review.md` were **too strong as shipped**, and are only true after this pass:

* **H-01** — "resists homoglyphs" was false for **uppercase** Cyrillic (`Оur team`, `Тen developers` both passed) and for format characters that are not in the hand-written invisible list (`revenue<U+180E>share` passed). The confusable table ran *before* `casefold()`, so an uppercase Cyrillic letter was only lowercased after the table had already been applied.
* **H-05** — the comparison list was incomplete: a writer could change `campaign_id`, `proposed_commercial_model` or `personalization_rationale` and still be reported `created`. The `channel` / `sequence_step` comparisons existed but had no regression test.

Nothing in this pass weakens a guard. Every change either adds a rejection, adds a verification of a value the gate previously took on trust, or turns a crash into an auditable refusal.

## 2. How "does the test actually fail if the fix is reverted" was established

For each finding, a pristine copy of `outreach/`, `contracts/`, `intelligence/`, `crm/schema.sql` and the test file was placed in a scratch directory. The fix was then patched out of the copy, the named tests were run, and the copy restored. A fix counts as verified only if its test failed in that state.

* Revert matrix 1 (the review's 15 code fixes): **all 15 reverts produce failing tests**. Full output: `C:/Users/puria/AppData/Local/Temp/outreach_revert/revert_matrix.json`.
* Revert matrix 2 (this pass's 9 fixes): **all 9 reverts produce failing tests** (`.../Temp/outreach_revert2/revert_matrix.json`, plus one one-off for the persisted-step guard).
* Baseline before any edit: `tests/test_outreach_gate.py` **105 passed**; full suite **300 passed, 6 skipped** — matches the review's numbers.
* After this pass: `tests/test_outreach_gate.py` **134 passed**; full suite **330 passed, 6 skipped**
  (336 collected — the 336th is `test_h01_preserves_suppression_source_semantics`, added by the
  migration workstream after this measurement).

## 3. Closure matrix

Legend — **Vulnerable path** is the pre-fix code site (reached by reverting the fix), **Fix site** the current one. "Revert→fail" gives the measured result of removing the fix.

| ID | Sev | Original vulnerability | Vulnerable path (pre-fix) | Fix site | Regression test(s) | Revert→fail | Verdict |
|---|---|---|---|---|---|---|---|
| C-01 | crit | Company B's request answered with company A's approved scorecard; `QualificationRecord.company_id` never compared. | `gate.py:429` `_qualification_codes` — no company comparison; any record accepted | `gate.py:435-442` new code `qualification_company_mismatch`, reused by the payload builder | `test_qualification_read_for_another_company_is_rejected`; `test_a_forged_decision_cannot_borrow_another_companys_scorecard` | 2 failed | **closed** |
| C-02 | crit | `build_draft_payload` trusted a caller-built `EligibilityDecision` (`codes=()` ⇒ "eligible"), so it could mint a payload with prohibited text or an unapproved/foreign scorecard. | `gate.py:827` — only `decision.eligible` checked | `gate.py:847-856` re-derives `_content_codes` and `_qualification_codes(record, request.company_id)` before building | `test_a_forged_decision_cannot_carry_prohibited_text_past_the_payload_builder`; `..._unapproved_scorecard_past_the_builder`; `..._borrow_another_companys_scorecard` | 3 failed | **closed** |
| H-01 | high | Lexical guards evadable: dash look-alikes, zero-width, full-width, lower-case Cyrillic/Greek, and whole unmodelled phrasings ("revenue split", "profit share", "our client", "we built", "start immediately", "ten developers"). | `gate.py:357` `_matches` ran on `text.lower()` | `gate.py:336-357` `normalize_for_matching` (NFKC → strip Cf → dashes → casefold → confusables) + 6 new/extended patterns `:246-283` | `test_revenue_sharing_obfuscation_is_still_rejected` (10); `test_delivery_and_client_claims_it_cannot_support_are_rejected` (7); **new** `test_uppercase_and_lowercase_cyrillic_homoglyphs_do_not_defeat_the_guards`; **new** `test_unknown_invisible_format_characters_do_not_defeat_the_guards` | 5 of 17 failed at `.lower()`; new tests: 3/4 and 4/4 fail without the ordering/Cf fixes | **closed** (was **partial** — see NEW-1, NEW-2) |
| H-02 | high | Subject accepted CR/LF (SMTP header injection) and control characters; body accepted C0 controls. | `gate.py:381` `_content_codes` — no structural text check | `gate.py:401-415` raw-text checks (`_LINE_BREAK_RE`, `_CONTROL_RE`) | `test_subject_cannot_carry_a_header_break_or_control_character` (4); `test_body_allows_newlines_but_not_other_control_characters`; **new** `test_c1_controls_and_unicode_line_separators_are_rejected` | 5 failed | **closed** (was **partial** — see NEW-5) |
| H-03 | high | Tier never compared to score: `tier_2` at score 12, `tier_1` at 45, `tier_4` at 90 all passed. | `gate.py:429` — tier only checked for membership/disqualification | `gate.py:500-509` derives the tier with `intelligence.ipp.rubric.tier_for_score` (IPP untouched) and rejects disagreement | `test_a_tier_the_score_does_not_earn_is_rejected`; `test_every_rejection_code_is_produced_by_at_least_one_scenario` | 1 failed | **closed** |
| H-04 | high | `needs_human_review=True` was ignored when a reviewer id was present. | `gate.py:470` — flag checked only when unreviewed | `gate.py:470-477` rejects unconditionally; `None` still means "not exposed" | `test_needs_human_review_is_not_overruled_by_a_reviewer_id` | 1 failed | **closed** |
| H-05 | high | `_verify_persisted` checked statuses/company/identity but not the text it had approved: a writer could rewrite body, subject, recipient, channel, step or evidence array and be reported `created`. | `gate.py:1014` — no content comparison | `gate.py:1038-1078` compares campaign, contact, channel, step, body, subject, evidence ids, rationale, commercial model and identity | `test_a_writer_cannot_rewrite_the_body_after_the_guard`; `..._substitute_the_recipient_after_the_guard`; `..._drop_the_evidence_array`; **new** `test_a_writer_cannot_change_the_channel_or_the_sequence_step`, `test_a_writer_cannot_change_the_campaign`, `test_a_writer_cannot_downgrade_the_commercial_model`, `test_a_writer_cannot_rewrite_the_provenance_rationale` | 3 failed (original block); each new revert 1 failed | **closed** (was **partial** — see NEW-6, NEW-7, NEW-9) |
| H-06 | high | Idempotency key included `outreach_event`, which is not a column, so no writer could recompute it from a persisted row; dedupe was a property of the fake. | `gate.py:729` `row_idempotency_key` | `gate.py:729-754` keys `(company_id, campaign_id, channel, sequence_step)`; `draft_idempotency_key:757` delegates | `test_the_idempotency_key_can_be_recomputed_from_the_persisted_row`; `test_idempotency_key_uses_the_shared_content_addressed_helper`; `test_a_different_sequence_step_is_a_new_draft`; `test_a_different_event_in_the_same_slot_is_refused_not_duplicated` | 2 failed | **closed** (app-level; DB uniqueness still absent — §6.1) |
| M-01 | med | `already_exists` path trusted whatever `find_existing` returned: no company, key or content check. | `gate.py:953` `_verify_existing` | `gate.py:953-1012` requires mapping + same company + `row_idempotency_key(row) == key`; **new** `_existing_row_differences` flags a mismatching contact/subject/body as a conflict, not a duplicate | `test_an_existing_row_from_another_company_is_an_integrity_failure`; `..._does_not_hash_to_the_key...`; `..._without_identity_columns...`; **new** `test_a_redraft_with_different_text_is_a_conflict_not_a_duplicate`, `test_an_identical_redraft_is_reported_as_a_duplicate` | 3 failed; new conflict test 1 failed | **closed** (was **partial** — see NEW-4) |
| M-02 | med | A reader returning a raw PostgREST dict crashed the gate with `AttributeError` instead of rejecting. | `gate.py:666` / `:558` — no type guards | `gate.py:666-675` and `:558-565` `isinstance` guards → `qualification_lookup_failed` / `suppression_lookup_failed` | `test_qualification_reader_returning_a_row_is_a_rejection_not_a_crash`; `test_suppression_reader_returning_a_row_is_a_rejection_not_a_crash` | 1 failed each | **closed** |
| M-03 | med | A `clear` suppression report that named nothing was indistinguishable from a service that answered nothing. | `gate.py:600` — no sources requirement | `gate.py:600-613` + `_names_a_source:615` requires at least one entry containing an alphanumeric character | `test_clear_without_naming_a_source_is_not_clean`; **new** `test_clear_with_a_blank_source_name_is_not_clean`, `test_a_suppression_source_with_a_name_still_passes` | 1 failed; new blank-source test 4/4 fail | **closed** (was **partial** — see NEW-3) |
| M-04 | med | Adapter exception text was copied verbatim into a logged `detail`, so an `apikey` in a PostgREST URL was logged. | `gate.py:362` `_describe_exception` — raw `str(exc)` | `gate.py:362-378` truncates, strips controls, redacts `apikey/token/password/secret/authorization` and `Bearer …`, contains a hostile `__str__` | `test_a_provider_error_is_reported_without_its_credential`; `test_an_exception_whose_str_raises_is_still_a_rejection` | 1 failed each (redaction and hostile-str reverts separately) | **closed** |
| M-05 | med | Fabricated non-UUID refs satisfied "has provenance" while producing an empty `personalization_evidence_ids`. | `gate.py:866` — refs filtered, emptiness allowed | `gate.py:866-876` requires ≥1 UUID-shaped ref; array is exactly the deduplicated UUIDs | `test_non_uuid_evidence_refs_cannot_produce_an_empty_evidence_array`; `test_evidence_array_carries_exactly_the_cited_finding_ids` | 1 failed | **closed** (syntactic provenance only — §5) |
| M-06 | med | Two writers that do not share state both insert for the same slot. | no code path — read-then-write in `create_draft:903` | none possible in application code; test pins the limitation | `test_two_writers_can_both_write_the_same_slot_until_a_unique_index_exists` | n/a (test asserts the failure) | **accepted-risk** — needs §6.1 index |
| M-07 | med | Gate cannot prove `contact_id` belongs to `company_id`; no port reads `contacts`. | no code path; `DraftRequest.contact_id` on trust | DB: `fk_outreach_contact_company (contact_id, company_id) → contacts(id, company_id)` `schema.sql:626-627` | none possible at unit level | n/a | **accepted-risk** (DB-enforced, not executed) |
| M-08 | med | `companies.lifecycle_stage` never read; a company at `negotiating` passes the gate. | no code path; record carries no stage | depends on unapplied `20261113_hardening_batch1.sql` H-03 policy + a `CompanyReader` that does not exist | none | n/a | **accepted-risk** (out of scope; migration not applied) |
| M-09 | med | `DraftPayload` claimed "only draft-legal values" but accepted `monthly_reserved_capacity` and an empty body when constructed directly. | `ports.py:333` `__post_init__` | `ports.py:372-380` refuses any model but `project_based_wholesale`, an empty body, or empty ids | `test_a_payload_cannot_be_constructed_for_the_later_commercial_model` | 1 failed | **closed** |
| L-01 | low | Rejection detail can contain a contact/domain identifier (PII in logs). | `gate.py:566-587` detail text | unchanged by design | none | n/a | **accepted-risk** — log retention is the control; removing it makes rejections undiagnosable |
| L-02 | low | `evidence_coverage` counts a dimension with only `finding_ids` as documented, while the DB trigger requires `evidence` text. | `ports.py:198-211` `_has_evidence` | unchanged by design | none | n/a | **accepted-risk** — tightening would duplicate the trigger's JSON shape |
| L-03 | low | HTML in a body is stored verbatim (no sanitisation). | `gate.py:381` pass-through | unchanged by design | none | n/a | **accepted-risk** — no renderer exists; escaping is a renderer responsibility |
| L-04 | low | `campaign_id` not validated against the company. | `create_draft:903` — no campaign read | DB: `agent_insert_outreach_messages` requires a task whose `campaign_id` matches or is NULL, `schema.sql:1996` | none | n/a | **accepted-risk** (DB-enforced, not executed) |
| L-05 | low | `reports/eryndex/outreach-workflow.md` documents the old key composition, evidence rule and rejection codes. | documentation | not editable in this pass (historical report, outside scope) | none | n/a | **open** — owner action |
| L-06 | low | `sequence_step` accepts non-integers (`1.5` truncated to 1 in key and row). | `gate.py:94-128` request validation | `gate.py:102-128` — non-str fields, booleans and non-numeric steps now raise `DraftRequestError`; **fractional floats still truncate** | **new** `test_non_structural_field_types_are_rejected_at_construction` | 4/6 fail without the guard | **partially closed** — float truncation accepted (DB `INT` column truncates the same way; both values are the same slot) |
| I-01 | info | `dispatch_module.dispatch` is a module attribute and can be rebound by anyone who already has a sender. | `dispatch.py:63` | no code change possible | `test_dispatch_cannot_be_turned_into_a_sender` | n/a | **accepted-risk** — the durable guarantee is the absent transport code and the absent import edge (both verified) |

## 4. New defects found by this pass (all closed)

| ID | Sev | Symptom (observed, not inferred) | Cause | Fix | Test | Revert→fail |
|---|---|---|---|---|---|---|
| NEW-1 | high | `"Оur team is ready"` (Cyrillic О) and `"Тen developers"` (Cyrillic Т) returned `eligible=True`; `"we can stаrt immediately"` (lower-case а) was blocked, so the control depended on the attacker's case. | `_CONFUSABLES` maps lower-case Cyrillic only, and ran **before** `casefold()`, which is what turns `О` into `о`. | `gate.py:336-357` — NFKC → Cf strip → dashes → **casefold** → confusables. | `test_uppercase_and_lowercase_cyrillic_homoglyphs_do_not_defeat_the_guards` (4 cases) | 3 failed, 1 passed |
| NEW-2 | med | `"revenue<U+180E>share"`, `"revenue<U+2061>share"`, `"rev<U+2063>enue share"`, `"pro<U+180E>fit share"` returned `eligible=True`. | The invisible set was a fixed list of 8 characters; category `Cf` has many more, and none of these are whitespace to `re`. | `gate.py:352` strips **every** `Cf` character, not a subset. | `test_unknown_invisible_format_characters_do_not_defeat_the_guards` (4 cases) | 4 failed |
| NEW-3 | med | `SuppressionReport(state="clear", sources_checked=("",))` and `("   ",)` were treated as **clear**. | The check tested the tuple's truthiness, not its entries: any non-empty tuple of blanks satisfied "named a source". | `gate.py:600-618` `_names_a_source` requires an entry with an alphanumeric character. | `test_clear_with_a_blank_source_name_is_not_clean` (4 cases); `test_a_suppression_source_with_a_name_still_passes` | 4 failed |
| NEW-4 | med | A re-draft for an occupied slot with a **completely different body** was returned as `already_exists` with the same detail as a genuine duplicate — the operator could not tell the composed message was never stored. | `_verify_existing` compared identity columns only; body/subject were not compared, because they are deliberately not part of the key. | `gate.py:1002-1012` reports the mismatching fields and sets `DraftOutcome.content_matches=False`; status stays `already_exists` because no write happened, but it is reported as a conflict that needs a human at G3. | `test_a_redraft_with_different_text_is_a_conflict_not_a_duplicate`; `test_an_identical_redraft_is_reported_as_a_duplicate` | 1 failed |
| NEW-5 | med | `subject="Note<U+0085>Bcc: victim@example.com"` (NEL) and `U+2028` passed the header-hygiene guard; body accepted NEL and C1 controls (`U+009B`). | `_CONTROL_RE` covered C0 only (`\x00-\x1f`, `\x7f`); `_LINE_BREAK_RE` covered `\r\n` only. NEL is a line break to several lenient parsers. | `gate.py:275-283` — `_CONTROL_RE` extends to `\x7f-\x9f`; `_LINE_BREAK_RE` adds `U+2028`/`U+2029`. Body still allows `\n`, `\r`, `\t`. | `test_c1_controls_and_unicode_line_separators_are_rejected` (4 cases) | 4 failed |
| NEW-6 | med | A writer could return the drafted row with a **different `campaign_id`**, a **downgraded `proposed_commercial_model`** (`monthly_reserved_capacity`, which the DB does *not* forbid) or a **rewritten `personalization_rationale`**, and the gate reported `created`. | `_verify_persisted` compared 9 columns but not these 3. | `gate.py:1038-1042` (campaign), `:1067-1078` (rationale, model). | `test_a_writer_cannot_change_the_campaign`; `test_a_writer_cannot_downgrade_the_commercial_model`; `test_a_writer_cannot_rewrite_the_provenance_rationale` | 1 failed each |
| NEW-7 | low | A writer returning `sequence_step="two"` raised a bare `ValueError` from `int()` instead of `DraftPersistenceError`. | Un-guarded `int()` in `_verify_persisted`. | `gate.py:1047-1053` wraps it in `DraftPersistenceError`. | `test_a_writer_cannot_change_the_channel_or_the_sequence_step` (third case) | 1 failed |
| NEW-8 | low | `subject=b"..."` raised `TypeError` from inside `re.search`; `body=5` raised `AttributeError`; `sequence_step="abc"` raised a plain `ValueError`. Crash, not an auditable rejection. | `DraftRequest.__post_init__` validated emptiness but not types. | `gate.py:102-128` type checks for every text field, `campaign_id`, `sequence_step`. | `test_non_structural_field_types_are_rejected_at_construction` (6 cases) | 4 failed, 2 passed |
| NEW-9 | low | The `channel` and `sequence_step` comparisons in H-05 existed but had **no regression test** — reverting them left the suite green. | Test coverage gap, not a code defect. | test added (same test as NEW-7). | `test_a_writer_cannot_change_the_channel_or_the_sequence_step` | 1 failed |

## 5. Attack log (independent probes, this checkout)

Run from the repo root against the **fixed** code; "before" column is the pre-fix behaviour reproduced by reverting in a scratch copy.

| Attack | Result now | Evidence |
|---|---|---|
| Company A's approved scorecard used for company B's request | rejected | `qualification_company_mismatch` |
| Same, via a forged `EligibilityDecision` | rejected | `DraftRejected: qualification_company_mismatch` / `revenue_sharing_prohibited` / `qualification_pending_review` |
| Forged decision with **no** codes and clean text | payload built | by design: the builder re-derives content + qualification; suppression cannot be re-derived without a reader and `create_draft` always evaluates first |
| Revenue sharing, 19 variants: U+2011, ZWSP, ZWJ, ZWNJ, word joiner, U+180E, U+2061, U+2063, full-width, lower/upper Cyrillic, mixed case, NFKC ligature, math-bold, "revenue split", "share of revenue", "profit share" | rejected (16/19 blocked; the 3 remaining are **phrasings without the word revenue**: "rev split", "share of the profits", "revenue/share model" — see §7) | probes + `test_revenue_sharing_obfuscation_is_still_rejected` |
| Capacity claims: "ten developers", "Тen developers", "Оur team", "start\u200b immediately", full-width "guaranteed" | rejected | probes + `test_delivery_and_client_claims_it_cannot_support_are_rejected` |
| Subject with `\r\n`, bare `\n`, bare `\r`, NUL, ESC, NEL, U+2028 | rejected | `draft_content_invalid` |
| Subject with a tab; body with `\r\n\r\n` | accepted | deliberately allowed (legal whitespace / plain-text body) |
| Body with NUL, ESC, BEL, NEL, U+009B | rejected | `draft_content_invalid` |
| Tier/score: 12+tier_2, 90+tier_4, 72+tier_1, non-existent tier, NaN, inf, -5 | rejected | `qualification_tier_inconsistent` / `qualification_unmeasured` / `qualification_disqualified` / `provenance_incomplete` |
| `needs_human_review=True` with a reviewer id and a timestamp | rejected | `qualification_pending_review` |
| Reader raises `TimeoutError`, `RuntimeError`, `PermissionError`, `ValueError`, or an exception whose `__str__` raises | rejected, never "clean" | `qualification_lookup_failed` / `suppression_lookup_failed`; `KeyboardInterrupt`/`SystemExit` propagate |
| `SuppressionReport(state="clear")` with no sources / `("",)` / `("   ",)` | rejected | `suppression_ambiguous` |
| `state="clear"` with findings | rejected | `suppression_ambiguous` |
| Suppression reader returns a dict / `None` | rejected | `suppression_lookup_failed` |
| Two writers, same logical request | **both `created`, 2 rows** | app-level race; no unique index exists (§6.1) — classified, not fixed |
| Re-draft where the existing row has a different body | `already_exists`, `content_matches=False`, detail names the body; no write | conflict, not an acceptable duplicate |
| Re-draft where the existing row has a different contact | `already_exists`, `content_matches=False`, detail names the contact; no write | same |
| Identical re-draft | `already_exists`, `content_matches=True`, same draft id | true duplicate |
| `find_existing` returns a foreign company row / a row that does not hash to the key / a row without identity columns | `DraftPersistenceError` | M-01 |
| Writer tampers body / subject / contact / channel / step / evidence / campaign / model / rationale / identity | `DraftPersistenceError` | H-05 + NEW-6 |
| `import outreach.gate`, then create a draft in a fresh interpreter | no `outreach.dispatch`, no `socket`, `ssl`, `http`, `urllib`, `urllib3`, `requests`, `httpx`, `aiohttp`, `smtplib`, `ftplib`, `telnetlib` | subprocess `sys.modules` probe; `transport_reached=[]` |
| Rebinding `outreach.dispatch.dispatch`, subclassing `DispatchChannel` without `__init__`, flipping `DISPATCH_AVAILABLE` | every entry point still raises `DispatchDisabled` | `test_dispatch_cannot_be_turned_into_a_sender` |

## 6. What the application layer cannot close (exact change needed)

None of this was applied. Every item names the exact column or index.

**6.1 One draft per slot (M-06).** Dedupe is still read-then-write in `create_draft`; two writers that do not share state both pass `find_existing`. There is **no unique constraint on `outreach_messages` anywhere** in `crm/schema.sql` (the only unique index near this area is `idx_contacts_unique_company_email`, L440). Required, matching `row_idempotency_key`:

```sql
CREATE UNIQUE INDEX uq_outreach_messages_slot
  ON outreach_messages (company_id, campaign_id, channel, sequence_step)
  NULLS NOT DISTINCT;                 -- PG15+
-- PG14 equivalent:
-- CREATE UNIQUE INDEX uq_outreach_messages_slot ON outreach_messages (
--   company_id, COALESCE(campaign_id, '00000000-0000-0000-0000-000000000000'::uuid),
--   channel, sequence_step);
```

Until that exists, `already_exists` is a single-writer property. A writer that implements `find_existing` by scanning its task scope and recomputing the key is required for even that — **no production writer exists in this repository** (§7).

**6.2 Provenance columns (S1).** `outreach_messages` has no `qualification_id`, `rubric_version` or `evidence_coverage`; `personalization_evidence_ids UUID[]` has **no FK to `research_findings`**, so any UUID-shaped string is accepted (M-05 is syntactic only).

```sql
ALTER TABLE outreach_messages
  ADD COLUMN qualification_id UUID REFERENCES lead_qualifications(id),
  ADD COLUMN rubric_version TEXT,
  ADD COLUMN evidence_coverage NUMERIC(4,3);
CREATE INDEX idx_outreach_messages_qualification ON outreach_messages (qualification_id);
```

**6.3 The scorecard's own verdicts (S3).** `needs_human_review`, `insufficient_evidence` and `evidence_coverage` exist only on the in-memory scorecard; a persisted-row reader passes `None`, which the gate must treat as "not exposed", never "false".

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

**6.4 Lifecycle stage (M-08).** The gate has no company port and cannot read `companies.lifecycle_stage`; the only control is H-03 in `20261113_hardening_batch1.sql`, which is **not applied**. Needs: the migration applied, plus optionally a `CompanyReader` so the refusal is a code rather than a 403.

**6.5 Contact ↔ company (M-07).** Already guaranteed by `fk_outreach_contact_company` (L626-627) with the composite unique `uq_contacts_id_company` (L430). No application check is possible without a third read that duplicates the FK. Not executed here.

**6.6 Zero rows vs. no rows (M-03 residual).** `sources_checked` makes a reader *state* what it consulted, but only the reader can distinguish an RLS-filtered empty set from a clean one (`agent_select_contacts` hides opted-out rows). The durable fix is a reader that verifies it can see the `contacts` row for the pair first, which needs the drafter's task-scoped session — an applied H-01 plus a provisioned `drafter` identity.

## 7. What a reviewer must still trust

1. **The readers.** The gate verifies shape (type, company, tier/score, reviewer, evidence refs; report type, named sources, no contradiction) but cannot prove a read happened or was task-scoped. A hand-built `QualificationRecord` with `review_status='approved'`, a UUID reviewer id and seven documented dimensions passes every check. Compensating controls are RLS on `lead_qualifications`, `chk_lead_qual_review_human_gate` and constructing the readers in a composition root — none of which this package can see.
2. **The writer's return value.** `_verify_persisted` inspects the mapping the writer returned, not the row in Postgres. A writer that returns a clean mapping and stores something else defeats it. The DB constraints and the insert policy are what make that a bug rather than a breach.
3. **That a writer exists at all.** There is **no implementation of `DraftWriter` / `QualificationReader` / `SuppressionReader` anywhere outside `tests/`** (`grep find_existing|DraftWriter` over `integrations/`, `orchestration/`, `agents/` returns nothing), and nothing outside `tests/` imports `outreach.gate`. H-06's "the writer recomputes the key from the row" is therefore a documented contract with no code to audit yet; so is the insertion path. This is the largest unverified surface in the whole finding set.
4. **The lexical guards' ceiling.** They are shallow by construction. Three revenue-sharing phrasings without the word "revenue" still pass: *"rev split"*, *"share of the profits"*, *"a revenue/share model"*. Catching them means adding patterns, not proving safety; a paraphrase nobody wrote a pattern for always passes. **G3 (human draft approval) remains the authority on what the message says.**
5. **`already_exists` semantics.** The slot key excludes body text by design. A reworded draft for an occupied slot is now *reported* as a conflict (`content_matches=False`), but nothing writes it: a human must move it to another step or reject the old draft at G3.
6. **Identity.** `drafted_by_agent_identity` comes from `draft_writer.agent_identity`, which the gate trusts to be JWT-derived. No `drafter` identity is provisioned, so no insert can succeed today (S8, unverifiable from this repository).
7. **The database facts below are read, not executed**: composite FK, insert/select policies, `chk_outreach_mandatory_human_gate`, `fn_sync_contact_suppression` (S7, 42501), and the absence of a unique slot index.

## 8. Test numbers (measured, not quoted)

```
python -m pytest tests/test_outreach_gate.py -o addopts="" -q
134 passed in 0.53s            # before this pass: 105 passed

python -m pytest -o addopts="" -q
329 passed, 6 skipped in 11.62s   # before this pass: 300 passed, 6 skipped
```

The 6 skips are `tests/db/test_security_fixes_db.py` (`ERYNDEX_DB_URL not set`). They are **not** passes: nothing in this report executed a database policy, constraint or trigger.

## 9. Files changed by this pass

| File | Change |
|---|---|
| `outreach/gate.py` | `normalize_for_matching` case-folds before the confusable table and strips all Unicode `Cf`; C1 controls and `U+2028`/`U+2029` rejected (subject) and C1 rejected (body); `_names_a_source` requires a real source name; `_existing_row_differences` + `DraftOutcome.content_matches` report a slot conflict instead of an acceptable duplicate; `_verify_persisted` compares campaign, commercial model, rationale and parses `sequence_step` defensively; `DraftRequest` type-checks its text/int fields |
| `tests/test_outreach_gate.py` | +9 test functions / 29 cases (105 → 134) covering NEW-1..NEW-9 |
| `reports/eryndex/security-closure-matrix.md` | this file |

`outreach/ports.py` and `outreach/dispatch.py` needed no change in this pass; every claim made about them held.
