"""Local authentication: password verification and token minting.

Replaces GoTrue. The shape of the result is deliberately the same as the Supabase client's
(`access_token`, `claims`, `user`) so callers do not change, and the claims are deliberately the
same (`sub`, `role`, `app_metadata.user_role/subagent_type/agent_id`) because that is what the
RLS policies and the human-gate model read.

What changes is who is authoritative: the database is ours, so the password hash lives in
`auth.users` (argon2id) and the token is signed here with a local secret. The database does not
verify tokens — the application does, and then hands the claims to PostgreSQL per transaction.
That trust boundary is stated in the migration plan; it holds because the application is the
only client of this database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import jwt
import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ALGORITHM = "HS256"
DEFAULT_TTL_MINUTES = 60
HASHER = PasswordHasher()


class AuthenticationError(PermissionError):
    """The credentials were refused. Deliberately says nothing about which part was wrong."""


@dataclass(frozen=True)
class SignedIn:
    access_token: str
    claims: Mapping[str, Any]
    user: Mapping[str, Any]

    @property
    def user_id(self) -> str:
        return str(self.claims.get("sub") or "")


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise AuthenticationError("a password must be at least 8 characters")
    return HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


class PostgresAuthClient:
    """Sign-in against ``auth.users``, minting HS256 tokens with the same claim shape."""

    def __init__(self, dsn: str, jwt_secret: str, *, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> None:
        if not jwt_secret:
            raise AuthenticationError(
                "ERYNDEX_JWT_SECRET is not configured; refusing to mint unsigned identities"
            )
        self.dsn = dsn
        self.jwt_secret = jwt_secret
        self.ttl_minutes = ttl_minutes

    # -- token ------------------------------------------------------------
    def mint_token(self, claims: Mapping[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            **dict(claims),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=self.ttl_minutes)).timestamp()),
        }
        return jwt.encode(payload, self.jwt_secret, algorithm=ALGORITHM)

    def verify_token(self, token: str) -> Mapping[str, Any]:
        try:
            return jwt.decode(token, self.jwt_secret, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("the session token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("the session token is not valid") from exc

    # -- sign-in ----------------------------------------------------------
    def sign_in_with_password(self, email: str, password: str) -> dict[str, Any]:
        record = self._fetch(email)
        if record is None or not record["is_active"] or not verify_password(record["password_hash"], password):
            # One message for both cases: "no such user" is an oracle.
            raise AuthenticationError("email or password is incorrect")

        user_id = str(record["id"])
        app_metadata = dict(record["app_metadata"] or {})
        claims = {
            "sub": user_id,
            "email": record["email"],
            "role": "authenticated",
            "app_metadata": app_metadata,
        }
        token = self.mint_token(claims)
        self._touch(user_id)
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": self.ttl_minutes * 60,
            "claims": claims,
            "user": {"id": user_id, "email": record["email"]},
        }

    # -- storage ----------------------------------------------------------
    def _fetch(self, email: str) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, email, password_hash, app_metadata, is_active "
                    "FROM auth.users WHERE lower(email) = lower(%s)",
                    (email,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "email": row[1],
            "password_hash": row[2],
            "app_metadata": row[3],
            "is_active": row[4],
        }

    def _touch(self, user_id: str) -> None:
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth.users SET last_sign_in_at = clock_timestamp() WHERE id = %s",
                    (user_id,),
                )
