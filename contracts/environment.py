"""Environment contract: where the database is, and what is allowed to be touched.

The platform no longer has a project reference to pin — it has a connection string. The guard
that used to refuse the Supabase Production project now refuses any host that is not explicitly
allow-listed, which is the same protection expressed for a self-hosted database:

* in development the host must be local (a container on this machine);
* naming a remote host requires ``ERYNDEX_ENV=production`` **and** listing it explicitly;
* a missing or malformed DSN fails before any connection is attempted;
* secrets are never echoed — messages name variables, never values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional
from urllib.parse import urlsplit

from .errors import EnvironmentGuardError

DEVELOPMENT = "development"
PRODUCTION = "production"
KNOWN_ENVIRONMENTS = (DEVELOPMENT, PRODUCTION)

#: Hosts that can only be a local container. Compose service names count because they resolve
#: inside the compose network; none of them can leave this machine.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", "postgres", "db"})

_REPO_ROOT = Path(__file__).resolve().parent.parent

SUPABASE_RETIRED_MESSAGE = (
    "Supabase is retired in this repository; use DatabaseConfig (ERYNDEX_DB_URL) instead. "
    "See docs/MIGRATION_TO_SELF_HOSTED_POSTGRES.md."
)


def load_env_file(path: Optional[str | os.PathLike[str]] = None) -> dict[str, str]:
    """Read a .env file into a dict without mutating ``os.environ``."""
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
    """Populate ``os.environ`` from .env without overwriting anything already set."""
    for key, value in load_env_file(dotenv_path).items():
        os.environ.setdefault(key, value)


def _resolved(environ: Optional[Mapping[str, str]]) -> dict[str, str]:
    if environ is not None:
        return dict(environ)
    return {**load_env_file(), **os.environ}


@dataclass(frozen=True)
class DatabaseConfig:
    """The database the platform talks to, and the environment it claims to be."""

    dsn: str
    env: str = DEVELOPMENT
    jwt_secret: str = ""

    @classmethod
    def load(cls, environ: Optional[Mapping[str, str]] = None) -> "DatabaseConfig":
        values = _resolved(environ)
        return cls(
            dsn=(values.get("ERYNDEX_DB_URL") or "").strip(),
            env=(values.get("ERYNDEX_ENV") or DEVELOPMENT).strip().lower(),
            jwt_secret=(values.get("ERYNDEX_JWT_SECRET") or "").strip(),
        )

    @property
    def host(self) -> str:
        return urlsplit(self.dsn).hostname or ""

    @property
    def database(self) -> str:
        return (urlsplit(self.dsn).path or "").lstrip("/")

    @property
    def is_production(self) -> bool:
        return self.env == PRODUCTION


def assert_database_environment(
    config: DatabaseConfig,
    *,
    allowed_hosts: Iterable[str] | None = None,
) -> None:
    """Fail closed unless the connection string targets a database we are allowed to touch."""
    if config is None:
        raise EnvironmentGuardError("Environment guard failure: configuration is required.")

    dsn = (config.dsn or "").strip()
    if not dsn:
        raise EnvironmentGuardError(
            "Environment guard failure: ERYNDEX_DB_URL is missing or empty."
        )
    if config.env not in KNOWN_ENVIRONMENTS:
        raise EnvironmentGuardError(
            f"Environment guard failure: ERYNDEX_ENV is {config.env!r}; expected one of "
            f"{list(KNOWN_ENVIRONMENTS)}."
        )

    parts = urlsplit(dsn)
    if parts.scheme not in {"postgresql", "postgres"}:
        raise EnvironmentGuardError(
            "Environment guard failure: ERYNDEX_DB_URL must be a postgresql:// connection string."
        )
    if not parts.username:
        raise EnvironmentGuardError(
            "Environment guard failure: the connection string names no role."
        )
    host = parts.hostname or ""
    if not host:
        raise EnvironmentGuardError("Environment guard failure: the connection string has no host.")
    if not (parts.path or "").lstrip("/"):
        raise EnvironmentGuardError("Environment guard failure: the connection string has no database.")

    if config.is_production:
        permitted = {h.strip().lower() for h in (allowed_hosts or ()) if h and h.strip()}
        if host.lower() not in permitted:
            raise EnvironmentGuardError(
                "Environment guard failure: ERYNDEX_ENV=production but the host is not in the "
                "explicit allow-list. Pass allowed_hosts — production is never reached by default."
            )
        return

    if host.lower() not in LOCAL_HOSTS:
        raise EnvironmentGuardError(
            f"Environment guard failure: host {host!r} is not local while ERYNDEX_ENV is "
            f"{config.env!r}. A non-local database requires ERYNDEX_ENV=production."
        )
