-- =============================================================================
-- Let the drafter identity do its job
--
-- The drafter policies exist (agent_select_companies / contacts / campaigns / findings / messages,
-- agent_insert_outreach_messages, agent_select_lead_qualifications). One thing was missing: the
-- suppression ledger had no agent policy at all, so `PostgresSuppressionReader` could only raise —
-- which is correct behaviour for an unreadable state, but it meant no agent could ever pass the
-- gate. Reading the ledger is what lets an agent avoid contacting a suppressed party; it is a list
-- of identifiers that must not be contacted, not a secret.
--
-- Scope: an agent may read the registry while it holds an active assignment. It cannot write it
-- (no INSERT/UPDATE/DELETE policy, and `fn_enforce_suppression_immutability` makes rows permanent).
-- =============================================================================

BEGIN;

CREATE POLICY agent_select_suppression_registry ON suppression_registry FOR SELECT TO authenticated
    USING (
        is_agent()
        AND EXISTS (
            SELECT 1 FROM tasks t
             WHERE t.assigned_actor_type = 'agent'
               AND t.assigned_actor_identity = current_agent_identity()
               AND t.status IN ('pending', 'in_progress')
        )
    );

COMMIT;
