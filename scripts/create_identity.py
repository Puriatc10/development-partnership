"""Create or update a local identity in ``auth.users``.

Replaces GoTrue's admin API. Three identities make up the platform's identity model, and each is
just a row plus the claims the policies read:

    operator   app_metadata.user_role = operator
    discovery  app_metadata = {user_role: agent, subagent_type: discovery, agent_id: …}
    qualifier  app_metadata = {user_role: agent, subagent_type: qualifier, agent_id: …}

Passwords are read from a prompt, or from a named environment variable for automation. They are
never accepted as a command-line argument (that puts them in the shell history and in `ps`) and
never printed.

    python scripts/create_identity.py --email operator@partnership.internal --operator
    python scripts/create_identity.py --email discovery.v1@agents.internal \
        --agent-type discovery --agent-id hermes-discovery-v1
    python scripts/create_identity.py --list
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import psycopg  # noqa: E402

from contracts.environment import DatabaseConfig, assert_database_environment  # noqa: E402
from contracts.errors import EnvironmentGuardError  # noqa: E402
from integrations.postgres.auth import hash_password  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "postgres", "db"}


def _connect(config: DatabaseConfig) -> psycopg.Connection:
    host = urlsplit(config.dsn).hostname or ""
    if host not in LOCAL_HOSTS and config.env != "production":
        raise SystemExit(f"refusing: host {host!r} is not local in {config.env!r}")
    return psycopg.connect(config.dsn, autocommit=True)


def _app_metadata(args: argparse.Namespace) -> dict[str, str]:
    if args.operator:
        return {"user_role": args.role or "operator"}
    metadata = {"user_role": "agent"}
    if not args.agent_type:
        raise SystemExit("--agent-type is required for an agent identity (discovery or qualifier)")
    metadata["subagent_type"] = args.agent_type
    metadata["agent_id"] = args.agent_id or f"hermes-{args.agent_type}-v1"
    return metadata


def _password(args: argparse.Namespace) -> str:
    if args.password_env:
        # Environment first (CI), then .env (this workstation). The value is never printed.
        from contracts.environment import load_env_file

        value = os.environ.get(args.password_env) or load_env_file().get(args.password_env, "")
        if not value:
            raise SystemExit(f"{args.password_env} is empty or unset")
        return value
    first = getpass.getpass("password: ")
    if first != getpass.getpass("repeat: "):
        raise SystemExit("passwords did not match")
    if len(first) < 8:
        raise SystemExit("a password must be at least 8 characters")
    return first


def _list(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, app_metadata, is_active, last_sign_in_at FROM auth.users ORDER BY email"
        )
        rows = cur.fetchall()
    if not rows:
        print("no identities")
        return
    for user_id, email, metadata, active, last in rows:
        flag = "active" if active else "disabled"
        print(f"{email:36} {flag:9} {json.dumps(metadata, sort_keys=True)}  id={user_id}  last={last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an identity in auth.users")
    parser.add_argument("--email")
    parser.add_argument("--operator", action="store_true", help="create a human operator identity")
    parser.add_argument("--role", default=None, help="operator role: operator | admin | founder")
    parser.add_argument("--agent-type", default=None, help="agent subagent_type, e.g. discovery")
    parser.add_argument("--agent-id", default=None)
    parser.add_argument("--password-env", default=None, help="read the password from this variable")
    parser.add_argument("--update", action="store_true", help="update an existing identity")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = DatabaseConfig.load()
        assert_database_environment(config)
    except EnvironmentGuardError as exc:
        print(f"environment refused: {exc}", file=sys.stderr)
        return 2

    with _connect(config) as conn:
        if args.list:
            _list(conn)
            return 0
        if not args.email:
            print("--email is required", file=sys.stderr)
            return 2

        metadata = _app_metadata(args)
        password = _password(args)
        password_hash = hash_password(password)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO auth.users (email, password_hash, app_metadata)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (email) DO UPDATE
                   SET password_hash = EXCLUDED.password_hash,
                       app_metadata = EXCLUDED.app_metadata,
                       is_active = TRUE,
                       updated_at = clock_timestamp()
                RETURNING id, (xmax = 0) AS inserted
                """,
                (args.email, password_hash, json.dumps(metadata)),
            )
            user_id, inserted = cur.fetchone()

    action = "created" if inserted else "updated"
    print(f"{action} {args.email}")
    print(f"  id          {user_id}")
    print(f"  app_metadata {json.dumps(metadata, sort_keys=True)}")
    print("  password    (not displayed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
