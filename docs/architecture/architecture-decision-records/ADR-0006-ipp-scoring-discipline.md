# ADR-0006 — IPP scoring discipline: anchors only, no inferred evidence, no inconclusive persistence

**Status:** Accepted
**Date:** 2026-11-13
**Context refs:** `agents/qualification/ipp-scoring-rubric-v2.md`, `intelligence/ipp/`, `integrations/supabase/qualification_repository.py`

## Context

The IPP v2 rubric (886 lines) defines seven dimensions, anchor ladders, a zero-evidence rule, intermediate-score governance, and six disqualifiers. It does not say how a deterministic engine should behave at the edges, and the database adds constraints the rubric does not mention (tier bands, evidence presence, approval immutability).

Five questions had to be settled before the engine could be trusted:

1. Emit intermediate scores between anchors, or anchors only?
2. What happens to headcounts in the rubric's undefined 51–100 band?
3. Should a zero-evidence scorecard be persisted at all?
4. Is `DQ_*` an acceptable disqualification vocabulary?
5. Does accessibility evidence need a new finding category?

## Decision

1. **Anchors only.** The engine emits only rubric anchor values (e.g. 5/10/15/20 for Customer Flow). Intermediate scores require a written human justification (§2.8); an engine that writes 12.5 is fabricating the justification it is not allowed to write. Human reviewers adjust between anchors.
2. **Undefined bands score 0 with a gap note.** 51–100 headcount is not in the rubric's ladder. The zero-evidence rule applies: 0 points, explicit gap. No interpolation, no "closest band".
3. **Inconclusive cards are not persisted.** A scorecard with `insufficient_evidence=True` is returned for human inspection but `persist_draft` refuses it. Rationale: the DB trigger forces `disqualified` for any score < 40, while the rubric says unknowns do not disqualify. Writing the row would convert "we don't know" into "rejected" in the business record — a durable false statement. The human either supplies evidence or records a review decision.
4. **`DQ_*` codes are the vocabulary**, stored in `disqualification_reasons TEXT[]` with the human-readable detail in `disqualification_notes`. The schema defines the column as free text; no existing controlled list exists. Codes keep them queryable.
5. **No schema change for accessibility.** `team_structure` and `other` cover it today. Adding a `decision_maker` category would need a migration, and a migration needs DDL rights we do not hold (BLK-01). Recorded as a future improvement, not a blocker.

## Consequences

- The engine is deterministic and auditable: every point traces to finding IDs and a rubric line range.
- Some real companies will score conservatively (undefined headcount band, hypothesis-only evidence). That is the intended direction of error: under-claim, human review, adjust.
- Zero-evidence companies cannot pollute the qualification history; they appear as review tasks, not as rejected leads.
- The repository mirrors the trigger locally, so illegal rows fail with a readable error instead of a Postgres exception mid-transaction.

## Verification

`tests/test_ipp_engine.py` (45 tests) covers anchors, determinism, hypotheses, disqualifiers, tier boundaries and the empty-evidence path. `tests/test_qualification_persistence.py` (17 tests) covers the trigger mirror, idempotency and the inconclusive-refusal rule. Both are pure — no database, no network.
