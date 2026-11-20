# ADR-0001 — Layered architecture with a runtime-independent workflow core

**Status:** Accepted
**Date:** 2026-11-13
**Deciders:** Orchestrator (technical owner)
**Context refs:** `docs/architecture/eryndex-target-architecture.md`, mission §4

## Context

The repository implements one workflow as a script that constructs its own auth client, PostgREST client and agent inside `run_researcher_vertical_slice()`. Antigravity-specific execution is implicit: a human runs a module by hand. Hermes is the intended long-term orchestrator.

If workflow logic stays inside a runtime's invocation path, every runtime change rewrites business code, and no workflow can be tested without a runtime.

## Decision

Split the platform into the seven layers of the target architecture (workflow, orchestration, agent execution, integration adapters, intelligence, persistence/governance, human operations). Dependencies point inward and only inward:

- `agents/*` → may import `contracts`, `integrations`, `intelligence`
- `orchestration/*` → may import `contracts` and agent entry points
- `integrations/*`, `intelligence/*`, `contracts/*` → may not import `agents` or `orchestration`
- no module imports an Antigravity or Hermes SDK

A test (`tests/test_architecture_boundaries.py`) asserts the forbidden edges exist nowhere, so the rule survives refactoring rather than depending on review discipline.

## Consequences

**Positive:** workflows become callable from Antigravity, Hermes, a test, or a CLI with the same code path; adapters can be swapped; the intelligence layer is unit-testable without a database.

**Negative:** more files and one more indirection than a single script. Justified: the mission runs five workflows, not one.

**Follow-ups:** migrate `agents/researcher` onto the extracted infrastructure without changing its public behaviour (ADR-0002).
