"""Supabase persistence adapter (layer D).

Auth and PostgREST access live here so every agent uses one client and one set
of retry/timeout rules (ADR-0002). These modules are the *only* place allowed to
write to canonical CRM tables, and every write happens under an authenticated
scoped JWT — never ``service_role``.
"""

from .auth import SupabaseAuthClient
from .client import PostgrestClient, PostgrestError

__all__ = ["PostgrestClient", "PostgrestError", "SupabaseAuthClient"]
