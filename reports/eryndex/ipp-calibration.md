# IPP v2 Engine — Calibration Run Against Real Evidence

**Date:** 2026-11-13
**Input:** the 13 persisted Refokus findings (read from the pre-transformation backup, unmodified)
**Engine:** `intelligence/ipp` @ commit `9c032dd`+ (deterministic, stdlib only)
**Output artifact:** `reports/eryndex/evidence/ipp-calibration-refokus.json`
**Writes:** none. Historical Refokus records were read, never touched.

---

## Why run this

A scoring engine that has only been tested against synthetic fixtures is unproven. The 13 Refokus findings are the only real, evidence-backed research the business owns, which makes them the honest calibration set — and the mission forbids re-running or editing them, not reading them.

## Result

| Dimension | Points | Max | Confidence | Cited findings |
|---|---:|---:|---|---:|
| Customer Flow | 5 | 20 | high | 2 |
| Development Demand | 5 | 20 | high | 1 |
| Repeatability of Orders | 0 | 15 | unverified | 0 |
| Technical Fit | 4 | 15 | high | 2 |
| Business Size | 10 | 10 | high | 1 |
| Decision-Maker Accessibility | 3 | 10 | high | 1 |
| Commercial Fit | 5 | 10 | medium | 1 |
| **Total** | **32** | **100** | medium | 9 |

- Tier: `disqualified` — **by score only**. Zero disqualification triggers fired.
- `insufficient_evidence=False` (5 of 7 dimensions have high/medium evidence).
- `needs_human_review=True`, `review_status='draft'`.
- Missing evidence: 3 explicit `TBD` findings + the unearned anchors on Repeatability.

## What this actually says

The engine is behaving as designed, and the number must be read precisely:

1. **This is a coverage statement, not a verdict on Refokus.** Refokus is a Webflow Enterprise partner with 200+ delivered projects; the research report describes a strong prospect. The score is 32 because the 13 findings were collected to answer *what Refokus does*, not to satisfy the rubric's anchor ladders — no post-delivery support evidence (Repeatability), no verified company-level contact channel (Accessibility), no external-API delivery evidence (Technical Fit).
2. **`disqualified` is a database rule, not a business decision.** The trigger forces `disqualified` for any score < 40 (`crm/schema.sql` L1303). The rubric separately says unknowns do not disqualify (L436-440). Both are satisfied only if a human reads the card — which is why the engine exposes `disqualified_by_score_only` and `evidence_coverage` (0.714 here) instead of letting the tier label speak alone.
3. **The zero-anchor rule dominates.** Anchors are awarded only when the rubric's own wording is matched by evidence. A real company with thin public evidence scores low; that is the correct direction of error for a system whose failure mode is over-claiming.

## Consequences for the workflow

- A qualification task needs a **research brief aimed at the rubric**, not only at "understand the company". Phase 4 work must add rubric-anchor questions to the research payload so the second pass has a chance of earning the anchors.
- Human Gate 2 must see `evidence_coverage` and the trigger list next to the score; a reviewer approving 32/100 without them is approving a number, not a judgement.
- Do **not** lower the anchors or the 40-point floor to make Refokus look better. If the model is wrong for the business, that is an explicit rubric revision (ADR) with the operator's sign-off — never a quiet recalibration.

## Bugs this run found

None in the engine. It did expose a presentation gap, now fixed: `QualificationScorecard` had no way to express "low score, decent coverage, no disqualifier". Added `evidence_coverage`, `disqualified_by_score_only`, `verified_dimensions`, `verified_fact_count`, all surfaced in `as_dict()` and covered by `tests/test_ipp_coverage.py`.

## Reproduce

```
python -m pytest -q                      # 141 tests
python <scratch>/ipp_calibrate.py         # reads backups/, writes reports/eryndex/evidence/
```
The calibration script is a read-only utility; it is not part of the runtime path and writes nothing to the database.
