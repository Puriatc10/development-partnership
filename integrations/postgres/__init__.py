"""PostgreSQL access layer: the platform's own database client.

Replaces ``integrations.supabase`` (PostgREST over HTTP + GoTrue). The interface is kept
deliberately identical so business logic did not have to change, while the identity boundary
became explicit: one transaction per call, ``SET LOCAL ROLE``, claims in
``request.jwt.claims``.

See ``docs/MIGRATION_TO_SELF_HOSTED_POSTGRES.md``.
"""

from __future__ import annotations

from .client import DatabaseError, PostgresClient, PostgrestError

__all__ = ["DatabaseError", "PostgresClient", "PostgrestError"]
