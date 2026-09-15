from __future__ import annotations

import ast
from pathlib import Path


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_generation_and_storage_keep_quantlib_duckdb_boundary(
    repository_root: Path,
) -> None:
    package = repository_root / "src/synthetic_derivatives/authoring"
    generation = {
        "generator_common.py",
        "underlying_path.py",
        "underlying_observations.py",
        "pricing_metadata.py",
        "underlying_daily_generator.py",
        "option_daily_generator.py",
    }
    storage = {
        "schema.py",
        "schema_ddl.py",
        "schema_migrations.py",
        "table_specs.py",
        "persistence.py",
        "snapshot_store.py",
        "visibility.py",
    }
    assert all("duckdb" not in _imported_roots(package / name) for name in generation)
    assert all("QuantLib" not in _imported_roots(package / name) for name in storage)


def test_authoring_never_imports_solver_or_verifier_implementations(
    repository_root: Path,
) -> None:
    package = repository_root / "src/synthetic_derivatives/authoring"
    forbidden = {
        "synthetic_derivatives.solver",
        "synthetic_derivatives.verifier",
    }
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert forbidden.isdisjoint(imported), path.name
