"""Workflow stages and human approval gates (ADR-0005).

A gate is a state transition that only a human operator may perform. The check
is made against the caller's verified JWT claims, not against a code path, so an
agent cannot satisfy a gate by calling the "right" function.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence

from contracts.errors import GateViolation


class Stage(str, Enum):
    DISCOVERED = "discovered"
    REGISTERED = "registered"
    RESEARCHED = "researched"
    QUALIFIED = "qualified"
    STRATEGIZED = "strategized"
    OUTREACH_DRAFTED = "outreach_drafted"
    OUTREACH_APPROVED = "outreach_approved"
    ENGAGED = "engaged"
    PARTNERSHIP = "partnership"


OPERATOR_ROLES = frozenset({"operator", "admin", "founder"})


@dataclass(frozen=True)
class Gate:
    id: str
    name: str
    source: Stage
    target: Stage
    required_roles: frozenset[str]
    evidence_required: Sequence[str]
    separate_action: bool = False


GATES: dict[str, Gate] = {
    "G1": Gate(
        id="G1",
        name="Company ingestion",
        source=Stage.DISCOVERED,
        target=Stage.REGISTERED,
        required_roles=OPERATOR_ROLES,
        evidence_required=("candidate_id", "reviewer", "decision"),
    ),
    "G2": Gate(
        id="G2",
        name="Qualification review",
        source=Stage.RESEARCHED,
        target=Stage.QUALIFIED,
        required_roles=OPERATOR_ROLES,
        evidence_required=("scorecard_id", "reviewer", "decision"),
    ),
    "G3": Gate(
        id="G3",
        name="Outreach draft approval",
        source=Stage.OUTREACH_DRAFTED,
        target=Stage.OUTREACH_APPROVED,
        required_roles=OPERATOR_ROLES,
        evidence_required=("message_id", "reviewer", "decision"),
    ),
    "G4": Gate(
        id="G4",
        name="Dispatch authorization",
        source=Stage.OUTREACH_APPROVED,
        target=Stage.ENGAGED,
        required_roles=OPERATOR_ROLES,
        evidence_required=("message_id", "reviewer", "authorized_at"),
        separate_action=True,
    ),
}


def actor_role(claims: Mapping[str, object]) -> str:
    app_metadata = claims.get("app_metadata")
    if isinstance(app_metadata, Mapping):
        return str(app_metadata.get("user_role") or "")
    return ""


def assert_gate(
    gate_id: str,
    claims: Mapping[str, object],
    *,
    evidence: Mapping[str, object] | None = None,
) -> Gate:
    """Return the gate if this actor may pass it, else raise GateViolation."""
    gate = GATES.get(gate_id)
    if gate is None:
        raise GateViolation(f"Unknown gate: {gate_id!r}")

    role = actor_role(claims)
    if role not in gate.required_roles:
        raise GateViolation(
            f"Gate {gate.id} ({gate.name}) requires one of {sorted(gate.required_roles)}; "
            f"caller role is {role!r}. Agents may never satisfy a human gate."
        )

    provided = set((evidence or {}).keys())
    missing = [field for field in gate.evidence_required if field not in provided]
    if missing:
        raise GateViolation(
            f"Gate {gate.id} ({gate.name}) is missing required evidence: {missing}"
        )
    return gate


def next_stage(current: Stage, gate_id: str) -> Stage:
    gate = GATES[gate_id]
    if gate.source != current:
        raise GateViolation(
            f"Gate {gate_id} applies to {gate.source.value}, not {current.value}"
        )
    return gate.target


def allowed_transitions(stage: Stage, human_role: str) -> list[str]:
    """Which gates this actor could pass from the given stage (used by the CLI)."""
    return [
        g.id
        for g in GATES.values()
        if g.source == stage and human_role in g.required_roles
    ]


def agent_forbidden_gates(agent_claims: Mapping[str, object]) -> Iterable[str]:
    """Convenience for tests: gates this actor can never pass."""
    role = actor_role(agent_claims)
    return [g.id for g in GATES.values() if role not in g.required_roles]
