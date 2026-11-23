-- =============================================================================
-- Eryndex — 0000 foundation
--
-- Everything the CRM schema and its policies assume about Supabase, provided locally:
--
--   * the three roles the grants are written against (anon, authenticated, migrator);
--   * an `auth` schema with `auth.jwt()` and `auth.uid()`, the only two helper functions
--     the 16 tables' RLS policies call (80 and 23 times respectively);
--   * `auth.users`, the identity table that replaces GoTrue's;
--   * the migration ledger that replaces `supabase_migrations.schema_migrations`.
--
-- Nothing here is Supabase-specific in behaviour: the policies read the same claim shape
-- they always did, from `request.jwt.claims`, which the application sets per transaction.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Roles
--
--      NOLOGIN + NOINHERIT, exactly like Supabase's: the application connects as
--      eryndex_owner and drops into one of these per transaction with SET LOCAL ROLE, so
--      a connection can never drift between identity classes.
-- -----------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') THEN
        CREATE ROLE migrator NOLOGIN NOINHERIT;
    END IF;
END
$$;

COMMENT ON ROLE anon IS 'Unprivileged requests. Holds no table privileges; policies deny everything.';
COMMENT ON ROLE authenticated IS 'Signed-in humans and agents. Every table privilege is gated by RLS.';
COMMENT ON ROLE migrator IS 'Schema changes only. Never used by the application.';

-- -----------------------------------------------------------------------------
-- 2. The auth schema
-- -----------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    app_metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_sign_in_at TIMESTAMPTZ,

    CONSTRAINT chk_auth_users_email CHECK (position('@' IN email) > 1)
);

COMMENT ON TABLE auth.users IS
    'Local identity table. app_metadata carries user_role / subagent_type / agent_id, the same '
    'claims the RLS policies and the human-gate model read from the token.';

-- -----------------------------------------------------------------------------
-- 3. The two helper functions the policies call
--
--      `request.jwt.claims` is set per transaction by the application adapter. When it is
--      absent the functions return NULL/empty rather than raising, so an unauthenticated
--      connection resolves to "no identity" and every policy denies — the fail-closed
--      direction.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION auth.jwt()
RETURNS JSONB
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(NULLIF(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb)
$$;

COMMENT ON FUNCTION auth.jwt() IS 'Claims for the current transaction; empty object when unset.';

CREATE OR REPLACE FUNCTION auth.uid()
RETURNS UUID
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
        WHEN (auth.jwt() ->> 'sub') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        THEN (auth.jwt() ->> 'sub')::uuid
    END
$$;

COMMENT ON FUNCTION auth.uid() IS
    'The signed-in subject as a UUID. Returns NULL for a non-UUID subject instead of raising: '
    'a malformed claim must read as "no identity", not as a 500 in the middle of a policy.';

CREATE OR REPLACE FUNCTION auth.role() RETURNS TEXT
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(auth.jwt() ->> 'role', 'anon')
$$;

COMMENT ON FUNCTION auth.role() IS 'PostgreSQL role named in the claims; kept for parity with Supabase.';

REVOKE ALL ON SCHEMA auth FROM PUBLIC;
GRANT USAGE ON SCHEMA auth TO anon, authenticated;
GRANT EXECUTE ON FUNCTION auth.jwt(), auth.uid(), auth.role() TO anon, authenticated;

-- The application never reads auth.users through the API: identities are provisioned by an
-- admin script and signed in by the auth module, both as the owner.
REVOKE ALL ON auth.users FROM anon, authenticated;

-- -----------------------------------------------------------------------------
-- 4. Migration ledger
--      Replaces supabase_migrations.schema_migrations. sha256 makes drift detectable: a file
--      whose content changed after it was applied is refused rather than silently re-run.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    applied_by TEXT NOT NULL DEFAULT current_user
);

COMMENT ON TABLE public.schema_migrations IS
    'Applied migration files, by 14-digit version, with the checksum that was executed.';

REVOKE ALL ON public.schema_migrations FROM anon, authenticated;

COMMIT;
