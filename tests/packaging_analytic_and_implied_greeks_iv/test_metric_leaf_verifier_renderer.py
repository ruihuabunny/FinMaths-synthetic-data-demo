from __future__ import annotations

import ast
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType
from typing import Iterator
from uuid import uuid4

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    DUCKDB_QUERY_V3_PROFILE,
    METRIC_LEAF_VERIFIER_FILENAMES,
    STATIC_V2_PROFILE,
    RuntimeProfile,
    bsm_metric_logical_checksum,
    digest_file,
    expected_market_metric_submission,
    load_bsm_market_metric_inputs,
    oracle_config_for_metric,
    render_metric_leaf_verifier_files,
    write_metric_leaf_verifier_bundle,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime.profiles import (
    METRIC_TARGET_ORDER,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_PACKAGE = (
    _REPOSITORY_ROOT
    / "src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv"
    / "metric_leaf_verifier_runtime"
)
_BASELINE = json.loads(
    (
        _REPOSITORY_ROOT
        / "tests/fixtures/bsm_metric_leaf_verifier_20260819_baseline.json"
    ).read_text(encoding="utf-8")
)
_PROFILES = {
    "static_v2": STATIC_V2_PROFILE,
    "duckdb_query_v3": DUCKDB_QUERY_V3_PROFILE,
}
_PROFILE_TARGETS = tuple(
    (profile_name, target)
    for profile_name in _PROFILES
    for target in METRIC_TARGET_ORDER
)
_PUBLIC_FACADE_SYMBOLS = (
    "BSMMarketMetricInput",
    "bsm_metric_logical_checksum",
    "digest_file",
    "expected_market_metric_submission",
    "load_bsm_market_metric_inputs",
    "validate_market_metric_submission_contract",
    "verify_market_metric_submission",
)
_IMPLEMENTATION_PATHS = (
    "runtime.py",
    "_runtime/__init__.py",
    "_runtime/profile.py",
    "_runtime/models.py",
    "_runtime/database.py",
    "_runtime/oracle.py",
    "_runtime/submission.py",
)


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _suite_root(profile_name: str) -> Path:
    return _REPOSITORY_ROOT / _BASELINE["deliveries"][profile_name]["relative_path"]


def _representative_leaf(profile_name: str, target: str) -> Path:
    suite_root = _suite_root(profile_name)
    manifest = json.loads(
        (suite_root / "suite_manifest.json").read_text(encoding="utf-8")
    )
    matches = [
        suite_root / assignment["relative_path"]
        for assignment in manifest["assignments"]
        if assignment["target"] == target
    ]
    assert len(matches) == 4
    return matches[0]


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for relative_path, payload in files.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


@contextmanager
def _loaded_runtime(verifier: Path) -> Iterator[ModuleType]:
    package_name = f"_phase3_metric_verifier_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        package_name,
        verifier / "__init__.py",
        submodule_search_locations=[str(verifier)],
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    try:
        spec.loader.exec_module(package)
        yield importlib.import_module(f"{package_name}.runtime")
    finally:
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)


def _normalized_inputs(inputs: tuple[object, ...]) -> tuple[dict[str, object], ...]:
    return tuple(dict(vars(item)) for item in inputs)


def _top_level_bound_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (node.name,)
    if isinstance(node, ast.Assign):
        return tuple(
            target.id for target in node.targets if isinstance(target, ast.Name)
        )
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (node.target.id,)
    return ()


@pytest.mark.parametrize("profile_name", tuple(_PROFILES))
def test_renderer_is_byte_deterministic_and_selects_one_protocol(
    profile_name: str,
) -> None:
    profile = _PROFILES[profile_name]
    rendered = {
        target: render_metric_leaf_verifier_files(profile, target)
        for target in METRIC_TARGET_ORDER
    }
    first = rendered[METRIC_TARGET_ORDER[0]]
    assert tuple(first) == METRIC_LEAF_VERIFIER_FILENAMES
    assert first == render_metric_leaf_verifier_files(profile.profile_id, "iv")

    for target, files in rendered.items():
        assert tuple(files) == METRIC_LEAF_VERIFIER_FILENAMES
        assert json.loads(files["oracle_config.json"]) == oracle_config_for_metric(
            profile, target
        )
        assert files == render_metric_leaf_verifier_files(profile, target)
        for name in files:
            if name != "oracle_config.json":
                assert files[name] == first[name]

    profile_source = first["_runtime/profile.py"].decode("utf-8")
    submission_source = first["_runtime/submission.py"].decode("utf-8")
    if profile_name == "static_v2":
        assert "STATIC_V2_PROFILE" in profile_source
        assert "DUCKDB_QUERY_V3_PROFILE" not in profile_source
        assert "STATIC_V2_PROFILE" in submission_source
        assert "DUCKDB_QUERY_V3_PROFILE" not in submission_source
    else:
        assert "DUCKDB_QUERY_V3_PROFILE" in profile_source
        assert "STATIC_V2_PROFILE" not in profile_source
        assert "DUCKDB_QUERY_V3_PROFILE" in submission_source
        assert "STATIC_V2_PROFILE" not in submission_source

    selected_submission = _SOURCE_PACKAGE / f"{profile.submission_module}.py"
    expected_sources = {
        "_runtime/models.py": _SOURCE_PACKAGE / "models.py",
        "_runtime/database.py": _SOURCE_PACKAGE / "database.py",
        "_runtime/oracle.py": _SOURCE_PACKAGE / "oracle.py",
        "_runtime/submission.py": selected_submission,
    }
    for rendered_name, source_path in expected_sources.items():
        expected = source_path.read_bytes().replace(
            b"from .profiles import", b"from .profile import", 1
        )
        assert first[rendered_name] == expected


def test_shared_rendered_modules_are_identical_across_protocols() -> None:
    static = render_metric_leaf_verifier_files(STATIC_V2_PROFILE, "delta")
    v3 = render_metric_leaf_verifier_files(DUCKDB_QUERY_V3_PROFILE, "delta")
    for name in (
        "runtime.py",
        "_runtime/__init__.py",
        "_runtime/models.py",
        "_runtime/database.py",
        "_runtime/oracle.py",
    ):
        assert static[name] == v3[name]
    assert static["_runtime/profile.py"] != v3["_runtime/profile.py"]
    assert static["_runtime/submission.py"] != v3["_runtime/submission.py"]


@pytest.mark.parametrize("profile_name", tuple(_PROFILES))
def test_every_rendered_python_module_is_structurally_safe(profile_name: str) -> None:
    files = render_metric_leaf_verifier_files(_PROFILES[profile_name], "iv")
    forbidden_calls = {"compile", "eval", "exec"}
    forbidden_text = {
        "reference/final_submission",
        "bsm_market_metric_verifier_runtime",
        "_V3_RUNTIME_SUFFIX",
    }
    local_roots = {
        "_runtime",
        "database",
        "models",
        "oracle",
        "profile",
        "submission",
        "verifier",
    }

    for name, payload in files.items():
        if not name.endswith(".py"):
            continue
        source = payload.decode("utf-8")
        tree = ast.parse(source, filename=name)
        assert "synthetic_derivatives" not in source, name
        assert str(_REPOSITORY_ROOT) not in source, name
        assert not any(text in source for text in forbidden_text), name
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden_calls
            for node in ast.walk(tree)
        ), name
        bound_names = [
            bound for node in tree.body for bound in _top_level_bound_names(node)
        ]
        assert len(bound_names) == len(set(bound_names)), name

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert roots.isdisjoint(local_roots), (name, roots)
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            root = (node.module or "").split(".", 1)[0]
            assert root not in local_roots, (name, root)

    assert len(files["runtime.py"].splitlines()) <= 150
    for name in _IMPLEMENTATION_PATHS[1:]:
        assert len(files[name].splitlines()) <= 350, name
    facade_tree = ast.parse(files["runtime.py"].decode("utf-8"))
    facade_all = next(
        node for node in facade_tree.body if isinstance(node, ast.Assign)
    )
    assert tuple(ast.literal_eval(facade_all.value)) == _PUBLIC_FACADE_SYMBOLS


def test_renderer_and_writer_fail_closed_before_replacing_any_file(
    tmp_path: Path,
) -> None:
    invalid = replace(STATIC_V2_PROFILE, task_version="wrong")
    destination = tmp_path / "invalid"
    with pytest.raises(ValueError, match="task version is inconsistent"):
        write_metric_leaf_verifier_bundle(destination, invalid, "delta")
    assert not destination.exists()

    with pytest.raises(ValueError, match="unsupported BSM metric target"):
        render_metric_leaf_verifier_files(STATIC_V2_PROFILE, "wrong")

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    sentinel = occupied / "sentinel.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    with pytest.raises(ValueError, match="destination must be absent or empty"):
        write_metric_leaf_verifier_bundle(occupied, STATIC_V2_PROFILE, "delta")
    assert sentinel.read_text(encoding="utf-8") == "keep\n"

    empty = tmp_path / "empty"
    empty.mkdir()
    expected = render_metric_leaf_verifier_files(STATIC_V2_PROFILE, "delta")
    write_metric_leaf_verifier_bundle(empty, STATIC_V2_PROFILE, "delta")
    actual_paths = {
        path.relative_to(empty).as_posix()
        for path in empty.rglob("*")
        if path.is_file()
    }
    assert actual_paths == set(METRIC_LEAF_VERIFIER_FILENAMES)
    assert all((empty / name).read_bytes() == payload for name, payload in expected.items())


@pytest.mark.parametrize(
    ("profile_name", "target"),
    _PROFILE_TARGETS,
    ids=[f"{profile}:{target}" for profile, target in _PROFILE_TARGETS],
)
def test_rendered_facades_match_all_metric_semantics(
    tmp_path: Path, profile_name: str, target: str
) -> None:
    profile = _PROFILES[profile_name]
    leaf = _representative_leaf(profile_name, target)
    database = tmp_path / "task.duckdb"
    shutil.copyfile(leaf / "task.duckdb", database)
    verifier = tmp_path / "verifier"
    write_metric_leaf_verifier_bundle(verifier, profile, target)
    config = json.loads((verifier / "oracle_config.json").read_text(encoding="utf-8"))
    source_inputs = load_bsm_market_metric_inputs(database, config)
    source_expected = expected_market_metric_submission(source_inputs, config)

    with _loaded_runtime(verifier) as runtime:
        assert tuple(runtime.__all__) == _PUBLIC_FACADE_SYMBOLS
        rendered_inputs = runtime.load_bsm_market_metric_inputs(database, config)
        assert _normalized_inputs(rendered_inputs) == _normalized_inputs(source_inputs)
        assert runtime.bsm_metric_logical_checksum(database, config) == (
            bsm_metric_logical_checksum(database, config)
        )
        assert runtime.digest_file(database) == digest_file(database)
        rendered_expected = runtime.expected_market_metric_submission(
            rendered_inputs, config
        )
        assert rendered_expected == source_expected
        runtime.validate_market_metric_submission_contract(rendered_expected, config)
        runtime.verify_market_metric_submission(
            rendered_inputs, rendered_expected, config
        )

        reversed_submission = deepcopy(rendered_expected)
        reversed_submission["rows"].reverse()
        if profile_name == "duckdb_query_v3":
            runtime.validate_market_metric_submission_contract(
                reversed_submission, config
            )
            runtime.verify_market_metric_submission(
                rendered_inputs, reversed_submission, config
            )
        else:
            with pytest.raises(ValueError, match="reordered"):
                runtime.validate_market_metric_submission_contract(
                    reversed_submission, config
                )


@pytest.mark.parametrize("profile_name", tuple(_PROFILES))
def test_rendered_bundle_relocates_and_passes_under_python_isolated_mode(
    tmp_path: Path, profile_name: str
) -> None:
    profile = _PROFILES[profile_name]
    leaf = _representative_leaf(profile_name, "delta")
    staged = tmp_path / f"staged-{profile_name}"
    staged.mkdir()
    database = staged / "task.duckdb"
    shutil.copyfile(leaf / "task.duckdb", database)
    write_metric_leaf_verifier_bundle(staged / "verifier", profile, "delta")
    config = json.loads(
        (staged / "verifier/oracle_config.json").read_text(encoding="utf-8")
    )
    inputs = load_bsm_market_metric_inputs(database, config)
    submission = expected_market_metric_submission(inputs, config)
    if profile_name == "duckdb_query_v3":
        submission["rows"] = submission["rows"][::2] + submission["rows"][1::2]
    (staged / "submission.json").write_bytes(_canonical_json_bytes(submission))
    manifest = {
        "public_child_snapshot": {
            "logical_checksum": bsm_metric_logical_checksum(database, config)
        },
        "artifacts": {"task.duckdb": digest_file(database)},
    }
    (staged / "delivery_manifest.json").write_bytes(
        _canonical_json_bytes(manifest)
    )

    relocated = tmp_path / f"relocated-{profile_name}"
    shutil.copytree(staged, relocated)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["BSM_GREEKS_SUBMISSION"] = str(relocated / "submission.json")
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "pytest", "-q", "verifier"],
        cwd=relocated,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "3 passed" in completed.stdout
    assert _REPOSITORY_ROOT not in relocated.parents
