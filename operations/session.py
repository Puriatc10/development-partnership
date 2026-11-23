"""Operator session: a signed-in human, with the claims the database will check.

The pattern is unchanged from the Supabase version — authenticate, verify the role locally so a
non-human fails immediately with an explanation, then hand the claims to the database on every
call. What changed is the source: a local `auth.users` row and an HS256 token instead of GoTrue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from contracts.environment import DatabaseConfig, assert_database_environment, load_env_file
from integrations.postgres.auth import PostgresAuthClient
from integrations.postgres.client import PostgresClient

#: Roles the schema treats as human (`is_human_operator()` in the core migration).
OPERATOR_ROLES = frozenset({"operator", "admin", "founder"})

REQUIRED_ENV = ("ERYNDEX_DB_URL", "ERYNDEX_JWT_SECRET", "OPERATOR_EMAIL", "OPERATOR_PASSWORD")


class OperatorSessionError(RuntimeError):
    """No valid human operator session could be established."""


@dataclass(frozen=True)
class OperatorSession:
    """A signed-in human operator, with the identity the audit trail will record."""

    client: PostgresClient
    user_id: str
    email: str
    role: str
    claims: Mapping[str, Any]

    @property
    def identity(self) -> str:
        return self.user_id


class AgentSessionError(RuntimeError):
    """No valid agent session could be established for the requested subagent type."""


@dataclass(frozen=True)
class AgentSession:
    """A signed-in agent, with the identity its rows will carry.

    ``user_id`` here is the agent's ``agent_id`` claim, not a human uuid — outreach rows record it
    in ``drafted_by_agent_identity``, which is a text column compared against
    ``current_agent_identity()`` by the insert policy.
    """

    client: PostgresClient
    agent_id: str
    email: str
    subagent_type: str
    claims: Mapping[str, Any]

    @property
    def identity(self) -> str:
        return self.agent_id

    @property
    def user_id(self) -> str:
        # CLI call sites and the draft writer both read `user_id`; for an agent the row identity is
        # the agent id, so the two names must not diverge.
        return self.agent_id


def open_agent_session(
    subagent_type: str,
    environ: Optional[Mapping[str, str]] = None,
    *,
    email: Optional[str] = None,
    password: Optional[str] = None,
    expected_agent_id: Optional[str] = None,
) -> AgentSession:
    """Sign in as an agent of ``subagent_type`` and refuse anything else.

    The database decides what an agent may do; this function only refuses to hand a caller a
    session whose claims do not say what the caller thinks they say. A human token passed here is
    an error, not a downgrade: the agent policies would deny every row and the caller would report
    an empty database instead of a permission mistake.
    """
    env = _environment(environ)
    if not (email and password):
        raise AgentSessionError(
            f"no credentials for the {subagent_type!r} agent: pass email and password, or set "
            f"{subagent_type.upper()}_EMAIL and {subagent_type.upper()}_PASSWORD"
        )

    config = DatabaseConfig.load(env)
    assert_database_environment(config)

    auth = PostgresAuthClient(config.dsn, config.jwt_secret)
    try:
        signed_in = auth.sign_in_with_password(email, password)
    except PermissionError as exc:
        raise AgentSessionError(str(exc)) from exc

    claims = dict(signed_in.get("claims") or {})
    app_metadata = dict(claims.get("app_metadata") or {})
    role = str(app_metadata.get("user_role") or "")
    actual_type = str(app_metadata.get("subagent_type") or "")
    agent_id = str(app_metadata.get("agent_id") or "")

    if role != "agent":
        raise AgentSessionError(
            f"this identity is not an agent: app_metadata.user_role is {role or 'absent'!r}. "
            "No agent session was opened."
        )
    if actual_type != subagent_type:
        raise AgentSessionError(
            f"this identity is a {actual_type or 'untyped'!r} agent, not {subagent_type!r}; "
            "the two are different policies."
        )
    if not agent_id:
        raise AgentSessionError("the agent identity carries no agent_id; every policy compares it")
    if expected_agent_id and agent_id != expected_agent_id:
        raise AgentSessionError(
            f"this identity is {agent_id!r}, not the configured {expected_agent_id!r}"
        )

    client = PostgresClient(config.dsn, role="authenticated", claims=claims)
    return AgentSession(
        client=client,
        agent_id=agent_id,
        email=str(signed_in["user"].get("email") or email),
        subagent_type=subagent_type,
        claims=claims,
    )


def _environment(environ: Optional[Mapping[str, str]]) -> dict[str, str]:
    """Resolve configuration: an explicit mapping is authoritative, disk is the default."""
    if environ is not None:
        return dict(environ)
    return {**load_env_file(), **os.environ}


def open_session(
    environ: Optional[Mapping[str, str]] = None,
    *,
    email: Optional[str] = None,
    password: Optional[str] = None,
) -> OperatorSession:
    """Sign in as the human operator and refuse anything that is not one."""
    env = _environment(environ)
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing and not (email and password):
        raise OperatorSessionError(
            "missing operator configuration: " + ", ".join(sorted(set(missing)))
        )

    config = DatabaseConfig.load(env)
    assert_database_environment(config)

    operator_email = email or env["OPERATOR_EMAIL"]
    operator_password = password or env["OPERATOR_PASSWORD"]

    auth = PostgresAuthClient(config.dsn, config.jwt_secret)
    try:
        signed_in = auth.sign_in_with_password(operator_email, operator_password)
    except PermissionError as exc:
        raise OperatorSessionError(str(exc)) from exc

    claims = dict(signed_in.get("claims") or {})
    app_metadata = claims.get("app_metadata") or {}
    role = str(app_metadata.get("user_role") or "")
    if role not in OPERATOR_ROLES:
        raise OperatorSessionError(
            "this identity is not a human operator: app_metadata.user_role is "
            f"{role or 'absent'!r}, expected one of {sorted(OPERATOR_ROLES)}. "
            "Gate actions must be taken by a human, so no session was opened."
        )

    user_id = str(claims.get("sub") or "")
    if not user_id:
        raise OperatorSessionError("the signed-in identity carries no subject (user id)")

    client = PostgresClient(config.dsn, role="authenticated", claims=claims)
    return OperatorSession(
        client=client,
        user_id=user_id,
        email=str(signed_in["user"].get("email") or operator_email),
        role=role,
        claims=claims,
    )
