"""Guards on the two operator scripts, which the audit found were the weakest links.

`rls_probe.py` runs privileged SQL through the Supabase CLI, whose target is whatever
`supabase link` last wrote — so it has to refuse before executing anything, not inspect the
output afterwards. `gate1_fixture.py --remove` deletes rows, so it has to prove both the
candidate and the company it would delete are synthetic.

Neither test touches a database: the probes here are about the guard's decision.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
DEV_REF = "vyfqmrdznmocnmzyjjcj"
PROD_REF = "vqnamszywsuqjxuqseas"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses resolve their module through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    module = _load("rls_probe_under_test", "scripts/rls_probe.py")
    monkeypatch.setattr(module, "REPO", tmp_path)
    (tmp_path / "supabase" / ".temp").mkdir(parents=True)
    return module


def _link(module, ref: str, tmp_path: Path) -> None:
    (tmp_path / "supabase" / ".temp" / "project-ref").write_text(ref + "\n", encoding="utf-8")


def test_probe_accepts_the_development_project(probe, tmp_path, monkeypatch):
    _link(probe, DEV_REF, tmp_path)
    monkeypatch.delenv("SUPABASE_PROJECT_REF", raising=False)
    assert probe.assert_development_target() == DEV_REF


def test_probe_refuses_production_before_running_anything(probe, tmp_path, monkeypatch):
    _link(probe, PROD_REF, tmp_path)
    monkeypatch.delenv("SUPABASE_PROJECT_REF", raising=False)
    with pytest.raises(SystemExit) as exc:
        probe.assert_development_target()
    assert "Production" in str(exc.value)


def test_probe_refuses_an_unknown_project(probe, tmp_path, monkeypatch):
    _link(probe, "abcdefghijklmnopqrst", tmp_path)
    monkeypatch.delenv("SUPABASE_PROJECT_REF", raising=False)
    with pytest.raises(SystemExit):
        probe.assert_development_target()


def test_probe_refuses_when_the_environment_disagrees_with_the_link(probe, tmp_path, monkeypatch):
    _link(probe, DEV_REF, tmp_path)
    monkeypatch.setenv("SUPABASE_PROJECT_REF", PROD_REF)
    with pytest.raises(SystemExit) as exc:
        probe.assert_development_target()
    assert "disagree" in str(exc.value)


def test_probe_refuses_when_nothing_is_linked(probe, monkeypatch):
    monkeypatch.delenv("SUPABASE_PROJECT_REF", raising=False)
    with pytest.raises(SystemExit) as exc:
        probe.assert_development_target()
    assert "no linked project" in str(exc.value)


def test_probe_allow_cases_assert_a_value_not_just_exit_code(probe):
    """An 'allow' case that only checks the exit code cannot fail for the right reason."""
    expectations = {case.name: case.expect for case in probe.CASES}
    gate = next(value for name, value in expectations.items() if name.startswith("G operator"))
    control = next(value for name, value in expectations.items() if name.startswith("K operator"))
    assert gate.startswith("allow:") and "approved" in gate
    assert control.startswith("allow:") and "visible" in control


class FakeClient:
    def __init__(self, candidates, companies=None, domains=None):
        self.candidates = candidates
        self.companies = companies or []
        self.domains = domains or []
        self.deleted: list[tuple[str, str]] = []

    def get(self, table, params=None):
        rows = {
            "discovery_candidates": self.candidates,
            "companies": self.companies,
            "company_domains": self.domains,
        }[table]
        wanted = (params or {}).get("id")
        if wanted:
            return [r for r in rows if f"eq.{r['id']}" == wanted]
        return rows

    def delete(self, table, params=None):
        wanted = (params or {}).get("id", "").replace("eq.", "")
        self.deleted.append((table, wanted))
        if table == "discovery_candidates":
            self.candidates = [c for c in self.candidates if c["id"] != wanted]
        if table == "companies":
            self.companies = [c for c in self.companies if c["id"] != wanted]


def _fixture(monkeypatch):
    module = _load("gate1_fixture_under_test", "scripts/gate1_fixture.py")
    return module


def test_fixture_removal_refuses_a_real_candidate(monkeypatch):
    module = _fixture(monkeypatch)
    client = FakeClient(
        candidates=[{"id": "c1", "domain": "refokus.com", "source_name": "directory", "converted_company_id": None}]
    )
    session = SimpleNamespace(client=client)
    with pytest.raises(SystemExit) as exc:
        module.remove(session, "c1")
    assert "not a synthetic fixture" in str(exc.value)
    assert client.deleted == []


def test_fixture_removal_refuses_a_company_it_did_not_create(monkeypatch):
    module = _fixture(monkeypatch)
    client = FakeClient(
        candidates=[{"id": "c1", "domain": "x.example.com", "source_name": "synthetic_fixture",
                     "converted_company_id": "company-real"}],
        companies=[{"id": "company-real", "name": "Real Co", "discovery_source": "operator_registration",
                    "discovery_details": {}}],
    )
    session = SimpleNamespace(client=client)
    with pytest.raises(SystemExit) as exc:
        module.remove(session, "c1")
    assert "was not created from this candidate" in str(exc.value)
    assert client.deleted == [], "nothing may be deleted when the provenance does not match"


def test_fixture_removal_deletes_the_company_first_and_confirms_absence(monkeypatch):
    module = _fixture(monkeypatch)
    client = FakeClient(
        candidates=[{"id": "c1", "domain": "x.example.com", "source_name": "synthetic_fixture",
                     "converted_company_id": "company-1"}],
        companies=[{"id": "company-1", "name": "[SYNTHETIC] X", "discovery_source": "discovery_candidate",
                    "discovery_details": {"candidate_id": "c1"}}],
    )
    session = SimpleNamespace(client=client)
    result = module.remove(session, "c1")
    assert client.deleted == [("companies", "company-1"), ("discovery_candidates", "c1")]
    assert result["verified_absent"] is True


def test_fixtures_do_not_impersonate_the_discovery_agent(monkeypatch):
    module = _fixture(monkeypatch)
    assert module.SYNTHETIC_IDENTITY != "hermes-discovery-v1"
    assert module.SYNTHETIC_IDENTITY.startswith("synthetic")
