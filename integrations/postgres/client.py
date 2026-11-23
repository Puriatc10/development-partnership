"""PostgreSQL data access with Row Level Security — the replacement for PostgREST.

The application used to speak HTTP to PostgREST and hope the filter semantics matched what the
policies expected. This module speaks to the database directly and makes the identity boundary
explicit: every call runs in its own transaction, drops into one of the three roles with
``SET LOCAL ROLE``, and sets ``request.jwt.claims`` for that transaction only.

Three properties are deliberate and worth stating, because they are what makes this safe:

1. **No connection is trusted.** The pool connects as the owner, but no statement ever runs as
   it: the first thing every transaction does is ``SET LOCAL ROLE``, which is reverted
   automatically at commit or rollback. RLS therefore applies to every read and write.
2. **Identifiers are checked, values are bound.** Table and column names are validated against
   the live catalogue; values are always parameters. There is no path where a caller-supplied
   string becomes SQL.
3. **Fail closed.** An unrecognised filter operator raises instead of being ignored — a
   silently dropped ``WHERE`` is how a policy gets bypassed by accident.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: Operators the code actually uses. Anything else is a bug in the caller, not a filter to skip.
FILTER_OPERATORS = ("eq.", "neq.", "in.", "is.null", "not.is.null", "gte.", "lte.", "gt.", "lt.")

RESERVED_PARAMS = frozenset({"select", "order", "limit", "offset"})


class DatabaseError(RuntimeError):
    """A statement was refused or failed.

    Shaped like the PostgREST error the code was written against (`status_code`, `code`,
    `message`) so existing handlers keep working: `code` is the SQLSTATE, so `42501` still
    means "a policy refused this" and `23514` still means "a check constraint fired".
    """

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        label = f"[{code}] " if code else ""
        super().__init__(f"Database error {label}{message}")
        self.message = message
        self.code = code
        self.status_code = status_code


#: Historical name, so no caller has to change its `except`.
PostgrestError = DatabaseError


def _ident(name: str, what: str) -> str:
    if not IDENTIFIER.match(name or ""):
        raise DatabaseError(f"{what} {name!r} is not a valid SQL identifier")
    return name


_NON_FILTER_PARAMS = frozenset({"select", "order", "limit", "offset"})


def _require_filter(
    table: str, params: Mapping[str, Any] | None, operation: str, allow_all: bool
) -> None:
    """Refuse a whole-table write unless the caller says so explicitly.

    Every row the statement would touch is one the caller never named. RLS narrows the damage but
    does not express intent, so an unfiltered ``patch``/``delete`` is an error, not a convenience.
    """
    filters = [key for key in (params or {}) if key not in _NON_FILTER_PARAMS]
    if not filters and not allow_all:
        raise DatabaseError(
            f"{operation} on {table!r} has no filter; pass the row's identifying column "
            f"(or allow_all=True if every visible row really is meant)"
        )


def _split_top_level(value: str) -> list[str]:
    """Split ``a,'b,c',d`` on commas that are not inside single quotes."""
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for char in value:
        if char == "'":
            quoted = not quoted
            current.append(char)
        elif char == "," and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if quoted:
        raise DatabaseError(
            f"unterminated quote in filter list {value!r}: values containing an apostrophe must be "
            "written as 'O''Brien'; refusing to guess"
        )
    parts.append("".join(current))
    return [p.strip().strip("'") for p in parts if p.strip()]


class PostgresClient:
    """A thin, RLS-aware data client with the same shape as the old PostgREST client."""

    def __init__(
        self,
        dsn: str,
        *,
        role: str = "authenticated",
        claims: Mapping[str, Any] | None = None,
        search_path: str = "public",
    ) -> None:
        if role not in {"authenticated", "anon"}:
            raise DatabaseError(
                f"role {role!r} is not an application role; only 'authenticated' and 'anon' may "
                "be used by the application"
            )
        self.dsn = dsn
        self.role = role
        self.claims = dict(claims or {})
        self.search_path = search_path
        self._columns: dict[str, frozenset[str]] = {}
        self._column_types: dict[str, dict[str, str]] = {}
        self._connection: psycopg.Connection | None = None

    # -- connection --------------------------------------------------------
    @property
    def connection(self) -> psycopg.Connection:
        if self._connection is None or self._connection.closed:
            self._connection = psycopg.connect(self.dsn, autocommit=True)
        return self._connection

    def close(self) -> None:
        if self._connection is not None and not self._connection.closed:
            self._connection.close()
        self._connection = None

    def __enter__(self) -> "PostgresClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- identity ----------------------------------------------------------
    def as_identity(self, *, role: str, claims: Mapping[str, Any]) -> "PostgresClient":
        """A second client view with different claims, sharing nothing mutable."""
        return PostgresClient(
            self.dsn, role=role, claims=claims, search_path=self.search_path
        )

    def _begin(self, cur: psycopg.Cursor) -> None:
        # SET LOCAL is reverted at the end of the transaction, so a connection can never leak
        # an identity into the next call.
        cur.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
        cur.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.search_path)))
        cur.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (_claims_json(self.claims),),
        )

    # -- schema introspection ---------------------------------------------
    def columns(self, table: str) -> frozenset[str]:
        _ident(table, "table")
        cached = self._columns.get(table)
        if cached is not None:
            return cached
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    "SELECT column_name, data_type, udt_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s",
                    (self.search_path, table),
                )
                rows = cur.fetchall()
                names = frozenset(row[0] for row in rows)
                self._column_types[table] = {row[0]: (row[2] or row[1]) for row in rows}
        if not names:
            raise DatabaseError(f"table {table!r} does not exist in schema {self.search_path!r}")
        self._columns[table] = names
        return names

    def _select_list(self, table: str, selection: str | None) -> str:
        if not selection or not selection.strip():
            return "*"
        if selection.strip() == "*":
            return "*"
        known = self.columns(table)
        columns = [_ident(part.strip(), "column") for part in selection.split(",") if part.strip()]
        unknown = [c for c in columns if c not in known]
        if unknown:
            raise DatabaseError(f"table {table!r} has no column(s) {unknown}")
        return ", ".join(columns)

    # -- filters -----------------------------------------------------------
    def _where(self, params: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for key, raw in params.items():
            if key in RESERVED_PARAMS:
                continue
            column = _ident(key, "column")
            if not isinstance(raw, str):
                raise DatabaseError(f"filter {key!r} must be a string like 'eq.value'")
            if raw == "is.null":
                clauses.append(f"{column} IS NULL")
                continue
            if raw == "not.is.null":
                clauses.append(f"{column} IS NOT NULL")
                continue
            if raw.startswith("in."):
                inner = raw[3:].strip()
                if not (inner.startswith("(") and inner.endswith(")")):
                    raise DatabaseError(f"filter {key}={raw!r} must look like 'in.(a,b)'")
                items = _split_top_level(inner[1:-1])
                if not items:
                    raise DatabaseError(f"filter {key!r} has an empty in.() list")
                clauses.append(f"{column} = ANY(%s)")
                values.append(items)
                continue
            for operator, comparator in (("eq.", "="), ("neq.", "<>"), ("gte.", ">="), ("lte.", "<="), ("gt.", ">"), ("lt.", "<")):
                if raw.startswith(operator):
                    clauses.append(f"{column} {comparator} %s")
                    values.append(raw[len(operator):])
                    break
            else:
                raise DatabaseError(
                    f"unsupported filter {key}={raw!r}; known operators: {', '.join(FILTER_OPERATORS)}. "
                    "Refusing to ignore a filter: a dropped WHERE clause is a security bug."
                )
        return clauses, values

    def _order(self, table: str, ordering: str | None) -> str:
        if not ordering:
            return ""
        parts: list[str] = []
        for item in ordering.split(","):
            bits = item.strip().split(".")
            column = _ident(bits[0], "column")
            if column not in self.columns(table):
                raise DatabaseError(f"table {table!r} has no column {column!r}")
            direction = bits[1].upper() if len(bits) > 1 else "ASC"
            if direction not in {"ASC", "DESC"}:
                raise DatabaseError(f"order direction {direction!r} must be ASC or DESC")
            parts.append(f"{column} {direction}")
        return " ORDER BY " + ", ".join(parts)

    def _paging(self, params: Mapping[str, Any]) -> tuple[str, list[Any]]:
        fragment = ""
        values: list[Any] = []
        if params.get("limit") is not None:
            fragment += " LIMIT %s"
            values.append(int(params["limit"]))
        if params.get("offset") is not None:
            fragment += " OFFSET %s"
            values.append(int(params["offset"]))
        return fragment, values

    # -- operations --------------------------------------------------------
    def rpc(
        self, function: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Call a database function by name, with named arguments.

        Why this exists at all: a few transitions are only legal *inside* a function — a manual
        Gmail send must set five columns, satisfy a trigger that no client may satisfy directly,
        and write an activity row in the same transaction. Expressing that as a table write would
        hand every caller the ability to fake it, so the write lives in SQL and the client only
        invokes it.

        Names are validated and every value is bound, as everywhere else here; ``SELECT * FROM``
        rather than ``(f(...)).*`` is deliberate, because expanding a volatile function in a target
        list evaluates it once per output column — which would record a manual send twice.
        """
        _ident(function, "function")
        args = dict(params or {})
        for key in args:
            _ident(key, "argument")
        names = list(args)
        if names:
            call = f"{function}(" + ", ".join(f"{name} => %s" for name in names) + ")"
        else:
            call = f"{function}()"
        return self._run(f"SELECT * FROM {call}", [args[name] for name in names])

    def get(self, table: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        selection = self._select_list(table, params.get("select"))
        clauses, where_values = self._where(params)
        ordering = self._order(table, params.get("order"))
        paging, paging_values = self._paging(params)

        query = f"SELECT {selection} FROM {_ident(table, 'table')}"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += ordering + paging

        return self._run(query, [*where_values, *paging_values])

    def post(self, table: str, data: Mapping[str, Any] | Sequence[Mapping[str, Any]], prefer: str = "") -> list[dict[str, Any]]:
        rows = [data] if isinstance(data, Mapping) else list(data)
        if not rows:
            return []
        known = self.columns(table)
        keys = sorted({key for row in rows for key in row})
        unknown = [key for key in keys if key not in known]
        if unknown:
            raise DatabaseError(f"table {table!r} has no column(s) {unknown}")

        placeholders = ", ".join("%s" for _ in keys)
        types = self._column_types.get(table, {})
        values: list[Any] = []
        for row in rows:
            missing = [key for key in keys if key not in row]
            if missing:
                # A multi-row INSERT with differing keys would silently drop columns.
                raise DatabaseError(
                    f"row in {table!r} is missing {missing}; every row must carry the same columns"
                )
            values.extend(_bind(row[key], types.get(key)) for key in keys)

        query = (
            f"INSERT INTO {_ident(table, 'table')} ({', '.join(keys)}) "
            f"VALUES {', '.join('(' + placeholders + ')' for _ in rows)} RETURNING *"
        )
        return self._run(query, values)

    def patch(self, table: str, data: Mapping[str, Any], params: Mapping[str, Any] | None = None, prefer: str = "", *, allow_all: bool = False) -> list[dict[str, Any]]:
        if not data:
            raise DatabaseError("patch requires at least one column")
        _require_filter(table, params, "patch", allow_all)
        known = self.columns(table)
        unknown = [key for key in data if key not in known]
        if unknown:
            raise DatabaseError(f"table {table!r} has no column(s) {unknown}")

        assignments = ", ".join(f"{key} = %s" for key in data)
        clauses, where_values = self._where(dict(params or {}))
        query = f"UPDATE {_ident(table, 'table')} SET {assignments}"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " RETURNING *"
        types = self._column_types.get(table, {})
        return self._run(
            query,
            [*[_bind(v, types.get(key)) for key, v in data.items()], *where_values],
        )

    def delete(self, table: str, params: Mapping[str, Any] | None = None, *, allow_all: bool = False) -> int:
        _require_filter(table, params, "delete", allow_all)
        clauses, values = self._where(dict(params or {}))
        query = f"DELETE FROM {_ident(table, 'table')}"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                self._begin(cur)
                cur.execute(query, values)
                return cur.rowcount

    # -- execution ---------------------------------------------------------
    def _run(self, query: str, values: Iterable[Any]) -> list[dict[str, Any]]:
        try:
            with self.connection.transaction():
                with self.connection.cursor() as cur:
                    self._begin(cur)
                    cur.execute(query, list(values))
                    if cur.description is None:
                        return []
                    names = [column.name for column in cur.description]
                    return [dict(zip(names, row)) for row in cur.fetchall()]
        except psycopg.errors.InsufficientPrivilege as exc:
            raise DatabaseError(str(exc), code=exc.sqlstate, status_code=403) from exc
        except psycopg.errors.Error as exc:
            raise DatabaseError(str(exc), code=getattr(exc, "sqlstate", None), status_code=400) from exc


def _claims_json(claims: Mapping[str, Any]) -> str:
    return json.dumps(dict(claims), default=str)


def _bind(value: Any, column_type: str | None = None) -> Any:
    """Adapt a Python value to the column it is going into.

    The first version of this sent every list to ``Jsonb``, which is wrong for the platform's
    array columns — ``disqualification_reasons text[]`` and ``personalization_evidence_ids uuid[]``
    both failed with 42804 the first time a real scorecard was persisted. A list therefore becomes
    ``Jsonb`` only when the column is json/jsonb; otherwise psycopg adapts it to a native array.
    """
    if isinstance(value, dict):
        return Jsonb(value)
    if isinstance(value, list):
        if column_type in {"json", "jsonb"}:
            return Jsonb(value)
        return value
    return value
