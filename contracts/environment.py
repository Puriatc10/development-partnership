"""Environment contract: loading, identity checks and the fail-closed guard.

Extracted from ``agents/researcher/config.py`` and
``agents/researcher/runner.py:validate_development_environment`` (ADR-0002) so
that every agent and adapter shares one guard.

Rules enforced here:
- only the approved Development project ref may be used by platform code;
- a missing or malformed configuration fails before any network call;
- secrets are never included in error messages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .errors import EnvironmentGuardError

APPROVED_DEV_PROJECT_REF = "vyfqmrdznmocnmzyjjcj"
APPROVED_DEV_SUPABASE_URL = "https://vyfqmrdznmocnmzyjjcj.supabase.co"
PRODUCTION_PROJECT_REF = "vqnamszywsuqjxuqseas"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Optional[str | os.PathLike[str]] = None) -> dict[str, str]:
    """Read a .env file into a dict without mutating ``os.environ``.

    Never raises on a missing file: absence of configuration is reported by the
    caller, not by an exception that hides its cause.
    """
    dotenv_path = Path(path) if path else _REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_dotenv(dotenv_path: Optional[str] = None) -> None:
    """Backwards-compatible shim: populate ``os.environ`` from .env.

    Preserved for ``agents.researcher.config`` compatibility (ADR-0002).
    """
    for key, value in load_env_file(dotenv_path).items():
        os.environ.setdefault(key, value)


def normalize_supabase_url(url: Optional[str]) -> str:
    if not url or not isinstance(url, str):
        return ""
    cleaned = url.strip().rstrip("/")
    if "://" in cleaned:
        scheme, rest = cleaned.split("://", 1)
        return f"{scheme.lower()}://{rest.lower()}"
    return cleaned.lower()


@dataclass(frozen=True)
class EnvironmentConfig:
    project_ref: str
    supabase_url: str
    anon_key: str
    researcher_email: str = ""
    researcher_password: str = ""
    researcher_agent_id: str = "hermes-researcher-v1"

    @classmethod
    def load(cls, environ: Optional[Mapping[str, str]] = None) -> "EnvironmentConfig":
        env = dict(environ) if environ is not None else {**load_env_file(), **os.environ}
        project_ref = env.get("SUPABASE_PROJECT_REF", APPROVED_DEV_PROJECT_REF)
        return cls(
            project_ref=project_ref,
            supabase_url=env.get("SUPABASE_URL", f"https://{project_ref}.supabase.co"),
            anon_key=env.get("SUPABASE_ANON_KEY", ""),
            researcher_email=env.get("RESEARCHER_EMAIL", "researcher.v1@agents.internal"),
            researcher_password=env.get("RESEARCHER_PASSWORD", ""),
            researcher_agent_id=env.get("RESEARCHER_AGENT_ID", "hermes-researcher-v1"),
        )

    # ``Config`` compatibility aliases used by the existing researcher package.
    @property
    def agent_id(self) -> str:
        return self.researcher_agent_id


def assert_development_environment(
    config: EnvironmentConfig,
    *,
    allowed_refs: Iterable[str] = (APPROVED_DEV_PROJECT_REF,),
) -> None:
    """Fail closed unless the configuration targets an allow-listed project.

    The production ref is *always* rejected, even if someone adds it to the
    allow-list by mistake.
    """
    if config is None:
        raise EnvironmentGuardError("Environment guard failure: configuration is required.")

    ref = (config.project_ref or "").strip()
    if not ref:
        raise EnvironmentGuardError("Environment guard failure: SUPABASE_PROJECT_REF is missing or empty.")
    if ref == PRODUCTION_PROJECT_REF:
        raise EnvironmentGuardError(
            "Environment guard failure: Production is never a valid target for platform code."
        )
    if ref not in set(allowed_refs):
        raise EnvironmentGuardError(
            f"Environment guard failure: project ref '{ref}' is not allow-listed. "
            f"Allowed: {sorted(allowed_refs)}."
        )

    url = normalize_supabase_url(config.supabase_url)
    if not url:
        raise EnvironmentGuardError("Environment guard failure: SUPABASE_URL is missing or empty.")
    expected = normalize_supabase_url(APPROVED_DEV_SUPABASE_URL)
    if url != expected:
        raise EnvironmentGuardError(
            "Environment guard failure: Supabase URL does not match the approved Development endpoint."
        )
    if "@" in url.split("://", 1)[-1].split("/", 1)[0]:
        # Defensive: never echo credentials that a malformed URL may carry.
        raise EnvironmentGuardError(
            "Environment guard failure: Supabase URL must not contain embedded credentials."
        )


# Backwards-compatible alias (existing runner imports this name).
validate_development_environment = assert_development_environment
