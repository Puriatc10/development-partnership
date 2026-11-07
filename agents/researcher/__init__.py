"""
Researcher Agent Vertical Slice Package.

Implements the first operational integration slice for Phase 3.0:
- Scoped Supabase Auth & JWT token acquisition
- PostgREST communication under PostgreSQL Row Level Security (RLS)
- Evidence-based company investigation adhering to Agent Contract 3 & Workflow 2
- Structured persistence of research reports, findings, and task completion
"""

from .agent import ResearcherAgent
from .auth import SupabaseAuthClient
from .client import PostgrestClient

__all__ = ["ResearcherAgent", "SupabaseAuthClient", "PostgrestClient"]
