"""Compatibility shim — auth now lives in ``integrations.supabase.auth`` (ADR-0002)."""

from integrations.supabase.auth import SupabaseAuthClient  # noqa: F401

__all__ = ["SupabaseAuthClient"]
