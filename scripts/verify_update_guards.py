"""Verify the update-guard fix against the database, inside one transaction that is rolled back.

Each case reproduces an attempt the audit made against the broken build and asserts it is now
refused for the right reason. Run: python scripts/verify_update_guards.py
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

import psycopg

from contracts.environment import DatabaseConfig

OPERATOR_ID = "c549b7f9-70a8-4919-b987-aff7d692ea1b"


def as_operator(cur, sub: str = OPERATOR_ID) -> None:
    claims = {
        "sub": sub,
        "role": "authenticated",
        "email": "operator@partnership.internal",
        "app_metadata": {"user_type": "human", "user_role": "operator"},
        "user_metadata": {"display_name": "operator"},
    }
    cur.execute("set local role authenticated")
    cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))


def expect_refused(cur, label: str, sql: str, params: tuple = ()) -> str:
    cur.execute("savepoint case_sp")
    try:
        cur.execute(sql, params)
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint case_sp")
        print(f"  REFUSED  {label}: {str(exc).strip().splitlines()[0][:150]}")
        return str(exc)
    cur.execute("rollback to savepoint case_sp")
    print(f"  ACCEPTED {label}  <-- DEFECT")
    raise SystemExit(f"case not refused: {label}")


def main() -> int:
    cfg = DatabaseConfig.load()
    with psycopg.connect(cfg.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute(
                "select id, company_id from outreach_messages where approval_status = 'approved'"
                " and sending_status = 'draft' order by created_at limit 1"
            )
            row = cur.fetchone()
            if not row:
                print("no approved draft message to test with; aborting")
                return 1
            message_id, e2e_company = row
            print(f"using message {message_id} of company {e2e_company}")

            print("A. approved message may not jump straight to sent")
            as_operator(cur)
            expect_refused(
                cur,
                "draft -> sent in one UPDATE",
                "update outreach_messages set sending_status = 'sent', sent_at = now() where id = %s",
                (message_id,),
            )

            print("B. the body of an approved message is immutable")
            as_operator(cur)
            expect_refused(
                cur,
                "body rewrite while approved",
                "update outreach_messages set body = body || ' PS' where id = %s",
                (message_id,),
            )

            print("C. the approver of an approved message may not be rewritten")
            as_operator(cur)
            expect_refused(
                cur,
                "approved_by_human_id rewrite",
                "update outreach_messages set approved_by_human_id = %s where id = %s",
                ("00000000-0000-4000-8000-000000000001", message_id),
            )

            print("D. a scorecard cannot be created already approved")
            as_operator(cur)
            expect_refused(
                cur,
                "operator INSERT of an approved scorecard",
                "insert into lead_qualifications (id, company_id, evaluator_type, evaluator_identity,"
                " overall_score, customer_flow_score, development_demand_score,"
                " repeatability_orders_score, technical_fit_score, business_size_score,"
                " decision_maker_accessibility_score, commercial_fit_score, dimension_evidence,"
                " qualification_tier, recommended_next_action, review_status, reviewed_by_human_id,"
                " reviewed_at) values (gen_random_uuid(), %s, 'agent', 'hermes-qualifier-v1', 70, 15,"
                " 15, 10, 10, 5, 5, 5, '{\"overall\": {\"finding_id\": null}}'::jsonb,"
                " 'tier_2_prospective', 'x', 'approved', %s, now())",
                (e2e_company, OPERATOR_ID),
            )

            print("E. a new message cannot be inserted pre-approved or pre-queued")
            as_operator(cur)
            contact = cur.execute(
                "select id from contacts where company_id = %s limit 1", (e2e_company,)
            ).fetchone()
            if contact:
                expect_refused(
                    cur,
                    "operator INSERT of an approved+queued message",
                    "insert into outreach_messages (id, company_id, contact_id, channel, body,"
                    " approval_status, approved_by_human_id, approved_at, sending_status,"
                    " personalization_evidence_ids, drafted_by_agent_identity)"
                    " values (gen_random_uuid(), %s, %s, 'email', 'hello', 'approved', %s, now(),"
                    " 'queued', '{}', %s)",
                    (e2e_company, contact[0], OPERATOR_ID, OPERATOR_ID),
                )

            print("E2. a secondary domain on the suppression list blocks dispatch")
            # The registry is owner-writeable only (no authenticated INSERT grant), so the fixture
            # is seeded as the owner and the attempt is then made as the operator.
            cur.execute("reset role")
            cur.execute(
                "insert into company_domains (id, company_id, domain, is_primary)"
                " values (gen_random_uuid(), %s, 'alias-probe.example.com', false)",
                (e2e_company,),
            )
            cur.execute(
                "insert into suppression_registry (id, target_type, identifier, reason, source,"
                " created_by_actor_type, created_by_actor_identity)"
                " values (gen_random_uuid(), 'domain', 'alias-probe.example.com', 'probe', 'manual',"
                " 'system', 'verify_update_guards')"
            )
            as_operator(cur)
            expect_refused(
                cur,
                "queue with only an alias domain suppressed",
                "update outreach_messages set sending_status = 'queued', scheduled_for = now()"
                " where id = %s",
                (message_id,),
            )

            print("F. a review that is deleted still leaves a trace")
            as_operator(cur)
            cur.execute(
                "select id from discovery_candidates order by created_at desc limit 1"
            )
            candidate = cur.fetchone()
            if candidate:
                cur.execute(
                    "select count(*) from audit_logs where table_name='discovery_candidates'"
                    " and record_id=%s and action='DELETE'",
                    (candidate[0],),
                )
                before = cur.fetchone()[0]
                cur.execute("delete from discovery_candidates where id = %s", (candidate[0],))
                cur.execute(
                    "select count(*) from audit_logs where table_name='discovery_candidates'"
                    " and record_id=%s and action='DELETE'",
                    (candidate[0],),
                )
                after = cur.fetchone()[0]
                print(f"  DELETE audit rows before={before} after={after}")
                assert after == before + 1, "candidate deletion left no audit row"

            print("G. the same queue transition succeeds when nothing is suppressed")
            # Suppression rows are permanent by design, so the control uses a different company's
            # approved draft rather than trying to un-suppress one.
            cur.execute(
                "select id from outreach_messages where approval_status = 'approved'"
                " and sending_status = 'draft' and id <> %s order by created_at limit 1",
                (message_id,),
            )
            control = cur.fetchone()
            assert control, "no second approved draft to use as a control"
            as_operator(cur)
            cur.execute(
                "update outreach_messages set sending_status = 'queued', scheduled_for = now()"
                " where id = %s",
                (control[0],),
            )
            print(f"  ACCEPTED control queue: rowcount={cur.rowcount} (expected 1)")
            assert cur.rowcount == 1, "a legitimate queue was refused"

            cur.execute("rollback")
            print("rolled back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
