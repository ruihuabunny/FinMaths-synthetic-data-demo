from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import asdict, dataclass
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType
from typing import Any, Callable

import duckdb
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv import (
    metric_leaf_verifier_runtime as shared_runtime,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    database as shared_database,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    oracle as shared_oracle,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = json.loads(
    (
        _REPOSITORY_ROOT
        / "tests/fixtures/bsm_metric_leaf_verifier_20260819_baseline.json"
    ).read_text(encoding="utf-8")
)
_PROFILE_NAMES = tuple(_BASELINE["deliveries"])
_ALL_CASE_KEYS = tuple(
    (profile, assignment[2])
    for profile in _PROFILE_NAMES
    for assignment in _BASELINE["deliveries"][profile]["assignments"]
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _suite_root(profile: str) -> Path:
    return _REPOSITORY_ROOT / _BASELINE["deliveries"][profile]["relative_path"]


def _suite_manifest(profile: str) -> dict[str, Any]:
    return _load_json(_suite_root(profile) / "suite_manifest.json")


def _capture_failure(action: Callable[[], Any]) -> tuple[type[Exception], str]:
    try:
        action()
    except Exception as error:
        return type(error), str(error)
    raise AssertionError("expected action to fail")


def _assert_same_failure(old_action: Callable[[], Any], new_action: Callable[[], Any]) -> None:
    assert _capture_failure(new_action) == _capture_failure(old_action)


@dataclass(frozen=True)
class _EquivalenceRecord:
    profile: str
    root: Path
    assignment: dict[str, Any]
    config: dict[str, Any]
    old_runtime: ModuleType
    old_inputs: tuple[Any, ...]
    new_inputs: tuple[Any, ...]
    old_checksum: str
    new_checksum: str
    old_expected: dict[str, Any]
    new_expected: dict[str, Any]


@pytest.fixture(scope="session")
def frozen_runtime_modules(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, ModuleType]:
    temporary = tmp_path_factory.mktemp("bsm-metric-phase1-old-runtimes")
    modules = {}
    names = []
    for profile in _PROFILE_NAMES:
        assignment = _suite_manifest(profile)["assignments"][0]
        source = (
            _suite_root(profile)
            / assignment["relative_path"]
            / "verifier/runtime.py"
        )
        copied = temporary / f"{profile}_runtime.py"
        shutil.copyfile(source, copied)
        name = f"_phase1_frozen_bsm_metric_{profile}_runtime"
        spec = importlib.util.spec_from_file_location(name, copied)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[profile] = module
        names.append(name)
    yield modules
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture(scope="session")
def equivalence_records(
    frozen_runtime_modules: dict[str, ModuleType],
) -> dict[tuple[str, str], _EquivalenceRecord]:
    records = {}
    for profile in _PROFILE_NAMES:
        old_runtime = frozen_runtime_modules[profile]
        suite_root = _suite_root(profile)
        for assignment in _suite_manifest(profile)["assignments"]:
            root = suite_root / assignment["relative_path"]
            config = _load_json(root / "verifier/oracle_config.json")
            database = root / "task.duckdb"
            old_inputs = old_runtime.load_bsm_market_metric_inputs(database, config)
            new_inputs = shared_runtime.load_bsm_market_metric_inputs(database, config)
            records[(profile, assignment["derived_task_id"])] = _EquivalenceRecord(
                profile=profile,
                root=root,
                assignment=assignment,
                config=config,
                old_runtime=old_runtime,
                old_inputs=old_inputs,
                new_inputs=new_inputs,
                old_checksum=old_runtime.bsm_metric_logical_checksum(database, config),
                new_checksum=shared_runtime.bsm_metric_logical_checksum(
                    database, config
                ),
                old_expected=old_runtime.expected_market_metric_submission(
                    old_inputs, config
                ),
                new_expected=shared_runtime.expected_market_metric_submission(
                    new_inputs, config
                ),
            )
    return records


def _representative(
    records: dict[tuple[str, str], _EquivalenceRecord], profile: str
) -> _EquivalenceRecord:
    return next(
        record
        for (candidate_profile, _), record in records.items()
        if candidate_profile == profile
    )


def test_extracted_shared_functions_preserve_the_old_signatures(
    frozen_runtime_modules: dict[str, ModuleType],
) -> None:
    names = (
        "digest_file",
        "load_bsm_market_metric_inputs",
        "bsm_metric_logical_checksum",
        "expected_market_metric_submission",
    )
    for old_runtime in frozen_runtime_modules.values():
        for name in names:
            assert inspect.signature(getattr(shared_runtime, name)) == inspect.signature(
                getattr(old_runtime, name)
            )


@pytest.mark.parametrize(
    ("profile", "task_id"),
    _ALL_CASE_KEYS,
    ids=[f"{profile}:{task_id}" for profile, task_id in _ALL_CASE_KEYS],
)
def test_old_and_extracted_modules_are_exact_on_every_frozen_assignment(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    profile: str,
    task_id: str,
) -> None:
    record = equivalence_records[(profile, task_id)]
    database = record.root / "task.duckdb"

    assert tuple(asdict(item) for item in record.new_inputs) == tuple(
        asdict(item) for item in record.old_inputs
    )
    assert record.new_checksum == record.old_checksum
    assert record.new_checksum == record.assignment["derived_logical_checksum"]
    assert record.new_expected == record.old_expected
    assert shared_runtime.digest_file(database) == record.old_runtime.digest_file(
        database
    )
    assert record.new_expected["task_id"] == task_id


@pytest.mark.parametrize("profile", _PROFILE_NAMES)
def test_config_path_and_input_failures_keep_exact_types_and_messages(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    tmp_path: Path,
    profile: str,
) -> None:
    record = _representative(equivalence_records, profile)
    database = record.root / "task.duckdb"
    invalid_config = dict(record.config)
    invalid_config["iterations"] = 79

    _assert_same_failure(
        lambda: record.old_runtime.load_bsm_market_metric_inputs(
            database, invalid_config
        ),
        lambda: shared_runtime.load_bsm_market_metric_inputs(
            database, invalid_config
        ),
    )
    _assert_same_failure(
        lambda: record.old_runtime.bsm_metric_logical_checksum(
            database, invalid_config
        ),
        lambda: shared_runtime.bsm_metric_logical_checksum(
            database, invalid_config
        ),
    )
    _assert_same_failure(
        lambda: record.old_runtime.expected_market_metric_submission(
            record.old_inputs, invalid_config
        ),
        lambda: shared_runtime.expected_market_metric_submission(
            record.new_inputs, invalid_config
        ),
    )

    missing = tmp_path / "missing.duckdb"
    _assert_same_failure(
        lambda: record.old_runtime.load_bsm_market_metric_inputs(
            missing, record.config
        ),
        lambda: shared_runtime.load_bsm_market_metric_inputs(
            missing, record.config
        ),
    )
    _assert_same_failure(
        lambda: record.old_runtime.bsm_metric_logical_checksum(
            missing, record.config
        ),
        lambda: shared_runtime.bsm_metric_logical_checksum(
            missing, record.config
        ),
    )

    _assert_same_failure(
        lambda: record.old_runtime.expected_market_metric_submission(
            record.old_inputs[:-1], record.config
        ),
        lambda: shared_runtime.expected_market_metric_submission(
            record.new_inputs[:-1], record.config
        ),
    )


@pytest.mark.parametrize("profile", _PROFILE_NAMES)
def test_input_model_domain_failures_are_mechanically_preserved(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    profile: str,
) -> None:
    record = _representative(equivalence_records, profile)
    baseline = asdict(record.old_inputs[0])

    cases = []
    missing = deepcopy(baseline)
    missing.pop("ask")
    cases.append(missing)
    for field, value in (
        ("task_id", "wrong"),
        ("valuation_date", "not-a-date"),
        ("spot", True),
        ("calendar", "TARGET"),
        ("bid", "999999999.00000000"),
    ):
        changed = deepcopy(baseline)
        changed[field] = value
        cases.append(changed)

    for changed in cases:
        _assert_same_failure(
            lambda changed=changed: record.old_runtime.BSMMarketMetricInput.from_mapping(
                changed
            ),
            lambda changed=changed: shared_runtime.BSMMarketMetricInput.from_mapping(
                changed
            ),
        )


def _tamper_database(source: Path, destination: Path, mutation: str) -> None:
    shutil.copyfile(source, destination)
    connection = duckdb.connect(str(destination))
    try:
        if mutation == "unexpected_relation":
            connection.execute("CREATE TABLE solver_visible.unexpected(value INTEGER)")
        elif mutation == "metadata_identity":
            connection.execute(
                "UPDATE metadata.public_task SET schema_version = 'wrong'"
            )
        elif mutation == "input_domain":
            connection.execute(
                """
                UPDATE solver_visible.underlying_market_inputs
                SET calendar = 'TARGET'
                WHERE underlying_id = (
                    SELECT min(underlying_id)
                    FROM solver_visible.underlying_market_inputs
                )
                """
            )
        else:
            raise AssertionError(f"unknown database mutation: {mutation}")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


@pytest.mark.parametrize("profile", _PROFILE_NAMES)
@pytest.mark.parametrize(
    "mutation", ("unexpected_relation", "metadata_identity", "input_domain")
)
def test_database_rejection_categories_match_the_frozen_runtime(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    tmp_path: Path,
    profile: str,
    mutation: str,
) -> None:
    record = _representative(equivalence_records, profile)
    tampered = tmp_path / f"{profile}-{mutation}.duckdb"
    _tamper_database(record.root / "task.duckdb", tampered, mutation)

    _assert_same_failure(
        lambda: record.old_runtime.load_bsm_market_metric_inputs(
            tampered, record.config
        ),
        lambda: shared_runtime.load_bsm_market_metric_inputs(
            tampered, record.config
        ),
    )
    _assert_same_failure(
        lambda: record.old_runtime.bsm_metric_logical_checksum(
            tampered, record.config
        ),
        lambda: shared_runtime.bsm_metric_logical_checksum(
            tampered, record.config
        ),
    )


@pytest.mark.parametrize("profile", _PROFILE_NAMES)
def test_dependency_pin_failures_stay_at_the_same_entry_boundaries(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    record = _representative(equivalence_records, profile)
    database = record.root / "task.duckdb"

    with monkeypatch.context() as patch:
        patch.setattr(record.old_runtime.duckdb, "__version__", "wrong")
        old_duckdb_failure = _capture_failure(
            lambda: record.old_runtime.load_bsm_market_metric_inputs(
                database, record.config
            )
        )
    with monkeypatch.context() as patch:
        patch.setattr(shared_database.duckdb, "__version__", "wrong")
        new_duckdb_failure = _capture_failure(
            lambda: shared_runtime.load_bsm_market_metric_inputs(
                database, record.config
            )
        )
    assert new_duckdb_failure == old_duckdb_failure

    with monkeypatch.context() as patch:
        patch.setattr(record.old_runtime.ql, "__version__", "wrong")
        old_quantlib_failure = _capture_failure(
            lambda: record.old_runtime.expected_market_metric_submission(
                record.old_inputs, record.config
            )
        )
    with monkeypatch.context() as patch:
        patch.setattr(shared_oracle.ql, "__version__", "wrong")
        new_quantlib_failure = _capture_failure(
            lambda: shared_runtime.expected_market_metric_submission(
                record.new_inputs, record.config
            )
        )
    assert new_quantlib_failure == old_quantlib_failure


def test_phase1_modules_keep_the_intended_acyclic_dependency_direction() -> None:
    package = (
        _REPOSITORY_ROOT
        / "src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv"
        / "metric_leaf_verifier_runtime"
    )
    imports_by_module = {}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports_by_module[path.stem] = imports
        assert all(
            not imported.startswith(
                ("synthetic_derivatives", "solver", "reference")
            )
            for imported in imports
        )
        assert imports.isdisjoint(
            {"submission_static_v2", "submission_duckdb_v3"}
        )

    assert imports_by_module["models"].isdisjoint({"database", "oracle"})
    assert "oracle" not in imports_by_module["database"]
    assert "database" not in imports_by_module["oracle"]


def test_phase4_switches_production_to_the_modular_renderer_inventory() -> None:
    from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
        METRIC_LEAF_VERIFIER_FILENAMES,
        STATIC_V2_PROFILE,
        render_metric_leaf_verifier_files,
    )
    from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
        METRIC_VERIFIER_FILENAMES,
        metric_verifier_files,
    )

    source = (
        _REPOSITORY_ROOT
        / "src/synthetic_derivatives/packaging_analytic_and_implied_greeks_iv"
        / "metric_verifier.py"
    ).read_text(encoding="utf-8")
    assert METRIC_VERIFIER_FILENAMES is METRIC_LEAF_VERIFIER_FILENAMES
    assert metric_verifier_files("delta") == render_metric_leaf_verifier_files(
        STATIC_V2_PROFILE, "delta"
    )
    assert "metric_leaf_verifier_runtime" in source
    assert "bsm_market_metric_verifier_runtime" not in source
