# Outreach schema hardening — proposal for owner review

**Prepared:** 2026-11-14
**Target:** Development `vyfqmrdznmocnmzyjjcj` only. Production `vqnamszywsuqjxuqseas` is out of scope and has not been contacted.
**Status:** DESIGN ONLY. No SQL has been authored into `supabase/migrations/`, nothing has been applied, and no database connection was opened while writing this. Nothing in this document may be executed until the owner approves it.
**Batch:** a **separate, later batch** from `supabase/migrations/pending/20261113_hardening_batch1.sql`. Nothing here is to be folded into that file; see §8 for what is blocked on it.
**Covers:** the four accepted risks in the outreach path — M-06 / S2 (no database-level dedupe), S1 / M-05 (provenance is text plus an unvalidated UUID array), S4 / M-07 (no company-level draft, `contact_id NOT NULL`), M-08 (the gate cannot read `companies.lifecycle_stage`).

**One-page summary**

| § | Risk | Objects touched | Statements | Existing rows affected | Backfill | Needs DB credential |
|---|---|---|---|---|---|---|
| 2 | M-06 duplicate drafts | `outreach_messages` (new unique index) | 1 | none | no | yes, to apply |
| 3 | S1 / M-05 provenance | `lead_qualifications`, `research_findings`, `outreach_messages`, new `outreach_message_evidence` | 14 (3 unique constraints, column + composite FK + index, 1 table, revoke/grant/RLS, 4 policies) | none (existing `qualification_id` stays NULL) | no | yes, to apply |
| 4 | S4 / M-07 no company-level draft | `outreach_messages` (nullable column, 3 checks) + app | 4 (1 `ALTER` for the nullable column, 3 checks) | none (all existing rows have a contact) | no | yes, to apply |
| 5 | M-08 lifecycle gate | `outreach/ports.py`, `outreach/gate.py` — no DDL | 0 | none | no | no |

Everything below is additive except §4, which drops one `NOT NULL`; each section names its own rollback, and §2's rollback is itself a security regression.

---

## 1. Ground truth this proposal rests on

**Postgres version — confirmed.** `supabase projects list` (read-only control-plane call, run today, no database connection) reports for `vyfqmrdznmocnmzyjjcj`: `"database":{"version":"17.11.0.003","postgres_engine":"17","release_channel":"ga"}`. `supabase/config.toml` L41 agrees (`major_version = 17`). `NULLS NOT DISTINCT` therefore exists: it is a PostgreSQL 15 feature, present in every 17.x. **Caveat, stated plainly:** that is the control plane's claim about the engine major, not the output of `SHOW server_version`; I could not run that, because this machine holds no database password or DDL credential (BLK-01, `reports/eryndex/phase-0-baseline.md` §10). It is a *version* fact, not a *capability* fact — no extension, no `CREATE ... NULLS NOT DISTINCT` was executed to prove it.

**Row counts — read, not re-measured.** `reports/eryndex/evidence/phase-0-db-snapshot.json`, captured `2026-11-13T19:44:05Z` by the `operator` identity against `project_ref: vyfqmrdznmocnmzyjjcj`, records `outreach_messages` **0**, `lead_qualifications` **0**, `contacts` **0**, `outreach_campaigns` **0**, `companies` **1**, `research_findings` **13**. Every compatibility claim below ("existing rows are unaffected") rests on that snapshot plus `reports/eryndex/phase-0-baseline.md` L58–77. It is one day old and I cannot refresh it without a credential, so **each section carries a pre-flight query that must be re-run immediately before applying**; if a count is non-zero, the section's compatibility statement is void and the pre-flight decides what happens.

**What the code guarantees today** (so this proposal is not solving an imaginary problem):

- `outreach/gate.py` L729–754 defines the slot as `(company_id, campaign_id, channel, sequence_step)`; L903–950 `create_draft` is read-then-write (`find_existing` at L927, `write` at L942) with no lock and no constraint.
- `outreach/gate.py` L953–1012 verifies a row a writer *claims* exists but cannot prevent a second write.
- `outreach/ports.py` L380 refuses a `DraftPayload` without both `company_id` and `contact_id`; `contact_id` is a required field on `DraftRequest` (L85) and `DraftPayload`.
- `outreach/ports.py` L39 already allows `contact_form` as a channel, but `crm/schema.sql` L600 makes `contact_id NOT NULL` — so the one channel that has no natural recipient is the one channel the schema cannot express.
- `crm/schema.sql` L1977–1999 (`agent_insert_outreach_messages`) requires an active task and a `drafter` identity; it does not require a qualification or a lifecycle stage. The pending H-03 replacement (L139–176) adds both.
- `crm/schema.sql` L2083 grants table-level `SELECT` on `companies` to `authenticated`, and L1810–1821 `agent_select_companies` already permits a task-scoped agent to read a company row. The gate cannot read it because no port exists, not because no grant exists.

**Decisions the owner is being asked to make**

| # | Decision | Recommendation | Section |
|---|---|---|---|
| D1 | `NULLS NOT DISTINCT` vs a `COALESCE(campaign_id, zero-uuid)` expression index | `NULLS NOT DISTINCT` | §2.3–2.4 |
| D2 | Does a rejected draft keep its slot? | Yes — full index, rework by `UPDATE` | §2.2 |
| D3 | Evidence: join table, trigger-validated array, or syntactic-only | Join table | §3.3 |
| D4 | Free-text or closed vocabulary for "no recipient" reason | Closed vocabulary of three values | §4.3 |
| D5 | Lifecycle read path: app port now, DB function later | App port now, no DDL in this batch | §5.3–5.4 |

---

## 2. Risk 1 — database-level idempotency for `outreach_messages` (M-06)

### 2.1 Invariant protected

> **At most one row exists for one outreach slot `(company_id, campaign_id-or-∅, channel, sequence_step)`, so a retried or concurrent drafting run cannot put two messages for the same step in front of the same prospect.**

This is `security-closure-matrix.md` §6.1 and `outreach-workflow.md` S2. It is an *integrity* invariant, not a performance one: the failure it prevents is a prospect receiving two near-identical messages, and an operator being unable to tell which draft is real.

### 2.2 The key the code already uses — evaluated

The code key is row-derivable, which is the property H-06 established: every component of it is a column on the row (`crm/schema.sql` L598–620), so a writer can recompute it after the fact, and `_verify_existing` (L980) can prove a writer's lookup matches the key the gate computed. That part is sound. The weaknesses are in what the key *means*:

| Property of the key | Verdict | Consequence for a database constraint |
|---|---|---|
| All four components are real columns | good | an index can mirror it exactly — no shadow column, no hash column |
| `campaign_id` is nullable (`L598`) | needs care | a unique index treats NULLs as distinct by default, so `NULL` campaigns would not dedupe at all — the exact hole this section closes |
| `contact_id` is excluded | deliberate (H-06) | two contacts at one company cannot both receive step 1 of one campaign. Enforced by the index too. Product question, not a hardening one — see §7 |
| body / subject excluded | deliberate | a reworded draft for an occupied slot is a **conflict** (`content_matches=False`, L993–999), not a new slot |
| approval/sending status excluded | deliberate | a rejected row keeps the slot, so rework must be `UPDATE` (`rejected → draft` is an allowed transition in `fn_enforce_outreach_human_approval`, `crm/schema.sql` L1063ff), never a second `INSERT`. **This is decision D2.** The alternative — a partial index `WHERE approval_status IN ('draft','pending_human_approval','approved')` — frees the slot on rejection, but then `find_existing` must filter by status or it will find the dead row and report `already_exists`; two changes, not one, and a weaker invariant. Recommend the full index |
| `int(row["sequence_step"] or 0)` (L752) | works, with a wart | the DB column is `INT`; a caller passing `1.5` gets key-slot 1 and a stored step of 2 (L-06, accepted). Today that silently creates a duplicate; with the index the *second* insert raises 23505 — the index does not fix L-06, it makes it loud. Not a reason to delay |
| missing/blank components | safe | `company_id` is `NOT NULL`; `sequence_step >= 1` (`chk_outreach_sequence_step`), so the `or 0` fallback cannot collide with a real step |

### 2.3 Candidate forms for handling a NULL `campaign_id`

| | A. `NULLS NOT DISTINCT` (recommended) | B. `COALESCE(campaign_id, '000…0'::uuid)` |
|---|---|---|
| PG version needed | 15+ (dev is 17) | none |
| Column list matches the app key | exactly | no: the index key is a *derived* value |
| `ON CONFLICT (company_id, campaign_id, channel, sequence_step)` — what PostgREST's `on_conflict=` emits, and what a plain `INSERT … ON CONFLICT` needs for index inference | **works**: inference is by column list, and this is not an expression index | **fails with 42P10** ("no unique or exclusion constraint matching the ON CONFLICT specification"), because inference cannot see through the expression. Every upsert must spell the expression out, which a PostgREST client cannot do at all |
| Semantics on the sentinel value | none — NULL is simply equal to NULL for this index | a real campaign whose id happened to be `00000000-…` would collide with every campaign-less draft. The app key *distinguishes* them (`campaign_id is None` vs the string, L750), so the DB and the app would disagree on the one row the app considers distinct |
| Readability | `\d outreach_messages` shows the rule | the rule is an expression nobody will remember |
| Portability | PG15+; irrelevant here — the CLI reports engine 17 for Development (the only project this proposal targets) | works on 14 |

**Recommend A.** The decisive argument is not aesthetic: with B, the *only* atomic way to "insert or find" is unavailable through the client the project actually uses, so the concurrency fix would be unusable in the one place it matters.

### 2.4 Proposed DDL

```sql
-- Risk 1: one draft per (company_id, campaign_id-or-none, channel, sequence_step).
CREATE UNIQUE INDEX uq_outreach_messages_slot
    ON outreach_messages (company_id, campaign_id, channel, sequence_step)
    NULLS NOT DISTINCT;
```

One statement, no new column, no policy, no grant. The name is stable on purpose: the writer must match `constraint_name = 'uq_outreach_messages_slot'` in the 23505 it catches (§2.5), so renaming it later is an application change.

A unique **index** is used rather than a table constraint so that the object can be dropped and rebuilt by name, and later built `CONCURRENTLY` if the table is ever large — matching the pattern already in the schema (`idx_contacts_unique_company_email` L440, `uq_lead_qualifications_approved` in the pending file L186). `CONCURRENTLY` is deliberately *not* used here: it cannot run inside the migration's `BEGIN … COMMIT`, and the table has 0 rows, so the `SHARE` lock it takes is instantaneous.

### 2.5 What two concurrent writers see after it exists

Both writers compute key `K` and both call `find_existing(K)`; under READ COMMITTED neither can see the other's uncommitted row, so both get `None` and both proceed to `INSERT`. This is the M-06 path and it stays; only the *outcome* changes:

1. Writer A's insert acquires the index's speculative insertion token. Writer B's insert **blocks** on A's transaction.
2. **A commits → B fails immediately** with SQLSTATE **23505**, `duplicate key value violates unique constraint "uq_outreach_messages_slot"`. `psycopg` raises `UniqueViolation` with `diag.constraint_name` set; PostgREST returns **HTTP 409** with `{"code":"23505","details":"Key (company_id, campaign_id, channel, sequence_step)=(…) already exists."}`.
3. **A rolls back (or its transaction dies) → B's insert proceeds normally** and returns its row. No error.
4. There is no third outcome. Two committed rows for one slot is no longer representable.

Required writer contract (must be written down before the first writer exists, because there is none today — matrix §7.3):

- On `23505` naming `uq_outreach_messages_slot`: **re-read** with `find_existing(key)` and return that row. `create_draft` already turns that into `already_exists` and `_verify_existing` (L953) already decides `content_matches` — no gate change is needed.
- Never swallow the error and return a fabricated row: `_verify_persisted` (L1027) raises `DraftPersistenceError` on a response without an id.
- `ON CONFLICT (…) DO NOTHING` is acceptable **only** with the follow-up read: it returns zero rows, and if the competing row was deleted between the two statements, the correct response is one retry — not "success". PostgREST's `Prefer: resolution=ignore-duplicates` is this path and has the same obligation.

Two honesty notes. First, the index does not stop two writers from both *composing* a body; it only stops both from *storing* one. Second, 23505 is raised before any row is returned and is not subject to RLS masking, but it is a weak existence oracle: a caller who supplies a `company_id`/`campaign_id`/`channel`/`step` can learn that *some* draft occupies it. That caller must already hold a task on that company to attempt the insert at all (`agent_insert_outreach_messages`, L1977–1999), so the disclosure is inside a boundary the policy already draws.

### 2.6 Existing rows, backfill, migration and rollback

- **Existing rows:** 0 at the snapshot. No row is read, written or rewritten; a unique index adds no column and changes no value.
- **Backfill:** none. There is nothing to populate.
- **Pre-flight (must return 0 rows):**

```sql
SELECT company_id, campaign_id, channel, sequence_step, count(*) AS n,
       array_agg(id ORDER BY created_at) AS ids
FROM outreach_messages
GROUP BY company_id, campaign_id, channel, sequence_step
HAVING count(*) > 1;
```

`GROUP BY` treats NULLs as equal, so this catches duplicate NULL-campaign slots too. If it returns rows, the index build fails with 23505 and the *owner* must decide which row survives — deleting a draft is a business decision, not a migration step, and must not be automated here.

- **Migration:** one statement inside the batch transaction. Lock: `SHARE` on an empty table, effectively instant. No table rewrite.
- **Rollback:** `DROP INDEX uq_outreach_messages_slot;` — and note that this restores the M-06 race immediately, so rollback needs the same approval as applying.
- **Compatibility with the pending batch:** none of its objects are referenced. It does not touch `agent_insert_outreach_messages`, so H-03's `DROP POLICY`/`CREATE POLICY` cannot silently undo it.

### 2.7 The tests that would prove it

Database (`tests/db/test_outreach_schema_db.py`, same fixture style as `test_security_fixes_db.py`: refuses to run if `ERYNDEX_DB_URL` contains the Production ref):

- `test_second_draft_for_the_same_slot_with_a_null_campaign_is_rejected` — insert a draft with `campaign_id IS NULL`, insert a second differing only in `body` and `id`, assert `pytest.raises(psycopg.errors.UniqueViolation)` and `exc.diag.constraint_name == "uq_outreach_messages_slot"`. This is the NULL case that a default unique index would let through.
- `test_the_next_sequence_step_is_a_different_slot` — step 2 for the same company/campaign/channel succeeds; proves the index does not over-block a follow-up.
- `test_two_connections_cannot_both_own_a_slot` — connection A inserts without committing, connection B's insert blocks; after A commits, B raises `UniqueViolation`. Needs its own second connection (the existing module-scoped `conn` fixture is a single connection). This is the executable form of M-06 and the only test that proves the *race* is closed rather than the sequential duplicate.

Static (`tests/test_security_sql_static.py` style, no database):

- `test_slot_index_is_null_safe` — the pending file contains `NULLS NOT DISTINCT`, the column list is exactly `company_id, campaign_id, channel, sequence_step`, and no `COALESCE(` sentinel appears in it.

Application (`tests/test_outreach_gate.py`):

- `test_a_unique_violation_is_reported_as_already_exists_not_as_an_error` — a fake writer whose first `write` raises a simulated 23505 and whose `find_existing` then returns the competing row; assert `DraftOutcome.status == "already_exists"` and `content_matches` is computed, not assumed.

### 2.8 What this still does not guarantee

- It does not make `find_existing` correct. A writer that searches by the wrong scope still answers wrongly; the index only makes the *second write* impossible.
- It does not add a `contact_id` to the key, so one company cannot run two contacts through the same campaign step. That is today's behaviour, now enforced — if it is wrong, it is a product change (`§7`).
- It does not stop a duplicate that differs in `sequence_step` (by definition a different draft) or in `campaign_id` (a different campaign). It cannot: those are the identity.

---

## 3. Risk 2 — referential integrity for qualification provenance (S1 / M-05)

### 3.1 Invariant protected

> **Every draft is traceable to the scorecard that justified it, and every finding it cites exists, belongs to the same company, and cannot be deleted while a draft still cites it.**

Today the traceability is prose: `personalization_rationale TEXT NOT NULL` (L606) contains `qualification_id=…; score=…; tier=…` written by `format_provenance` (`gate.py` L797–819), and `personalization_evidence_ids UUID[]` (L605) is documented as UUID-shaped only. The matrix records M-05 as closed *syntactically*: `_content_codes`/`build_draft_payload` require at least one UUID-shaped ref, but the database accepts any UUID, from any company, including one that never existed.

### 3.2 Why an FK from the array is not possible — plainly

PostgreSQL has **no foreign key on array elements**. `REFERENCES` accepts columns, not the element type of a column; there is no `FOREIGN KEY (unnest(personalization_evidence_ids))`, and a `CHECK` cannot query another table (a CHECK may only reference the row being written). Any design that keeps `UUID[]` as the only representation therefore has, at best, a trigger that *re-implements* referential integrity by hand — including the part that is hardest to get right, which is the delete side: a trigger on `outreach_messages` cannot stop a later `DELETE FROM research_findings` from orphaning a citation, so it needs a second trigger on `research_findings` that scans array columns, which cannot be indexed for that lookup and is not concurrency-safe without locks.

### 3.3 The three realistic alternatives

| | A. Join table `outreach_message_evidence` | B. Keep the array, validate with a trigger | C. Keep it syntactic, document the limit |
|---|---|---|---|
| Existence of the finding | real FK | trigger check on insert/update only | no check |
| Same-company guarantee (the C-01 class) | real composite FK | trigger check, if the author remembers `company_id` | no |
| Protection against deleting a cited finding | real FK `ON DELETE RESTRICT` | needs a second hand-written trigger on `research_findings` | none |
| Reverse lookup ("which drafts cite this finding?") | one index scan | unindexable array scan | impossible |
| Cost | 1 table, 3 unique constraints, 4 policies, app writes child rows | 2 triggers + 1 function, no app change | zero |
| Reviewability | plain relational model, visible in `\d` | logic hidden in plpgsql, and it runs on untrusted input | a comment |
| Verdict | **recommended** | rejected | status quo — this section exists to end it |

**Recommend A.** Two reasons beyond the table. First, the class of bug it closes is not hypothetical: C-01 (critical) was "company B's request answered with company A's approved scorecard", and the same shape is available here one level down — a draft that cites a competitor's research finding. A composite FK is the only mechanism that makes that unrepresentable rather than checked. Second, there is **no production writer yet** (matrix §7.3), so the cost of changing the write contract is at its lowest it will ever be; every month this waits, the app-side cost rises.

The `ON DELETE` asymmetry is intentional and should be read as the design: `message_id → outreach_messages` is `ON DELETE CASCADE` (deleting a draft deletes its provenance — a deleted draft's citations mean nothing), `finding_id → research_findings` is `ON DELETE RESTRICT` (a finding that a live draft relies on cannot vanish; the operator must retire the draft first). The same asymmetry already exists for `contact_id` (`fk_outreach_contact_company`, L626–627, `RESTRICT`).

### 3.4 Proposed DDL

```sql
-- (a) FK targets: each referenced column pair must be unique. `id` is already
--     the primary key, so these add no new distinct values — only the pair.
ALTER TABLE lead_qualifications
    ADD CONSTRAINT uq_lead_qualifications_id_company UNIQUE (id, company_id);

ALTER TABLE research_findings
    ADD CONSTRAINT uq_research_findings_id_company UNIQUE (id, company_id);

ALTER TABLE outreach_messages
    ADD CONSTRAINT uq_outreach_messages_id_company UNIQUE (id, company_id);

-- (b) The scorecard the draft was built from.
ALTER TABLE outreach_messages
    ADD COLUMN qualification_id UUID;

ALTER TABLE outreach_messages
    ADD CONSTRAINT fk_outreach_qualification_company
        FOREIGN KEY (qualification_id, company_id)
        REFERENCES lead_qualifications (id, company_id)
        ON DELETE RESTRICT;

CREATE INDEX idx_outreach_messages_qualification
    ON outreach_messages (qualification_id);

-- (c) The findings the draft cites, one row per citation.
CREATE TABLE outreach_message_evidence (
    message_id  UUID NOT NULL,
    finding_id  UUID NOT NULL,
    company_id  UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT pk_outreach_message_evidence PRIMARY KEY (message_id, finding_id),

    -- The message and the citation must belong to the same company.
    CONSTRAINT fk_ome_message_company FOREIGN KEY (message_id, company_id)
        REFERENCES outreach_messages (id, company_id) ON DELETE CASCADE,

    -- The cited finding must exist and belong to that same company.
    CONSTRAINT fk_ome_finding_company FOREIGN KEY (finding_id, company_id)
        REFERENCES research_findings (id, company_id) ON DELETE RESTRICT
);
```

Notes that matter for review:

- `qualification_id` is **nullable**, and that is load-bearing, not laziness: with the default `MATCH SIMPLE`, a composite FK is not enforced when *any* of its columns is NULL, so `(NULL, company_id)` is legal while `(some_id, company_id)` is checked. That is exactly "provenance is optional, but never wrong" — the same mechanism §4 uses for `contact_id`. Making it `NOT NULL` is a later batch that needs H-04 applied and a writer that always supplies it (§7).
- No new data is required to populate `qualification_id`: the gate already holds the value (`QualificationRecord.qualification_id`, `ports.py` L118), writes it into the provenance mapping (`gate.py` L786) and into the rationale text (L811). The app change is one line in `DraftPayload.to_row()` — but it is a change, so this column is **not** populated by any code in the repository yet.
- The three `UNIQUE (id, company_id)` constraints look redundant (one column is already a PK) and are not free of review cost: they exist only because PostgreSQL requires a unique constraint on the *exact* referenced column list. Two extra btree indexes per table, on empty tables.
- `cited_order` is deliberately **not** a column. The array's order is preserved in `personalization_evidence_ids` (the gate compares it order-sensitively, L1063), and order is not an integrity property; adding it would create a second thing to keep in sync.

### 3.5 Policies and grants

The new table is a read path to research evidence, so it must not become a side door around `agent_select_research_findings`. It inherits the parent message's visibility:

```sql
REVOKE ALL ON outreach_message_evidence FROM anon, authenticated, public;
GRANT SELECT, INSERT, UPDATE, DELETE ON outreach_message_evidence TO authenticated;
ALTER TABLE outreach_message_evidence ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_deny_outreach_message_evidence ON outreach_message_evidence
    FOR ALL TO anon USING (false);

CREATE POLICY human_operator_all_outreach_message_evidence ON outreach_message_evidence
    FOR ALL TO authenticated
    USING (is_human_operator()) WITH CHECK (is_human_operator());

CREATE POLICY agent_select_outreach_message_evidence ON outreach_message_evidence
    FOR SELECT TO authenticated
    USING (
        is_agent()
        AND is_agent_type('drafter')
        AND EXISTS (SELECT 1 FROM outreach_messages om WHERE om.id = message_id)
    );

CREATE POLICY agent_insert_outreach_message_evidence ON outreach_message_evidence
    FOR INSERT TO authenticated
    WITH CHECK (
        is_agent()
        AND is_agent_type('drafter')
        AND EXISTS (
            SELECT 1 FROM outreach_messages om
            WHERE om.id = message_id
              AND om.drafted_by_agent_identity = current_agent_identity()
        )
    );
```

Two things a reviewer should check specifically:

1. **The `EXISTS` is itself RLS-filtered.** `agent_select_outreach_messages` (L2001–2014) scopes the subquery to a task on the message's company, so "can see the child row" is defined as "can see the parent row", and cannot drift from it. The consequence is a coupling that must be deliberate: widening the *parent select* policy widens child visibility automatically. If you would rather not have that coupling, the alternative is to repeat the full task-scope predicate here — at the cost of a second copy of the tenancy rule.
2. **`REVOKE` before `GRANT`, explicitly.** Supabase sets `ALTER DEFAULT PRIVILEGES` on schema `public`, so a table created in a *later* migration inherits grants that the original `REVOKE ALL ON ALL TABLES` (L2069) never saw. Without this `REVOKE`, `anon` could hold privileges on a provenance table. RLS would still refuse it (`anon_deny_…`), but the grant should not exist in the first place.

No grant or policy on `outreach_messages` or `lead_qualifications` changes, so H-03's policy replacement is unaffected.

### 3.6 Existing rows, backfill, migration and rollback

- **Existing rows:** `outreach_messages` 0, `lead_qualifications` 0, `research_findings` 13 (all of one company, from the Refokus research report). No row is read or rewritten. Existing `qualification_id` values would stay NULL — no backfill, and none is possible: the mapping exists only inside the rationale text of zero rows.
- **Backfill:** none. Deliberately: the honest answer for historical rows is "unknown", and NULL says that; a parser that scrapes `qualification_id=` out of prose would invent provenance.
- **Pre-flight:**

```sql
SELECT count(*) FROM outreach_messages;            -- expect 0
SELECT count(*) FROM lead_qualifications;          -- expect 0
SELECT company_id, count(*) FROM research_findings GROUP BY 1;  -- expect the 13-row company
```

If `outreach_messages` is non-zero, the `qualification_id` column still adds cleanly (NULLs are not checked), but the evidence table will start empty for those rows — which is a *silent* downgrade of their provenance. In that case the owner must decide whether to backfill from the array (a one-time `INSERT … SELECT unnest(personalization_evidence_ids)`), and that decision must be made before applying, not after.
- **Migration:** all statements are metadata or index-only on empty tables; the two `ADD CONSTRAINT … FOREIGN KEY` perform a validation scan over 0 rows.
- **Rollback**, in this order (the child table first, because it references the parents):

```sql
DROP TABLE outreach_message_evidence;
DROP INDEX idx_outreach_messages_qualification;
ALTER TABLE outreach_messages
    DROP CONSTRAINT fk_outreach_qualification_company,
    DROP COLUMN qualification_id,
    DROP CONSTRAINT uq_outreach_messages_id_company;
ALTER TABLE research_findings DROP CONSTRAINT uq_research_findings_id_company;
ALTER TABLE lead_qualifications DROP CONSTRAINT uq_lead_qualifications_id_company;
```

- **Compatibility with the pending batch:** H-04 adds `uq_lead_qualifications_approved` on `(company_id) WHERE review_status = 'approved'` — a different constraint on different columns; no conflict, either order.
- **Residual this section does not close:** the array and the table can disagree. A message written with three citations in `personalization_evidence_ids` and one row in the child table is internally inconsistent, and nothing in this batch prevents it (`_verify_persisted` compares the array to the payload, not to the table). This is accepted for now because RLS limits the insert path to a task-scoped `drafter` and there is exactly one writer to change; the enforcement trigger is in §7, and the alternative — dropping the array — is also there.

### 3.7 The tests that would prove it

Database (`tests/db/test_outreach_schema_db.py`):

- `test_outreach_message_cannot_cite_another_companys_qualification` — company A's approved qualification id on a draft whose `company_id` is B: expect `ForeignKeyViolation` (`23503`) on `fk_outreach_qualification_company`. This is C-01's shape, moved to the database.
- `test_outreach_message_evidence_rejects_a_finding_from_another_company` — `23503` on `fk_ome_finding_company`; proves the citation is company-scoped, not merely existence-checked.
- `test_deleting_a_cited_finding_is_restricted` — `DELETE FROM research_findings` for a cited id raises `23503`; the finding survives.
- `test_deleting_a_draft_removes_its_evidence_rows` — cascade removes the child rows, and the finding is then deletable.
- `test_agent_cannot_read_message_evidence_for_a_company_without_a_task` — as a drafter JWT with a task on company A, `SELECT` on the child rows of company B's draft returns 0 rows. Proves the inherited-`EXISTS` policy is not a directory disclosure.

Static:

- `test_evidence_table_has_no_dangling_reference` — the pending file declares both composite FKs, `ON DELETE RESTRICT` on the finding side and `CASCADE` on the message side, and contains `REVOKE ALL ON outreach_message_evidence` before any `GRANT`.

Application (`tests/test_outreach_gate.py` / `tests/test_qualification_persistence.py`):

- `test_the_payload_carries_the_qualification_it_was_built_from` — `DraftPayload.to_row()["qualification_id"] == payload.provenance["qualification_id"]`, and a payload whose qualification came from another company is refused before the writer is called.

### 3.8 What this still does not guarantee

- That the cited qualification is the **approved** one. The FK proves existence and same-company; H-03 proves *an* approved scorecard exists for the company. Tying the two together (the cited id must be that approved row) needs H-04 applied so exactly one approved row can exist; until then it is a documented limit, not a claim.
- That the evidence is *relevant*. A real finding from the right company that has nothing to do with the message still passes. Relevance is G3's judgement, as the matrix already says for the lexical guards.

---

## 4. Risk 3 — company-level drafts when there is no contact (S4 / M-07)

### 4.1 Invariant protected

> **A draft either has a named recipient contact that belongs to the draft's company, or it carries an explicit, enumerated reason why it has none. It can never be recipient-less by accident.**

Today the invariant is "a draft always has a contact" — which is enforced (`contact_id NOT NULL`, L600) and which makes the `contact_form` channel (a legal value of `outreach_channel_type`, L223, and of `OUTREACH_CHANNELS`, `ports.py` L39) impossible to draft for a company whose contact is unknown. That is not a hypothetical: `contacts` is empty today, `companies` has one row, and the only channel that can address a company without a person is the one the schema forbids.

### 4.2 What the composite FK already does — correcting the premise

`fk_outreach_contact_company (contact_id, company_id) → contacts(id, company_id)` (L626–627) is declared with **no `MATCH` clause**, so it is `MATCH SIMPLE`. PostgreSQL 17 docs, `CREATE TABLE`, `REFERENCES`: *"`MATCH SIMPLE` allows any of the foreign key columns to be null; if any of them are null, the row is not required to have a match in the referenced table."* Only `MATCH FULL` requires all-or-nothing.

So the composite FK **already permits `contact_id IS NULL` with `company_id NOT NULL`** once the `NOT NULL` on the column is dropped. No FK alteration is needed, no `MATCH` change, no partial/conditional FK (PostgreSQL has no such thing anyway). The "conditional FK" is the default behaviour, and it is the reason this change is much smaller than it looks:

- when `contact_id` is present, the composite FK still guarantees the contact belongs to `company_id` — **the M-07 guarantee is not weakened for contact-level drafts**;
- when it is absent, the constraint is simply not applicable, and the only thing standing between the operator and an accidental recipient-less draft is the CHECK below.

The real risk is therefore not the FK. It is that a bug which *fails to resolve* a contact now has a legal landing spot instead of raising `23502`. That is a fail-open change to the code path, and it is why the reason vocabulary below is closed rather than free text: `no_recipient_reason = 'n/a'` would restore exactly the hole this section is supposed to close.

### 4.3 Proposed DDL

```sql
ALTER TABLE outreach_messages
    ALTER COLUMN contact_id DROP NOT NULL,
    ADD COLUMN no_recipient_reason TEXT;

-- Required: a recipient, or an explicit reason, never neither and never both.
ALTER TABLE outreach_messages
    ADD CONSTRAINT chk_outreach_recipient_or_reason CHECK (
        (contact_id IS NOT NULL AND no_recipient_reason IS NULL)
        OR
        (contact_id IS NULL AND no_recipient_reason IS NOT NULL
            AND btrim(no_recipient_reason) <> '')
    );

-- Recommended: the reason must be one of a closed set, so it cannot be "n/a".
ALTER TABLE outreach_messages
    ADD CONSTRAINT chk_outreach_no_recipient_reason_vocabulary CHECK (
        no_recipient_reason IS NULL
        OR no_recipient_reason IN ('no_contact_on_record', 'company_contact_form', 'role_mailbox')
    );

-- Recommended: only a form submission can be recipient-less. `email` must not be in
-- this list: outreach_messages carries no address column, so an email draft with no
-- contact would be a message nobody can receive.
ALTER TABLE outreach_messages
    ADD CONSTRAINT chk_outreach_recipientless_channel CHECK (
        contact_id IS NOT NULL OR channel = 'contact_form'
    );
```

- `chk_outreach_recipient_or_reason` is the invariant. Making the two fields mutually exclusive (not just "one is present") means an operator cannot park a stale reason on a row that has a recipient, which would make the audit column meaningless.
- `chk_outreach_no_recipient_reason_vocabulary` is decision **D4**. Extending the set later is a one-statement constraint replacement; that is cheaper than living with a free-text field that satisfies the check with `'TODO'`. If the owner prefers free text, drop this constraint — the invariant still holds, only its auditability weakens.
- `chk_outreach_recipientless_channel` encodes a business fact that the enum already implies: `linkedin_inmail` is a message to a person. It is one line and it is reversible; if it is wrong, it is wrong in the safe direction (it refuses rather than permits).
- No `MATCH FULL`, no FK change, no policy change, no grant change. `ON DELETE RESTRICT` still protects contact-level rows.

### 4.4 Why the insert policy needs no change (and must not get one here)

`agent_insert_outreach_messages` (L1977–1999) — and H-03's replacement, L159 — contains `(t.contact_id IS NULL OR t.contact_id = outreach_messages.contact_id)`. With `outreach_messages.contact_id IS NULL` this evaluates as:

- task scoped to a contact: `FALSE OR (contact = NULL → NULL)` → **not true → refused**;
- task scoped to the company only: `TRUE OR NULL` → **true → allowed**.

That is exactly the intended rule — a company-level draft needs a company-level task — and it already exists. Which means **§4 is not blocked on H-03**: the policy semantics are correct today, and any edit here would be silently discarded when H-03 drops the policy. This is a case where the correct change is no change, and it should be recorded as such in the review.

### 4.5 The application change this DDL requires (and why the DDL is last in §6)

The DDL alone is inert: the gate cannot express a recipient-less draft. The same PR must change, at minimum:

- `DraftRequest.contact_id: str` → `str | None`, and `__post_init__` (L95–97) must require *either* a contact or a reason instead of a contact (a new `no_recipient_reason` field on the request).
- `DraftPayload.__post_init__` L380 (`"a draft payload needs both company_id and contact_id"`) and `to_row()` (which writes `contact_id`).
- `_existing_row_differences` L1005: `str(existing.get("contact_id") or "") != str(request.contact_id)` becomes `"None"` vs `""` for every company-level row, i.e. **every recipient-less draft would be reported as "a different contact"** — a bug that would look like a data problem.
- `_verify_persisted` L1043 already compares `row.get("contact_id") != payload.contact_id`, which is correct for `None`, but the payload must also carry and compare `no_recipient_reason`, or a writer could strip the reason on the way in.

This is four small edits plus tests, which is why §4 is ordered last: it is the only change that *loosens* a constraint, and it should land when the tightening changes (and their tests) are already in place.

### 4.6 Risk analysis: what a nullable FK costs

| Risk | Assessment | Mitigation |
|---|---|---|
| Cross-company guarantee weakens for contact-level drafts | **No.** With `contact_id` present, `MATCH SIMPLE` enforces the composite FK exactly as before | `test_contact_level_draft_still_rejects_a_foreign_contact` (§4.8) |
| A resolver bug silently produces a recipient-less draft | **Yes, real.** This is the fail-open direction | the CHECK; the closed vocabulary; the request-level validation; a DB test that insert without either fails |
| A draft with neither contact nor reason | impossible | `chk_outreach_recipient_or_reason` |
| A draft with a contact *and* a reason (stale audit field) | impossible | same CHECK |
| Two recipients at one company, same campaign step | not permitted — and not changed by this section: the slot key (§2) excludes `contact_id` | already the existing behaviour; see §7 |
| Deleting a contact that a draft references | still blocked | `ON DELETE RESTRICT` unchanged |
| Dispatch has no address to send to | out of scope, and deliberately: nothing dispatches today, and the form URL is `companies.website_url`, not a message property | §7 |

### 4.7 Existing rows, backfill, migration and rollback

- **Existing rows:** `outreach_messages` 0. For any existing row, `contact_id` is non-NULL, so `DROP NOT NULL` is a catalog-only change and every row continues to satisfy both new CHECKs (the vocabulary CHECK is satisfied by `no_recipient_reason IS NULL`; the channel CHECK by `contact_id IS NOT NULL`).
- **Backfill:** none. `no_recipient_reason` is NULL on every existing row by construction, and that is the correct value for them.
- **Pre-flight:** `SELECT count(*) FROM outreach_messages WHERE contact_id IS NULL;` (expect 0 — the column is `NOT NULL`, so this is a tautology today, and it stays in the file as the check that fails loudly if someone applies out of order).
- **Migration:** `DROP NOT NULL` is metadata-only (no rewrite); `ADD COLUMN` with no default is metadata-only; both CHECKs validate 0 rows.
- **Compatibility with the pending batch:** none of its statements touch `contact_id`, and H-03's policy already handles `NULL` correctly (§4.4).
- **Rollback:** the `SET NOT NULL` will fail if any recipient-less row exists, which is correct — it is the database refusing to lose the distinction:

```sql
ALTER TABLE outreach_messages
    DROP CONSTRAINT chk_outreach_recipientless_channel,
    DROP CONSTRAINT chk_outreach_no_recipient_reason_vocabulary,
    DROP CONSTRAINT chk_outreach_recipient_or_reason,
    DROP COLUMN no_recipient_reason;
ALTER TABLE outreach_messages ALTER COLUMN contact_id SET NOT NULL;   -- fails while NULL rows exist
```

A real rollback therefore requires a business decision about every recipient-less draft (attach a contact, or delete), exactly as the §2 rollback requires a decision about duplicates. Say so in the approval, not in an incident.

### 4.8 The tests that would prove it

Database (`tests/db/test_outreach_schema_db.py`):

- `test_company_level_draft_without_a_reason_is_rejected` — `INSERT` with `contact_id = NULL, no_recipient_reason = NULL` raises `CheckViolation` (`23514`) naming `chk_outreach_recipient_or_reason`.
- `test_company_level_draft_with_a_reason_is_accepted` — insert succeeds, row has `contact_id IS NULL` and the reason; read back.
- `test_a_reason_is_rejected_on_a_contact_addressed_draft` — both fields set raises `23514`; the audit column cannot go stale.
- `test_contact_level_draft_still_rejects_a_foreign_contact` — `contact_id` from company A with `company_id` B raises `23503` on `fk_outreach_contact_company`. **This is the regression test for the whole section**: it proves the loosening did not weaken M-07.
- `test_a_contact_scoped_task_cannot_create_a_company_level_draft` — as a drafter with a task scoped to contact C, insert a draft for the same company with `contact_id NULL`: expect `InsufficientPrivilege` (`42501`) from the insert policy. Proves §4.4's predicate reading, rather than assuming it.

Application (`tests/test_outreach_gate.py`):

- `test_a_company_level_request_requires_an_explicit_reason` — `DraftRequest(contact_id=None)` with no reason raises `DraftRequestError`; with a reason it builds a payload whose `contact_id` is `None`.
- `test_a_company_level_redraft_is_not_reported_as_a_different_contact` — the L1005 bug above, pinned: an existing row with `contact_id = None` and the same body is `content_matches=True`.

---

## 5. Risk 4 — `companies.lifecycle_stage` access for the eligibility gate (M-08)

### 5.1 Invariant protected

> **No draft is composed for a company that is not at an outreach-legal lifecycle stage, and the refusal is decided the same way at composition time and at write time.**

Today the gate has no company port and no stage in `QualificationRecord`, so a company at `negotiating`, `active_conversation` or `partner_active` passes the gate. The only control is H-03's `EXISTS (… c.lifecycle_stage IN ('qualified','outreach_queued','in_outreach'))` in the pending file (L171–175), which is not applied. The matrix records this as accepted risk M-08 for exactly that reason.

### 5.2 What is actually missing: a port, not a grant

This is worth stating before the options, because it changes the recommendation. `agent_select_companies` (L1810–1821) already permits an agent with a pending/in-progress task on a company to `SELECT` that company row, and L2083 already grants table-level `SELECT` to `authenticated`. The gate cannot read `lifecycle_stage` because **no `CompanyReader` exists in `outreach/ports.py`** — a missing interface, not a missing permission. So the default option is app-only, and any DB object has to justify itself.

### 5.3 The options

| | A. `CompanyReader` port over the existing table (recommended) | B. `SECURITY DEFINER` function `fn_company_outreach_eligible(uuid)` |
|---|---|---|
| New DB object | none | 1 function + grant |
| Enforces task scoping | yes — by RLS, for free | **no** — `SECURITY DEFINER` bypasses RLS, so the function must re-derive the task predicate or it becomes an oracle: any authenticated caller could ask "is company X outreach-eligible?" by uuid |
| Column exposure | the port returns three fields; the grant is table-level but the *interface* is narrow | returns a boolean; no column exposure, but also no stage in the error path, so the operator cannot tell `negotiating` from `disqualified` |
| Can the policy use it | n/a | only if H-03 is rewritten to call it — otherwise the stage list exists twice, which is the disagreement this section is trying to remove |
| Reviewable | a Protocol and a dataclass | plpgsql, `search_path`, grant surface, and a scope predicate that must mirror a policy |
| Blocked | no | yes — on H-03 being applied (§8) |

**Recommend A now, B later.** A is the smaller change and the safer one: RLS already answers "may this identity see this company", so the reader cannot be used to enumerate companies. B becomes the right answer only when it *replaces* the policy's inline list, and that cannot happen until H-03 is applied and stable — adding B today would create a second copy of the stage list rather than removing one.

### 5.4 The exact read path (no DDL in this batch)

```python
# outreach/ports.py
@dataclass(frozen=True)
class CompanyEligibility:
    company_id: str
    lifecycle_stage: str
    is_suppressed: bool

class CompanyReader(Protocol):
    def read(self, company_id: str) -> CompanyEligibility | None:
        """The company's outreach-relevant state, or None when not visible.

        None means "this identity cannot see the row" (no task scope, or the
        company is suppressed and agent_select_companies hides it), never
        "unknown, proceed". A reader that raises is a rejection, not a crash.
        """
```

- Adapter: a PostgREST `GET /companies?select=id,lifecycle_stage,is_suppressed&id=eq.…` with the drafter's JWT. If it returns a row, build the dataclass; if it returns `[]`, return `None`; if it raises or returns a shape that is not a list of one mapping, raise — and the gate converts that to a rejection code (see below), not an exception that escapes.
- `evaluate_draft_eligibility` (L625) gains a **required keyword-only** `company_reader`. Required, not optional: an optional reader is a parameter a future composition root forgets, and the failure mode is silent eligibility. The cost is mechanical — every call site in `tests/test_outreach_gate.py` gains a `FakeCompanyReader` (the suite is 134 tests in one file), which is the honest price of fail-closed.
- Placement: after the suppression check, so existing suppression rejection precedence and its tests are unchanged. A suppressed company is caught by `SuppressionReader` first; the reader's `None` is the belt-and-braces path.
- Two new codes in the closed `RejectionCode` set (L137–166): `company_not_eligible` (row read, stage not in `{qualified, outreach_queued, in_outreach}`; detail names the stage) and `company_lookup_failed` (reader raised, returned a non-mapping, or returned `None`; detail says "not visible to this identity, or absent" and must **not** claim which). The closed-set test `test_every_rejection_code_is_produced_by_at_least_one_scenario` (L790) will force a scenario for both — that is the point of having it.
- The stage set is a module constant next to `OUTREACH_CHANNELS`, and a static test parses H-03's literal list out of the pending SQL and asserts equality. That is the cheapest available defence against the two layers drifting while the policy keeps its own copy; it does not survive a *future* migration, which is what B is for.
- `outreach_queued` must stay in the allowed set even though nothing currently sets that stage: the only automatic transition is `identified|researching → qualified` (`fn_sync_company_qualification`, L1414–1417), so requiring `outreach_queued` before drafting would deadlock the first draft.

**Optional DDL, for the later batch only — do not include it now:**

```sql
CREATE OR REPLACE FUNCTION fn_company_outreach_eligible(p_company_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1 FROM companies c
        WHERE c.id = p_company_id
          AND c.is_suppressed = FALSE
          AND c.lifecycle_stage IN ('qualified', 'outreach_queued', 'in_outreach')
    );
$$;

REVOKE ALL ON FUNCTION fn_company_outreach_eligible(UUID) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION fn_company_outreach_eligible(UUID) TO authenticated;
```

with H-03's inline `EXISTS` replaced by `AND fn_company_outreach_eligible(outreach_messages.company_id)`. Until that replacement happens this function is a third opinion, not a source of truth — which is why it is not in this batch.

### 5.5 What a normal-path disagreement looks like, and which layer wins

Four shapes, in the order they will actually occur:

1. **Before H-03 is applied (today).** The gate rejects `negotiating`; the database would accept it. App stricter than DB. Benign, and the reason the reader is worth writing first: it closes M-08 for the only code path that exists.
2. **After H-03, stale read.** The gate reads `qualified` at T0; a human moves the company to `negotiating` at T1 (a legitimate transition after a reply); the `INSERT` at T2 fails the policy with `new row violates row-level security policy for table "outreach_messages"` — SQLSTATE **42501**, PostgREST HTTP 403. The gate has already said "eligible", so this arrives as a persistence failure, not a rejection with a code. Correct handling: the writer must not retry blindly, and the orchestration layer may re-read and re-decide once; nothing was written, so the failure is safe. **This is the normal disagreement**, and it is not a bug — it is the write-time check being fresher than the read.
3. **After H-03, invisible company.** The company is suppressed (or out of the agent's task scope); `agent_select_companies` returns no row; the reader returns `None`; the gate rejects `company_lookup_failed`. The policy's `EXISTS` is false too, so both layers agree — but for a reason neither can name. This is the M-03 lesson (a filtered empty set is not a clean set) applied to `companies`: the gate must never turn "I could not see it" into "it is fine", and the detail text must not pretend to know whether the cause is suppression, stage or scope.
4. **Half a change.** Someone decides `negotiating` should be allowed for a follow-up and edits only the app constant → the database refuses (403 for a legitimate draft); or edits only H-03 → the app refuses first and the DB edit is dead code. Nothing surfaces as an error; the change simply does not take effect. The parity test in §5.4 catches the first at review time; only B removes the class.

**Which layer wins: the database, always.** It evaluates at write time, it cannot be bypassed by a caller that skips the gate, and RLS is the tenancy boundary the whole design rests on. The gate's read is an *explanation*, not an authority: its value is that the operator gets `company_not_eligible: negotiating` instead of a 403 with no code. Consequence to state in the contract: a 42501 from the insert is never "the gate was wrong" and never a reason to widen the policy; it means the world moved between read and write.

### 5.6 Existing rows, backfill, migration and rollback

- **Existing rows:** none affected — no DDL, and the new reader only *reads* `companies` (1 row, Refokus, stage `identified`; the gate would now refuse to draft for it, which is the intended behaviour).
- **Backfill:** none.
- **Pre-flight:** none needed for the app change; for the later function, `SELECT count(*) FROM companies WHERE lifecycle_stage NOT IN (…)` is informational only.
- **Migration/rollback:** none in this batch. The app change rolls back by reverting the commit; the new codes are additive to a closed set, so nothing downstream that switches on `REJECTION_CODES` breaks.

### 5.7 The tests that would prove it

Application (`tests/test_outreach_gate.py`):

- `test_gate_refuses_a_company_that_is_already_negotiating` — `CompanyEligibility(lifecycle_stage="negotiating")` → `codes == (RejectionCode.COMPANY_NOT_ELIGIBLE,)`, and the writer is never called (`FakeDraftWriter` has zero rows). The second half is what makes it a gate rather than a filter.
- `test_a_company_the_reader_cannot_see_is_a_rejection_not_a_crash` — reader returns `None` → `company_lookup_failed`; reader raises `TimeoutError` → `company_lookup_failed`, never eligible. Mirrors the existing `test_qualification_reader_returning_a_row_is_a_rejection_not_a_crash`.
- `test_every_eligible_stage_still_passes` — parametrised over `qualified`, `outreach_queued`, `in_outreach`.

Database (`tests/db/test_outreach_schema_db.py`) — these prove the read path exists and is not a widening, which is the whole justification for not adding a DB object:

- `test_drafter_can_read_lifecycle_stage_for_a_task_scoped_company` — company + task for the drafter, JWT claims as in the existing `_claims` helper, `SELECT lifecycle_stage FROM companies WHERE id = …` returns one row. If this fails, option A is not viable and the proposal must switch to B.
- `test_drafter_cannot_read_a_company_without_a_task` — same identity, no task: 0 rows. The port cannot become a directory.

Static:

- `test_gate_stage_set_matches_the_pending_insert_policy` — the constant in `outreach/gate.py` equals the literal list parsed from `20261113_hardening_batch1.sql` L174. Fails the day either side changes alone.

---

## 6. Combined ordering

Apply as **separate pending files**, in this order, verifying after each. The order is chosen so that every step is independently applicable, every tightening lands before the one loosening, and nothing depends on a later step.

1. **`20261114_outreach_slot_unique.sql` — §2, the slot index.** One statement, no app change, closes an accepted medium that needs no other work. Highest value per line of review. It must be applied **before the first production writer exists**, because a writer written against a database without the index will have no 23505 path and will need a second change.
2. **§5, the `CompanyReader` (app-only, no migration).** Land it before H-03 is applied so the first operator who hits the lifecycle check gets a code instead of a 403. It has no DDL dependency and closes the explanatory half of M-08 today.
3. **`20261114_outreach_qualification_fk.sql` — §3(a)(b): the three unique constraints, `qualification_id`, its composite FK and index.** Six statements; makes the scorecard joinable; independent of everything else.
4. **`20261114_outreach_message_evidence.sql` — §3(c): the join table, grants and policies.** The largest review surface in this proposal; split from 3 so that a problem with the policies cannot hold the FK hostage, and vice versa.
5. **`20261114_outreach_recipient_optional.sql` — §4, nullable `contact_id` plus the checks — *with* the gate/payload change in the same PR.** Last because the DDL is inert without the app change and because it is the only loosening: it should arrive after the tightening tests exist, so the suite already has something to catch it with.
6. **§5's DB half (`fn_company_outreach_eligible` + H-03 rewritten to call it) — after H-03 is applied.** Not part of this proposal's batch; see §8.

Why not "all four in one migration": the acceptance criterion for each is *different evidence* (a concurrency test, an FK violation, a check violation, a rejection code). A single transaction means one bad policy blocks the index that closes M-06, and the review cannot approve them independently. The four files are also individually revertible, which the combined form is not.

---

## 7. Do not build yet

Each of these is deliberately excluded. The reason is the argument for excluding it, not a lack of interest.

1. **`qualification_id NOT NULL`, and "the cited scorecard must be the approved one".** Needs H-04 applied (so at most one approved row per company exists), a writer that always supplies it, and a decision about historical rows. Until then, the nullable FK plus H-03's "an approved scorecard exists" is the strongest statement that is *true*.
2. **`rubric_version TEXT` and `evidence_coverage NUMERIC(4,3)` on `outreach_messages`** (matrix §6.2). Both are derived from the scorecard, and there is no `rubric_version` on `lead_qualifications` to copy *from* — a text column here would be an unfalsifiable claim written by the same code that would have to be trusted. Add the column to `lead_qualifications` first, then join.
3. **Dropping `personalization_evidence_ids`.** It is the right end state once the gate reads the join table, but it requires changing `DraftPayload`, `to_row()`, `_verify_persisted` (L1063) and every test that asserts the array. Do it in the batch that changes the writer contract, not in a schema batch.
4. **A deferred constraint trigger enforcing array ⇄ join-table equality** (§3.6 residual). The machinery is justified when there are two writers; today RLS limits the insert path to a task-scoped `drafter` and there is one writer to change. Building a cross-table equality trigger before the second writer exists is speculative complexity in the most security-sensitive part of the schema.
5. **`to_address` / `recipient_url` columns for contact-form outreach.** The form URL is a property of the company (`companies.website_url`), and a copy on the message goes stale the moment the site changes. Nothing dispatches today; when a transport exists, the address belongs to the send attempt, not the draft.
6. **A partial slot index (`WHERE approval_status IN (…)`) so a rejected draft frees its slot.** Changes what "duplicate" means and forces `find_existing` to filter by status; two coupled changes for a weaker invariant. If the owner wants rejection to free the slot (D2), that is a product decision to take deliberately, not a side effect of hardening.
7. **Adding `contact_id` to the slot key.** Same class: it changes what counts as a duplicate (one draft per *recipient* rather than per company-step) and would make the app key and the index disagree unless both change in one PR. It is also a real product question — two decision-makers at one company may deserve the same campaign step.
8. **`MATCH FULL` on `fk_outreach_contact_company`.** Listed because it is the natural-looking "tightening" a reviewer might reach for. `MATCH FULL` requires all FK columns to be null or all non-null, and `company_id` is `NOT NULL` — so it would make the company-level draft of §4 *impossible*, i.e. the exact opposite of the intent.
9. **`fn_company_outreach_eligible` before H-03 is applied.** A third opinion on the stage set, by a `SECURITY DEFINER` function that bypasses RLS and therefore needs its own task-scope predicate. See §5.3.
10. **Column-level grants such as `GRANT SELECT (id, lifecycle_stage) ON companies`.** Column privileges cannot be added on top of the existing table-level grant without revoking it first, and RLS cannot express columns at all. The `CompanyReader` port is the column boundary; the grant stays table-level, as it already is.
11. **An audit trigger on `outreach_message_evidence`.** The parent message's own audit row (`trg_audit_outreach_messages`, L1665) already captures the array, so a second trail would record the same fact twice and could disagree with the first.

---

## 8. What is blocked on the pending hardening migration

**Applying this batch does not require `20261113_hardening_batch1.sql`.** None of the statements here reference an object it creates, and the objects it replaces are not touched. That is deliberate, not accidental: the two policy-adjacent sections (§4, §5) were designed so that **no policy change is needed**, precisely because H-03 drops and recreates `agent_insert_outreach_messages`.

Blocked on H-03 being applied:

- **Any edit to `agent_insert_outreach_messages`** — including a "small" tightening for company-level drafts. If authored before H-03 lands, H-03's `DROP POLICY` discards it silently; if authored after, it must be written on top of H-03's version. Nothing in this proposal needs it (§4.4).
- **`fn_company_outreach_eligible` + H-03 rewritten to call it** (§5.4, §5.5 shape 4). Until then the stage list exists in the app and in the pending policy, bridged only by a static parity test.
- **Any test asserting that the *insert path* refuses an unqualified company.** That is H-03's own test (`test_h03_drafting_requires_an_approved_qualification`), not this batch's; duplicating it here would produce two tests that fail for the same reason and disagree about which fix owns it.

Blocked on H-04 being applied:

- **`qualification_id NOT NULL`** and "the cited scorecard is the approved one" (§3.6, §7.1). Without a single-approved-per-company guarantee there is no unambiguous row to require.

Not blocked, but strictly *before*: the slot index (§2) should exist before any production `DraftWriter` is written, and the `CompanyReader` (§5) before H-03 is applied, so the first 403 an operator sees has a code next to it.

---

## 9. Approval checklist

- [ ] Owner confirms target is Development `vyfqmrdznmocnmzyjjcj` (not `vqnamszywsuqjxuqseas`) and that the four files below will be authored into `supabase/migrations/pending/`, never into the applied directory.
- [ ] Decisions D1–D5 recorded (§1), because each one changes the DDL that gets authored.
- [ ] Pre-flight counts re-run immediately before applying (`§2.6`, `§3.6`, `§4.7`). A non-zero `outreach_messages` count voids the compatibility statements in §2, §3 and §4 and requires the decisions named there.
- [ ] Fresh logical export taken (`backups/dev-snapshot-*.json`), per `pending/README.md` step 2.
- [ ] Files applied one at a time, in the §6 order, each wrapped in its own `BEGIN … COMMIT`, verifying between steps.
- [ ] `ERYNDEX_DB_URL=… python -m pytest tests/db -q` run after each file; output attached to `reports/eryndex/implementation-progress.md`.
- [ ] Each file moved into `supabase/migrations/` with its applied timestamp **only after** its verification passes, per the `pending/README.md` convention that a file in `supabase/migrations/` means applied.
- [ ] Owner accepts that the §2 rollback re-opens M-06 and the §4 rollback requires a decision about every recipient-less draft, so both need the same approval as applying.

**Verification limits of this document, stated once:** nothing here was executed. The Postgres semantics cited (`MATCH SIMPLE`, `NULLS NOT DISTINCT`, index inference for `ON CONFLICT`, SQLSTATE `23505`/`23503`/`23514`/`42501`) are from the PostgreSQL 17 documentation and the existing schema, not from a run — there is no local Postgres on this machine (`psql`, `initdb` absent; no Docker daemon) and no credential for the Development database. Row counts are the 2026-11-13 snapshot, not a fresh query. The version claim is the control plane's `postgres_engine: "17"` for `vyfqmrdznmocnmzyjjcj`, not `SHOW server_version`.
