"""An authenticated operator session under Row Level Security.

The database decides whether the caller is a human operator: ``is_human_operator()``
reads ``app_metadata.user_role`` from the JWT. This module only makes sure the platform
never *pretends* a session is an operator one: the claim is checked locally before any
write, so an agent credential fails immediately with an explanation instead of producing
a confusing policy denial later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from contracts.environment import (
    EnvironmentConfig,
    assert_development_environment,
    load_env_file,
)
from integrations.supabase.auth import SupabaseAuthClient
from integrations.supabase.client import PostgrestClient

#: Roles the schema treats as human (``is_human_operator()`` in the core migration).
OPERATOR_ROLES = frozenset({"operator", "admin", "founder"})

REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "OPERATOR_EMAIL", "OPERATOR_PASSWORD")


class OperatorSessionError(RuntimeError):
    """No valid human operator session could be established."""


@dataclass(frozen=True)
class OperatorSession:
    """A signed-in human operator, with the identity the audit trail will record."""

    client: PostgrestClient
    user_id: str
    email: str
    role: str
    claims: Mapping[str, Any]

    @property
    def identity(self) -> str:
        return self.user_id


def _environment(environ: Optional[Mapping[str, str]]) -> dict[str, str]:
    """Resolve configuration: an explicit mapping is authoritative, disk is the default.

    Passing ``environ`` must not silently inherit ``.env`` — a caller that supplies a
    mapping (a test, or another deployment) would otherwise get a session authenticated
    with credentials it never named.
    """
    if environ is not None:
        return dict(environ)
    return {**load_env_file(), **os.environ}


def open_session(
    environ: Optional[Mapping[str, str]] = None,
    *,
    email: Optional[str] = None,
    password: Optional[str] = None,
) -> OperatorSession:
    """Sign in as the human operator and refuse anything that is not one.

    Fails closed on a missing variable, a refused sign-in, an unreadable claim, a
    non-operator role, or an environment that is not the approved Development project.
    Never returns a session for an agent identity.
    """
    env = _environment(environ)
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing and not (email and password):
        # Only name the variables; never echo a value or a fragment of one.
        raise OperatorSessionError(
            "missing operator configuration: " + ", ".join(sorted(set(missing)))
        )

    config = EnvironmentConfig.load(env)
    assert_development_environment(config)

    operator_email = email or env["OPERATOR_EMAIL"]
    operator_password = password or env["OPERATOR_PASSWORD"]

    auth = SupabaseAuthClient(config.supabase_url, config.anon_key)
    signed_in = auth.sign_in_with_password(operator_email, operator_password)
    claims = signed_in.get("claims") or {}
    app_metadata = claims.get("app_metadata") or {}
    role = str(app_metadata.get("user_role") or "")
    if role not in OPERATOR_ROLES:
        raise OperatorSessionError(
            "this identity is not a human operator: app_metadata.user_role is "
            f"{role or 'absent'!r}, expected one of {sorted(OPERATOR_ROLES)}. "
            "Gate actions must be taken by a human, so no session was opened."
        )

    user = signed_in.get("user") or {}
    user_id = str(claims.get("sub") or user.get("id") or "")
    if not user_id:
        raise OperatorSessionError("the signed-in identity carries no subject (user id)")

    client = PostgrestClient(
        supabase_url=config.supabase_url,
        anon_key=config.anon_key,
        access_token=signed_in["access_token"],
    )
    return OperatorSession(
        client=client,
        user_id=user_id,
        email=str(user.get("email") or operator_email),
        role=role,
        claims=claims,
    )
