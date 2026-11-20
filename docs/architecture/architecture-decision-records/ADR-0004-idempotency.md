# ADR-0004 — Application-enforced idempotency until a migration can add constraints

**Status:** Accepted (temporary)
**Date:** 2026-11-13
**Context refs:** baseline finding B-05

## Context

`research_reports` and `research_findings` have no uniqueness key beyond their primary keys. A retried runner — or a Hermes task re-claim — inserts a second report and a second copy of every finding. The mission requires idempotent ingestion, but DDL credentials are unavailable (BLK-01), so a `UNIQUE` constraint cannot be added now.

## Decision

Two layers, in this order:

1. **Content-addressed key (platform layer, available now).**
   `idempotency_key = sha256(canonical_json({company_id, report_version, sorted_finding_statements, evidence_urls}))`.
   Before any write, the runner queries for an existing report with the same `company_id` + `report_version` and compares its persisted findings' content hash. A match returns the existing result (`status="already_executed"`) instead of writing.
2. **Database constraint (deferred, authored now).**
   Migration `supabase/migrations/<ts>_research_idempotency.sql` adds `idempotency_key text` + `UNIQUE (company_id, idempotency_key)` to `research_reports` and the same to `research_findings` scoped by `report_id`. It is authored and reviewed now, applied when BLK-01 clears.

Canonicalization rules (so the key is stable across runtimes): sorted keys, UTF-8, no floats in the payload, statements compared after whitespace normalization, evidence URLs lowercased host + normalized path.

## Consequences

**Positive:** retries are safe today; the key is recorded in the report row's `idempotency_key` when present and always in the completion activity metadata, so duplicates are auditable either way.

**Negative:** the check is read-then-write, so two concurrent executors could still race. Accepted: the platform runs one executor per task today; the DB constraint removes the window permanently.

**Verification:** `tests/test_idempotency.py` — running the same payload twice yields one report and N findings; running a modified payload with the same version yields a new report; canonicalization is order-insensitive.
