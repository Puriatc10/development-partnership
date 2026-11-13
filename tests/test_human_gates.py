"""Human gate tests (ADR-0005): agents can never pass a gate."""

from __future__ import annotations

import pytest

from contracts.errors import GateViolation
from orchestration.workflow import (
    GATES,
    Stage,
    assert_gate,
    next_stage,
    agent_forbidden_gates,
)

OPERATOR = {"app_metadata": {"user_role": "operator"}, "sub": "op-1"}
AGENT = {"app_metadata": {"user_role": "agent", "subagent_type": "researcher"}, "sub": "ag-1"}

EVIDENCE = {
    "G1": {"candidate_id": "c1", "reviewer": "op-1", "decision": "approved"},
    "G2": {"scorecard_id": "s1", "reviewer": "op-1", "decision": "approved"},
    "G3": {"message_id": "m1", "reviewer": "op-1", "decision": "approved"},
    "G4": {"message_id": "m1", "reviewer": "op-1", "authorized_at": "2026-11-13T00:00:00Z"},
}


def test_operator_can_pass_every_gate_with_evidence():
    for gate_id in GATES:
        gate = assert_gate(gate_id, OPERATOR, evidence=EVIDENCE[gate_id])
        assert gate.id == gate_id


@pytest.mark.parametrize("gate_id", sorted(GATES))
def test_agent_can_never_pass_a_gate(gate_id):
    with pytest.raises(GateViolation):
        assert_gate(gate_id, AGENT, evidence=EVIDENCE[gate_id])


def test_gate_without_evidence_is_rejected_even_for_operator():
    with pytest.raises(GateViolation) as exc:
        assert_gate("G1", OPERATOR, evidence={"candidate_id": "c1"})
    assert "missing required evidence" in str(exc.value)


def test_unknown_gate_is_rejected():
    with pytest.raises(GateViolation):
        assert_gate("G9", OPERATOR, evidence={})


def test_transition_requires_the_correct_source_stage():
    assert next_stage(Stage.DISCOVERED, "G1") is Stage.REGISTERED
    with pytest.raises(GateViolation):
        next_stage(Stage.QUALIFIED, "G1")


def test_agents_are_forbidden_every_gate():
    assert sorted(agent_forbidden_gates(AGENT)) == sorted(GATES)


def test_dispatch_is_a_separate_action_from_approval():
    assert GATES["G3"].target is Stage.OUTREACH_APPROVED
    assert GATES["G4"].separate_action is True
    assert GATES["G4"].required_roles == frozenset({"operator", "admin", "founder"})
