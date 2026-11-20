# ADR-0005 — Human gates are operator-only state transitions

**Status:** Accepted
**Date:** 2026-11-13
**Context refs:** `AGENTS.md` §10–11, workflow specifications, mission §7

## Context

The business requires four human gates (ingestion, qualification review, draft approval, dispatch authorization). Today they exist as prose and as RLS grants: agents can insert research but cannot insert companies. Prose does not survive a refactor, and "the agent simply doesn't call that endpoint" is not a control.

## Decision

Model each gate as an explicit transition in the workflow layer with a required actor role, and enforce it in both places:

1. **Workflow layer:** `orchestration/workflow.py` declares `Gate(name, from_stage, to_stage, required_role, evidence_required)`. A gate transition function asserts the caller's role and raises `GateViolation` otherwise.
2. **Database layer:** the identity performing the write must satisfy the existing RLS policies (`is_human_operator()`, `is_agent_type(...)`), which stay as the last line of defence.

Gate definitions:

| Gate | Transition | Required role | Evidence recorded |
|---|---|---|---|
| G1 Ingestion | candidate → company | `operator`/`admin`/`founder` | candidate id, decision, reviewer |
| G2 Qualification | draft scorecard → approved/rejected | operator | scorecard id, decision, notes |
| G3 Outreach approval | draft message → approved | operator | message id, decision |
| G4 Dispatch | approved → sent | operator, separate action | message id, channel, timestamp |

No agent may hold a role that satisfies any gate, and no gate is satisfiable by an agent's JWT regardless of intent — the check is on claims, not on code paths.

## Consequences

**Positive:** gates are testable (`tests/test_human_gates.py` asserts an agent JWT is rejected at every gate); the four gates appear in one table instead of scattered prose; dispatch cannot be reached by a single mis-wired call.

**Negative:** every gate transition needs operator credentials, so end-to-end tests must construct an operator client — which is exactly the behaviour we want to prove.
