"""Compatibility shim — environment loading now lives in ``contracts.environment``.

``Config`` keeps its historical field names because existing tests, CLIs and the
runner construct it by keyword (ADR-0002). New code should use
``contracts.environment.EnvironmentConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from contracts.environment import (  # noqa: F401  (re-exported for callers)
    APPROVED_DEV_PROJECT_REF,
    EnvironmentConfig,
    load_dotenv,
    load_env_file,
    normalize_supabase_url,
    validate_development_environment,
)


@dataclass
class Config:
    project_ref: str
    supabase_url: str
    anon_key: str
    researcher_email: str
    researcher_password: str
    researcher_agent_id: str

    @classmethod
    def load(cls, environ: Optional[Mapping[str, str]] = None) -> "Config":
        env = EnvironmentConfig.load(environ)
        return cls(
            project_ref=env.project_ref,
            supabase_url=env.supabase_url,
            anon_key=env.anon_key,
            researcher_email=env.researcher_email,
            researcher_password=env.researcher_password,
            researcher_agent_id=env.researcher_agent_id,
        )


__all__ = [
    "APPROVED_DEV_PROJECT_REF",
    "Config",
    "EnvironmentConfig",
    "load_dotenv",
    "load_env_file",
    "normalize_supabase_url",
    "validate_development_environment",
]
