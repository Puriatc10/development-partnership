# ADR-0002 — Extract Supabase infrastructure out of `agents/researcher/`

**Status:** Accepted
**Date:** 2026-11-13
**Context refs:** baseline finding B-04, target architecture §4

## Context

`SupabaseAuthClient` (`agents/researcher/auth.py`), `PostgrestClient` (`agents/researcher/client.py`), the `.env` loader and `Config` (`agents/researcher/config.py`), and the fail-closed Development guard (`agents/researcher/runner.py:validate_development_environment`) are generic platform infrastructure living inside one agent's package. Every new agent would import `agents.researcher.client` — an agent-to-agent dependency the architecture forbids — or copy the code.

## Decision

Move the generic pieces to `contracts/` and `integrations/supabase/`:

| Current | New location | Compatibility |
|---|---|---|
| `config.load_dotenv`, `Config` | `contracts/environment.py` | `agents.researcher.config` re-exports both names |
| `validate_development_environment`, `normalize_supabase_url` | `contracts/environment.py` | `agents.researcher.runner` re-exports both names |
| `SupabaseAuthClient` | `integrations/supabase/auth.py` | `agents.researcher.auth` re-exports |
| `PostgrestClient`, `PostgrestError` | `integrations/supabase/client.py` | `agents.researcher.client` re-exports |

Re-export shims keep every existing import, CLI invocation and test working; the 20-test suite must stay green with no edits to the assertions.

## Consequences

**Positive:** one environment guard and one HTTP client for all agents; new agents import infrastructure, not each other; the guard can be tightened in a single place.

**Negative:** two names for the same class during the migration window. Accepted while the shims live; each shim carries a docstring naming its removal condition.

**Verification:** `python -m pytest` stays at 20+ passing; `python -m agents.researcher.runner --help` still resolves.
