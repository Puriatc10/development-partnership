"""Environment guard tests: Production can never be reached by platform code."""

from __future__ import annotations

import pytest

from contracts.environment import (
    APPROVED_DEV_PROJECT_REF,
    APPROVED_DEV_SUPABASE_URL,
    PRODUCTION_PROJECT_REF,
    EnvironmentConfig,
    assert_development_environment,
    normalize_supabase_url,
)


def config(**over):
    base = dict(
        project_ref=APPROVED_DEV_PROJECT_REF,
        supabase_url=APPROVED_DEV_SUPABASE_URL,
        anon_key="x",
    )
    base.update(over)
    return EnvironmentConfig(**base)


def test_development_configuration_passes():
    assert_development_environment(config())


def test_production_ref_is_rejected_even_if_allow_listed():
    with pytest.raises(Exception) as exc:
        assert_development_environment(
            config(
                project_ref=PRODUCTION_PROJECT_REF,
                supabase_url=f"https://{PRODUCTION_PROJECT_REF}.supabase.co",
            ),
            allowed_refs=(APPROVED_DEV_PROJECT_REF, PRODUCTION_PROJECT_REF),
        )
    assert "Production" in str(exc.value)


def test_unknown_ref_is_rejected():
    with pytest.raises(Exception):
        assert_development_environment(
            config(project_ref="abcdefghijklmnopqrst", supabase_url="https://abcdefghijklmnopqrst.supabase.co")
        )


def test_mismatched_url_is_rejected():
    with pytest.raises(Exception):
        assert_development_environment(config(supabase_url="https://evil.example.com"))


def test_empty_project_ref_is_rejected():
    with pytest.raises(Exception):
        assert_development_environment(config(project_ref=""))


def test_url_with_credentials_is_rejected_without_leaking_them():
    with pytest.raises(Exception) as exc:
        assert_development_environment(
            config(supabase_url="https://user:secret@vyfqmrdznmocnmzyjjcj.supabase.co")
        )
    assert "secret" not in str(exc.value)


def test_url_normalization_tolerates_trailing_slash_and_case():
    assert normalize_supabase_url("HTTPS://VYFQMRDZNMOCNMZYJJCJ.SUPABASE.CO/") == APPROVED_DEV_SUPABASE_URL


def test_missing_configuration_object_is_rejected():
    with pytest.raises(Exception):
        assert_development_environment(None)  # type: ignore[arg-type]
