"""Environment guard: the platform can only talk to a database it is allowed to touch.

The Supabase version pinned a project reference. With a self-hosted database there is no
reference — so the rule is expressed as a host policy: local by default, remote only when
production is named *and* the host is explicitly allow-listed. Production can never be reached by
omission, which is the property these tests exist to keep.
"""

from __future__ import annotations

import pytest

from contracts.environment import (
    LOCAL_HOSTS,
    DatabaseConfig,
    assert_database_environment,
)
from contracts.errors import EnvironmentGuardError

LOCAL_DSN = "postgresql://eryndex_owner:secret@127.0.0.1:5433/eryndex"


def config(**over) -> DatabaseConfig:
    base = dict(dsn=LOCAL_DSN, env="development", jwt_secret="x")
    base.update(over)
    return DatabaseConfig(**base)


def test_a_local_development_database_passes():
    assert_database_environment(config())


def test_every_local_host_is_accepted():
    for host in ("127.0.0.1", "localhost", "postgres", "db"):
        assert host in LOCAL_HOSTS
        assert_database_environment(config(dsn=f"postgresql://u:p@{host}:5432/eryndex"))


def test_a_remote_host_is_refused_in_development():
    with pytest.raises(EnvironmentGuardError) as exc:
        assert_database_environment(config(dsn="postgresql://u:p@db.example.com:5432/eryndex"))
    assert "not local" in str(exc.value)


def test_production_requires_the_host_to_be_named_explicitly():
    remote = "postgresql://u:p@db.example.com:5432/eryndex"
    with pytest.raises(EnvironmentGuardError) as exc:
        assert_database_environment(config(dsn=remote, env="production"))
    assert "allow-list" in str(exc.value)

    assert_database_environment(config(dsn=remote, env="production"), allowed_hosts=["db.example.com"])


def test_an_unlisted_production_host_is_still_refused():
    with pytest.raises(EnvironmentGuardError):
        assert_database_environment(
            config(dsn="postgresql://u:p@other.example.com:5432/eryndex", env="production"),
            allowed_hosts=["db.example.com"],
        )


def test_an_empty_or_malformed_connection_string_is_refused():
    for dsn in ("", "   ", "mysql://u:p@localhost/db", "postgresql://127.0.0.1:5432/eryndex", "postgresql://u:p@localhost:5432/"):
        with pytest.raises(EnvironmentGuardError):
            assert_database_environment(config(dsn=dsn))


def test_an_unknown_environment_name_is_refused():
    with pytest.raises(EnvironmentGuardError) as exc:
        assert_database_environment(config(env="staging"))
    assert "ERYNDEX_ENV" in str(exc.value)


def test_a_password_in_the_connection_string_is_never_echoed():
    dsn = "postgresql://u:hunter2-secret@db.example.com:5432/eryndex"
    with pytest.raises(EnvironmentGuardError) as exc:
        assert_database_environment(config(dsn=dsn))
    assert "hunter2-secret" not in str(exc.value)


def test_config_loads_from_an_explicit_mapping_only():
    loaded = DatabaseConfig.load({"ERYNDEX_DB_URL": LOCAL_DSN, "ERYNDEX_ENV": "development", "ERYNDEX_JWT_SECRET": "s"})
    assert loaded.dsn == LOCAL_DSN and loaded.env == "development" and loaded.jwt_secret == "s"
    assert loaded.host == "127.0.0.1" and loaded.database == "eryndex"


def test_an_operator_session_refuses_a_remote_database_before_connecting():
    """The guard must run before any credential is offered to a host we do not trust."""
    from operations.session import open_session

    with pytest.raises(EnvironmentGuardError) as exc:
        open_session(
            {
                "ERYNDEX_DB_URL": "postgresql://u:secret@db.example.com:5432/eryndex",
                "ERYNDEX_ENV": "development",
                "ERYNDEX_JWT_SECRET": "x",
                "OPERATOR_EMAIL": "operator@partnership.internal",
                "OPERATOR_PASSWORD": "not-a-real-password",
            }
        )
    assert "not local" in str(exc.value)
    assert "not-a-real-password" not in str(exc.value)
    assert "secret" not in str(exc.value).replace("secret@", "")


def test_the_guard_accepts_no_configuration_object():
    with pytest.raises(EnvironmentGuardError):
        assert_database_environment(None)  # type: ignore[arg-type]
