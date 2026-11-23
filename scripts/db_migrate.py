"""Apply the SQL migrations in ``db/`` to the platform database, with a checksum ledger.

Replaces `supabase db push`, which here hung on Docker and which we no longer depend on.

    python scripts/db_migrate.py --status
    python scripts/db_migrate.py --dry-run
    python scripts/db_migrate.py

Rules that matter:

* A file is identified by its 14-digit version prefix and recorded with its sha256. A file
  that changed after it was applied is **refused**, not re-run — drift in an applied
  migration is a bug, and silently replaying DDL is how schemas diverge from their history.
* Each file is applied in one transaction. Two of the three legacy migrations carry their own
  `BEGIN; … COMMIT;`, so those are executed as-is and the file's own boundaries are honoured
  rather than nested.
* The connection is the owner. Application paths never do this: they connect as the owner but
  `SET LOCAL ROLE` on every transaction, which is what RLS evaluates.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from contracts.environment import load_env_file  # noqa: E402

MIGRATIONS_DIR = REPO / "db"
FOUNDATION = "0000_foundation"
VERSION_RE = re.compile(r"^(\d{4,14})_([a-z0-9_]+)\.sql$")
TRANSACTION_CONTROL = re.compile(r"^(BEGIN|COMMIT|ROLLBACK)\s*;", re.MULTILINE)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "postgres"}


class MigrationError(RuntimeError):
    """A migration cannot be applied, or the ledger disagrees with the files."""


@dataclass(frozen=True)
class Migration:
    path: Path
    version: str
    name: str
    sha256: str
    owns_transaction: bool

    @property
    def label(self) -> str:
        return f"{self.version} {self.name}"


def _config() -> tuple[str, str]:
    env = {**load_env_file(), **os.environ}
    dsn = (env.get("ERYNDEX_DB_URL") or "").strip()
    if not dsn:
        raise MigrationError(
            "ERYNDEX_DB_URL is not set. Add the local connection string to .env — the host is "
            "127.0.0.1 and the database is created by compose.yaml."
        )
    host = urlsplit(dsn).hostname or ""
    env_name = (env.get("ERYNDEX_ENV") or "development").strip().lower()
    if host not in LOCAL_HOSTS and env_name != "production":
        raise MigrationError(
            f"refusing to migrate a remote host ({host!r}) while ERYNDEX_ENV is {env_name!r}. "
            "Set ERYNDEX_ENV=production deliberately if that is really the target."
        )
    return dsn, env_name


def discover() -> list[Migration]:
    """Foundation first, then dated migrations in order."""
    found: list[Migration] = []
    foundation = MIGRATIONS_DIR / f"{FOUNDATION}.sql"
    if not foundation.exists():
        raise MigrationError(f"missing {foundation.relative_to(REPO)}")
    found.append(
        Migration(
            path=foundation,
            version=FOUNDATION,
            name="foundation",
            sha256=hashlib.sha256(foundation.read_bytes()).hexdigest(),
            owns_transaction=True,
        )
    )

    migrations_dir = MIGRATIONS_DIR / "migrations"
    if migrations_dir.exists():
        for path in sorted(migrations_dir.glob("*.sql")):
            match = VERSION_RE.match(path.name)
            if not match:
                raise MigrationError(
                    f"{path.name} is not named <version>_<name>.sql; the ledger cannot order it"
                )
            text = path.read_text(encoding="utf-8")
            found.append(
                Migration(
                    path=path,
                    version=match.group(1),
                    name=match.group(2),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    owns_transaction=bool(TRANSACTION_CONTROL.search(text)),
                )
            )
    return found


def _ledger(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
        if not cur.fetchone()[0]:
            return {}
        cur.execute("SELECT version, sha256 FROM public.schema_migrations")
        return {version: sha for version, sha in cur.fetchall()}


def _apply(conn, migration: Migration) -> None:
    """Execute one file, then record it.

    The connection is in autocommit mode so a file that carries its own ``BEGIN; … COMMIT;``
    is honoured exactly as written — nesting it inside another transaction would close the
    outer one at the file's COMMIT and leave the ledger write outside it.
    """
    # utf-8-sig: one of the legacy files carries a BOM, which PostgreSQL rejects as a token
    # before the first comment. The checksum still hashes the raw bytes, so a BOM change is
    # drift even though it is invisible in the SQL.
    sql = migration.path.read_text(encoding="utf-8-sig")
    if migration.owns_transaction:
        with conn.cursor() as cur:
            cur.execute(sql)
    else:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.schema_migrations (version, name, sha256) VALUES (%s, %s, %s)"
                " ON CONFLICT (version) DO UPDATE SET sha256 = EXCLUDED.sha256",
                (migration.version, migration.name, migration.sha256),
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply db/ migrations with a checksum ledger")
    parser.add_argument("--status", action="store_true", help="show applied/pending and exit")
    parser.add_argument("--dry-run", action="store_true", help="show what would run and exit")
    args = parser.parse_args(argv)

    import psycopg

    try:
        dsn, env_name = _config()
        migrations = discover()
    except MigrationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, autocommit=True) as conn:
        applied = _ledger(conn)
        pending = [m for m in migrations if m.version not in applied]

        drift = [
            m
            for m in migrations
            if m.version in applied and applied[m.version] != m.sha256
        ]
        if drift:
            for m in drift:
                print(
                    f"DRIFT: {m.label} was applied as {applied[m.version][:12]}… but the file is "
                    f"now {m.sha256[:12]}…",
                    file=sys.stderr,
                )
            print(
                "Refusing to continue: an applied migration must not be edited. Write a new "
                "migration instead.",
                file=sys.stderr,
            )
            return 2

        print(f"database: {env_name} · {len(applied)} applied · {len(pending)} pending")
        for m in migrations:
            mark = "applied" if m.version in applied else "PENDING"
            print(f"  {m.label:52} {mark}")

        if args.status or args.dry_run:
            return 0
        if not pending:
            print("nothing to apply")
            return 0

        for m in pending:
            print(f"applying {m.label} …", end=" ", flush=True)
            try:
                _apply(conn, m)
            except Exception as exc:  # noqa: BLE001 - report the real cause, then stop
                print("FAILED")
                print(f"  {exc.__class__.__name__}: {exc}", file=sys.stderr)
                return 1
            print("ok")

    print(f"applied {len(pending)} migration(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
