# Workflow 9 — Minimum Persistence Assessment

**Date:** 2026-11-13
**Question:** does the Discovery → Qualification path need the partnership/project tables, and should Workflow 9 be built now?
**Answer:** no and no. This document records why, so the decision is not revisited from scratch.

---

## 1. What the current workflow actually needs

| Stage | Persistence | Exists? |
|---|---|---|
| Discover | `discovery_candidates` + evidence | migration authored (`pending/20261113_discovery_candidates.sql`) |
| Register (G1) | `companies`, `company_domains` | deployed |
| Research | `tasks`, `research_reports`, `research_findings`, `activities` | deployed |
| Qualify (G2) | `lead_qualifications` | deployed |
| Strategize / draft (G3) | `outreach_campaigns`, `outreach_messages` | deployed |
| Dispatch (G4) | `outreach_messages.sending_status` | deployed (never enabled) |
| Engage | `conversations`, `conversation_messages` | deployed |
| Manage partnership | `partnerships`, `projects`, `project_milestones`, `project_change_requests` | **designed only** |

The first six stages are fully covered by deployed tables plus one authored staging table. Nothing in Discovery or Qualification reads or writes the partnership layer.

## 2. Why deploying the delivery layer now would be a mistake

1. **It is contradictory.** `crm/delivery-governance-expansion.sql` (1,309 lines) and `crm/phase2_delivery_expansion.sql` (818 lines) differ by **650 diff lines**, both claim to be canonical, and the governance file revokes a function it never creates — it cannot even run. Deploying either one ships an unresolved design.
2. **It has an agent data leak.** `phase2_delivery_expansion.sql:801-811` grants any agent `SELECT` on all four tables with no task, company, or subagent predicate. That violates the platform's task-scoped access model.
3. **No workflow writes to it yet.** Partnership management becomes real when there is a partner and a project — neither exists. Deploying empty tables now creates drift between schema and use, and every later change becomes a migration on a live (if empty) structure.
4. **The delivery team is not onboarded.** Capacity, rates, and milestone policy are `TBD`; the schema would encode commitments the business cannot yet honour.

## 3. Recommendation

**Defer Workflow 9 until a real partner exists.** At that point, build it from one canonical file:

- One file wins (`delivery-governance-expansion.sql`), the other is deleted, and the difference is reviewed line by line.
- Remove the blanket agent `SELECT` policies; reuse the STAC pattern (task-scoped) or make the delivery layer operator-only.
- Fix `fn_enforce_team_delivery_authorization` → the existing function name.
- Add the four tables in one migration with RLS, grants, indexes, rollback, and a test plan — the same shape as `migration-review-batch1.md`.

## 4. What was done instead

- Authored the staging migration only (minimum necessary persistence).
- Left `partnerships`/`projects` untouched and undeployed.
- Recorded the schema conflict as finding H-07 in `reports/eryndex/security-findings.md`.

This keeps the operational surface honest: the system can discover, register, research, qualify, and draft — and cannot pretend to manage a partnership it does not have.
