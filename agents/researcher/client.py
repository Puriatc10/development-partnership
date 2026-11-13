"""Compatibility shim — the client now lives in ``integrations.supabase.client``.

Kept so existing imports (``agents.researcher.agent``, CLIs, tests) keep working
during the migration described in ADR-0002. Remove once every caller imports
from ``integrations.supabase`` directly.
"""

from integrations.supabase.client import PostgrestClient, PostgrestError  # noqa: F401

__all__ = ["PostgrestClient", "PostgrestError"]
