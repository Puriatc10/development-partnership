"""Guards on the fixture script, which deletes rows.

`--remove` has to prove both the candidate and the company it would delete are synthetic before
touching either, and has to confirm the deletion actually happened: a `DELETE` that matches no
rows returns success, and a partial removal is unrecoverable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, candidates, companies=None):
        self.candidates = candidates
        self.companies = companies or []
        self.deleted: list[tuple[str, str]] = []

    def get(self, table, params=None):
        rows = {"discovery_candidates": self.candidates, "companies": self.companies}[table]
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


def _fixture():
    return _load("gate1_fixture_under_test", "scripts/gate1_fixture.py")


def test_fixture_removal_refuses_a_real_candidate():
    module = _fixture()
    client = FakeClient(
        candidates=[{"id": "c1", "domain": "refokus.com", "source_name": "directory", "converted_company_id": None}]
    )
    with pytest.raises(SystemExit) as exc:
        module.remove(SimpleNamespace(client=client), "c1")
    assert "not a synthetic fixture" in str(exc.value)
    assert client.deleted == []


def test_fixture_removal_refuses_a_company_it_did_not_create():
    module = _fixture()
    client = FakeClient(
        candidates=[{"id": "c1", "domain": "x.example.com", "source_name": "synthetic_fixture",
                     "converted_company_id": "company-real"}],
        companies=[{"id": "company-real", "name": "Real Co", "discovery_source": "operator_registration",
                    "discovery_details": {}}],
    )
    with pytest.raises(SystemExit) as exc:
        module.remove(SimpleNamespace(client=client), "c1")
    assert "was not created from this candidate" in str(exc.value)
    assert client.deleted == []


def test_fixture_removal_deletes_the_company_first_and_confirms_absence():
    module = _fixture()
    client = FakeClient(
        candidates=[{"id": "c1", "domain": "x.example.com", "source_name": "synthetic_fixture",
                     "converted_company_id": "company-1"}],
        companies=[{"id": "company-1", "name": "[SYNTHETIC] X", "discovery_source": "discovery_candidate",
                    "discovery_details": {"candidate_id": "c1"}}],
    )
    result = module.remove(SimpleNamespace(client=client), "c1")
    assert client.deleted == [("companies", "company-1"), ("discovery_candidates", "c1")]
    assert result["verified_absent"] is True


def test_fixtures_do_not_impersonate_the_discovery_agent():
    module = _fixture()
    assert module.SYNTHETIC_IDENTITY != "hermes-discovery-v1"
    assert module.SYNTHETIC_IDENTITY.startswith("synthetic")
