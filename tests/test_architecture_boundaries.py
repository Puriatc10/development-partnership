"""Architecture boundary tests (ADR-0001).

The layer rules are only real if something fails when they are broken. These
tests read the source of every module in the inner layers and assert that
forbidden imports do not exist.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

FORBIDDEN = {
    # layer -> modules it must never import
    "contracts": {"agents", "orchestration", "integrations", "intelligence", "requests"},
    "integrations": {"agents", "orchestration", "intelligence"},
    "intelligence": {"agents", "orchestration"},
    # ADR-0001: `agents/*` may import contracts, integrations and intelligence —
    # never orchestration, which is what drives agents, not the other way round.
    # psycopg is banned because `integrations.postgres` is the only sanctioned
    # database path: it is where SET LOCAL ROLE and the claims live, so a direct
    # driver import anywhere else silently bypasses Row Level Security.
    "agents": {"orchestration", "psycopg"},
    # ADR-0001: `orchestration/*` may import contracts (and agent entry points).
    # It composes workers; it does not reach into adapters or scoring itself, and
    # it receives its TaskSource client by injection rather than importing one.
    "orchestration": {"integrations", "intelligence", "psycopg"},
}


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import stays inside the layer
                continue
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def python_files(layer: str) -> list[Path]:
    root = REPO / layer
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_inner_layer_does_not_import_outer_layer(layer: str):
    banned = FORBIDDEN[layer]
    violations = []
    for path in python_files(layer):
        bad = module_imports(path) & banned
        if bad:
            violations.append(f"{path.relative_to(REPO)} imports {sorted(bad)}")
    assert not violations, "Layering violated:\n" + "\n".join(violations)


def test_the_exported_runtime_layers_are_covered():
    """`agents/` and `orchestration/` used to be exempt; keep them in the rule set."""
    missing = {"agents", "orchestration"} - set(FORBIDDEN)
    assert not missing, f"layers with no import rule: {sorted(missing)}"


def test_every_ruled_layer_actually_contains_python():
    """A rule for a directory with no modules under it passes without checking anything."""
    empty = [layer for layer in FORBIDDEN if not python_files(layer)]
    assert not empty, f"import rules are vacuous for {sorted(empty)}"


def test_module_imports_reads_what_it_claims_to(tmp_path):
    """Guard the reader itself: if it stopped seeing imports, every rule would pass."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import orchestration\nfrom integrations.postgres import client\nfrom . import relative\n",
        encoding="utf-8",
    )
    assert module_imports(sample) == {"orchestration", "integrations"}


def test_contracts_has_no_third_party_imports():
    third_party = set()
    stdlib_ok = {
        "__future__", "abc", "dataclasses", "datetime", "enum", "functools", "hashlib",
        "json", "logging", "os", "pathlib", "re", "typing", "urllib", "collections",
    }
    for path in python_files("contracts"):
        for name in module_imports(path):
            if name in stdlib_ok or name in {"contracts"}:
                continue
            third_party.add(name)
    assert not third_party, f"contracts must stay dependency-free; found {sorted(third_party)}"


def test_adapters_never_write_to_the_crm():
    """A provider adapter must not contain insert/update calls against the CRM."""
    offenders = []
    for path in python_files("integrations"):
        # The data-access layer itself is the sanctioned writer; adapters must not be.
        if path.parts[-2] in {"postgres", "supabase"}:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in (".post(\"companies\"", ".post('companies'", "insert into companies"):
            if needle in text:
                offenders.append(f"{path.relative_to(REPO)}: {needle}")
    assert not offenders, offenders
