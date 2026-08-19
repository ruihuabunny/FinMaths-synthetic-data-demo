from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, replace
import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType
from typing import Any, Callable

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    DUCKDB_QUERY_V3_PROFILE,
    STATIC_V2_PROFILE,
    RuntimeProfile,
    get_runtime_profile,
    oracle_config_for_metric,
    validate_render_profile,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    load_bsm_market_metric_inputs,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    submission_duckdb_v3,
    submission_static_v2,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS,
    METRIC_SPECS_DB_QUERY_V3,
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
_PROFILE_NAMES = tuple(_BASELINE["deliveries"])
_PROFILES = {
    "static_v2": STATIC_V2_PROFILE,
    "duckdb_query_v3": DUCKDB_QUERY_V3_PROFILE,
}
_SUBMISSION_MODULES = {
    "static_v2": submission_static_v2,
    "duckdb_query_v3": submission_duckdb_v3,
}
_ALL_CASE_KEYS = tuple(
    (profile, assignment[2])
    for profile in _PROFILE_NAMES
    for assignment in _BASELINE["deliveries"][profile]["assignments"]
)
_REPRESENTATIVE_KEYS = tuple(
    (profile, target)
    for profile in _PROFILE_NAMES
    for target in (spec.target for spec in METRIC_SPECS)
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _suite_root(profile: str) -> Path:
    return _REPOSITORY_ROOT / _BASELINE["deliveries"][profile]["relative_path"]


def _suite_manifest(profile: str) -> dict[str, Any]:
    return _load_json(_suite_root(profile) / "suite_manifest.json")


def _capture_outcome(action: Callable[[], Any]) -> tuple[Any, ...]:
    try:
        return ("returned", action())
    except Exception as error:
        cause = error.__cause__
        return (
            "raised",
            type(error),
            str(error),
            None if cause is None else type(cause),
            None if cause is None else str(cause),
        )


def _assert_same_outcome(
    old_action: Callable[[], Any], new_action: Callable[[], Any]
) -> None:
    assert _capture_outcome(new_action) == _capture_outcome(old_action)


def _different_last_place(value: str) -> str:
    return value[:-1] + ("1" if value[-1] != "1" else "2")


@dataclass(frozen=True)
class _ProtocolRecord:
    profile_name: str
    root: Path
    config: dict[str, Any]
    assignment: dict[str, Any]
    old_runtime: ModuleType
    new_submission: ModuleType
    old_inputs: tuple[Any, ...]
    new_inputs: tuple[Any, ...]
    expected: dict[str, Any]


@pytest.fixture(scope="session")
def frozen_runtime_modules(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, ModuleType]:
    temporary = tmp_path_factory.mktemp("bsm-metric-phase2-old-runtimes")
    modules = {}
    names = []
    for profile_name in _PROFILE_NAMES:
        assignment = _suite_manifest(profile_name)["assignments"][0]
        source = (
            _suite_root(profile_name)
            / assignment["relative_path"]
            / "verifier/runtime.py"
        )
        copied = temporary / f"{profile_name}_runtime.py"
        shutil.copyfile(source, copied)
        name = f"_phase2_frozen_bsm_metric_{profile_name}_runtime"
        spec = importlib.util.spec_from_file_location(name, copied)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[profile_name] = module
        names.append(name)
    yield modules
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture(scope="session")
def protocol_records(
    frozen_runtime_modules: dict[str, ModuleType],
) -> dict[tuple[str, str], _ProtocolRecord]:
    records = {}
    for profile_name in _PROFILE_NAMES:
        old_runtime = frozen_runtime_modules[profile_name]
        new_submission = _SUBMISSION_MODULES[profile_name]
        suite_root = _suite_root(profile_name)
        for assignment in _suite_manifest(profile_name)["assignments"]:
            root = suite_root / assignment["relative_path"]
            database = root / "task.duckdb"
            config = _load_json(root / "verifier/oracle_config.json")
            old_inputs = old_runtime.load_bsm_market_metric_inputs(database, config)
            records[(profile_name, assignment["derived_task_id"])] = _ProtocolRecord(
                profile_name=profile_name,
                root=root,
                config=config,
                assignment=assignment,
                old_runtime=old_runtime,
                new_submission=new_submission,
                old_inputs=old_inputs,
                new_inputs=load_bsm_market_metric_inputs(database, config),
                expected=old_runtime.expected_market_metric_submission(
                    old_inputs, config
                ),
            )
    return records


def _representative(
    records: dict[tuple[str, str], _ProtocolRecord],
    profile_name: str,
    target: str,
) -> _ProtocolRecord:
    matches = [
        record
        for (candidate_profile, _), record in records.items()
        if candidate_profile == profile_name and record.config["target"] == target
    ]
    assert len(matches) == 4
    return matches[0]


def test_typed_profiles_match_all_frozen_registry_and_leaf_configs() -> None:
    registry_by_profile = {
        "static_v2": METRIC_SPECS,
        "duckdb_query_v3": METRIC_SPECS_DB_QUERY_V3,
    }
    fields = (
        "target",
        "output_field",
        "variant_id",
        "method_id",
        "submission_schema_version",
        "verifier_id",
        "task_id_pattern",
        "decimal_constraint",
        "needs_iv_status",
        "database_schema_version",
        "task_version",
    )
    for profile_name, profile in _PROFILES.items():
        assert get_runtime_profile(profile_name) is profile
        assert validate_render_profile(profile) is profile
        registry = registry_by_profile[profile_name]
        assert tuple(spec.target for spec in profile.metric_specs) == tuple(
            spec.target for spec in registry
        )
        for extracted, canonical in zip(profile.metric_specs, registry, strict=True):
            assert tuple(getattr(extracted, field) for field in fields) == tuple(
                getattr(canonical, field) for field in fields
            )

        for assignment in _suite_manifest(profile_name)["assignments"]:
            leaf = _suite_root(profile_name) / assignment["relative_path"]
            config = _load_json(leaf / "verifier/oracle_config.json")
            assert oracle_config_for_metric(profile, assignment["target"]) == config


@pytest.mark.parametrize(
    ("profile_name", "task_id"),
    _ALL_CASE_KEYS,
    ids=[f"{profile}:{task_id}" for profile, task_id in _ALL_CASE_KEYS],
)
def test_explicit_submission_modules_match_valid_frozen_behavior(
    protocol_records: dict[tuple[str, str], _ProtocolRecord],
    profile_name: str,
    task_id: str,
) -> None:
    record = protocol_records[(profile_name, task_id)]
    old_runtime = record.old_runtime
    new_submission = record.new_submission

    assert inspect.signature(
        new_submission.validate_market_metric_submission_contract
    ) == inspect.signature(old_runtime.validate_market_metric_submission_contract)
    assert inspect.signature(
        new_submission.verify_market_metric_submission
    ) == inspect.signature(old_runtime.verify_market_metric_submission)

    new_submission.validate_market_metric_submission_contract(
        record.expected, record.config
    )
    new_submission.verify_market_metric_submission(
        record.new_inputs, record.expected, record.config
    )
    old_runtime.verify_market_metric_submission(
        record.old_inputs, record.expected, record.config
    )


@pytest.mark.parametrize(
    ("profile_name", "task_id"),
    _ALL_CASE_KEYS,
    ids=[f"{profile}:{task_id}" for profile, task_id in _ALL_CASE_KEYS],
)
def test_explicit_modules_preserve_order_and_malformed_contract_outcomes(
    protocol_records: dict[tuple[str, str], _ProtocolRecord],
    profile_name: str,
    task_id: str,
) -> None:
    record = protocol_records[(profile_name, task_id)]
    old_validate = record.old_runtime.validate_market_metric_submission_contract
    new_validate = record.new_submission.validate_market_metric_submission_contract
    config = record.config

    cases = []
    missing_field = deepcopy(record.expected)
    missing_field.pop("status")
    cases.append(missing_field)
    wrong_identity = deepcopy(record.expected)
    wrong_identity["method_id"] = "wrong"
    cases.append(wrong_identity)
    wrong_row_shape = deepcopy(record.expected)
    wrong_row_shape["rows"][0]["unexpected"] = "0.00000000"
    cases.append(wrong_row_shape)
    invalid_decimal = deepcopy(record.expected)
    invalid_decimal["rows"][0][config["output_field"]] = True
    cases.append(invalid_decimal)

    duplicate = deepcopy(record.expected)
    if profile_name == "static_v2":
        duplicate["rows"][1] = deepcopy(duplicate["rows"][0])
    else:
        duplicate["rows"].append(deepcopy(duplicate["rows"][0]))
    cases.append(duplicate)

    missing_row = deepcopy(record.expected)
    missing_row["rows"].pop()
    cases.append(missing_row)

    reversed_rows = deepcopy(record.expected)
    reversed_rows["rows"].reverse()
    cases.append(reversed_rows)

    for submission in cases:
        _assert_same_outcome(
            lambda submission=submission: old_validate(submission, config),
            lambda submission=submission: new_validate(submission, config),
        )

    if profile_name == "duckdb_query_v3":
        record.new_submission.verify_market_metric_submission(
            record.new_inputs, reversed_rows, config
        )
        record.old_runtime.verify_market_metric_submission(
            record.old_inputs, reversed_rows, config
        )


@pytest.mark.parametrize(
    ("profile_name", "target"),
    _REPRESENTATIVE_KEYS,
    ids=[f"{profile}:{target}" for profile, target in _REPRESENTATIVE_KEYS],
)
def test_verification_rejections_keep_exact_wrapper_and_mismatch_errors(
    protocol_records: dict[tuple[str, str], _ProtocolRecord],
    profile_name: str,
    target: str,
) -> None:
    record = _representative(protocol_records, profile_name, target)
    old_verify = record.old_runtime.verify_market_metric_submission
    new_verify = record.new_submission.verify_market_metric_submission

    missing = deepcopy(record.expected)
    missing["rows"].pop()
    _assert_same_outcome(
        lambda: old_verify(record.old_inputs, missing, record.config),
        lambda: new_verify(record.new_inputs, missing, record.config),
    )

    mismatch = deepcopy(record.expected)
    field = record.config["output_field"]
    mismatch["rows"][0][field] = _different_last_place(
        mismatch["rows"][0][field]
    )
    _assert_same_outcome(
        lambda: old_verify(record.old_inputs, mismatch, record.config),
        lambda: new_verify(record.new_inputs, mismatch, record.config),
    )


def test_each_submission_module_rejects_the_other_protocol_profile(
    protocol_records: dict[tuple[str, str], _ProtocolRecord],
) -> None:
    static = _representative(protocol_records, "static_v2", "delta")
    v3 = _representative(protocol_records, "duckdb_query_v3", "delta")

    _assert_same_outcome(
        lambda: static.old_runtime.validate_market_metric_submission_contract(
            v3.expected, v3.config
        ),
        lambda: submission_static_v2.validate_market_metric_submission_contract(
            v3.expected, v3.config
        ),
    )
    _assert_same_outcome(
        lambda: v3.old_runtime.validate_market_metric_submission_contract(
            static.expected, static.config
        ),
        lambda: submission_duckdb_v3.validate_market_metric_submission_contract(
            static.expected, static.config
        ),
    )


def _assert_profile_error(profile: RuntimeProfile, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        validate_render_profile(profile)


def test_render_profile_validation_fails_closed_on_every_owned_identity() -> None:
    profile = STATIC_V2_PROFILE
    first = profile.metric_specs[0]

    _assert_profile_error(
        replace(profile, profile_id="unknown"), "runtime profile id is not supported"
    )
    _assert_profile_error(
        replace(profile, database_schema_version="wrong"),
        "runtime profile database schema version is inconsistent",
    )
    _assert_profile_error(
        replace(profile, task_version="wrong"),
        "runtime profile task version is inconsistent",
    )
    _assert_profile_error(
        replace(
            profile,
            metric_specs=(replace(first, target="wrong"),) + profile.metric_specs[1:],
        ),
        "runtime profile metric keys are inconsistent",
    )
    _assert_profile_error(
        replace(
            profile,
            metric_specs=(
                replace(first, database_schema_version="wrong"),
            )
            + profile.metric_specs[1:],
        ),
        "runtime profile database schema version is inconsistent",
    )
    _assert_profile_error(
        replace(
            profile,
            metric_specs=(replace(first, task_version="wrong"),)
            + profile.metric_specs[1:],
        ),
        "runtime profile task version is inconsistent",
    )
    _assert_profile_error(
        replace(profile, submission_module="wrong"),
        "runtime profile submission module is inconsistent",
    )
    _assert_profile_error(
        replace(
            profile,
            metric_specs=(replace(first, method_id="wrong"),)
            + profile.metric_specs[1:],
        ),
        "runtime profile metric definitions differ from canonical",
    )


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


def test_phase2_sources_have_no_shadowing_dynamic_execution_or_dispatch() -> None:
    forbidden_calls = {"compile", "eval", "exec"}
    for path in sorted(_SOURCE_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        bound_names = [
            name for node in tree.body for name in _top_level_bound_names(node)
        ]
        assert len(bound_names) == len(set(bound_names)), path
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden_calls
            for node in ast.walk(tree)
        ), path
        assert "ModuleType" not in source
        assert "_V3_RUNTIME_SUFFIX" not in source
        assert "bsm_market_metric_verifier_runtime" not in source

    protocol_sources = {
        "static_v2": (_SOURCE_PACKAGE / "submission_static_v2.py").read_text(
            encoding="utf-8"
        ),
        "duckdb_query_v3": (
            _SOURCE_PACKAGE / "submission_duckdb_v3.py"
        ).read_text(encoding="utf-8"),
    }
    assert "DUCKDB_QUERY_V3_PROFILE" not in protocol_sources["static_v2"]
    assert "submission_duckdb_v3" not in protocol_sources["static_v2"]
    assert "STATIC_V2_PROFILE" not in protocol_sources["duckdb_query_v3"]
    assert "submission_static_v2" not in protocol_sources["duckdb_query_v3"]

    for profile in _PROFILES.values():
        selected = _SOURCE_PACKAGE / f"{profile.submission_module}.py"
        assert validate_render_profile(profile) is profile
        assert selected.is_file()


@pytest.mark.parametrize("profile_name", _PROFILE_NAMES)
def test_phase2_profile_selection_contains_exactly_one_submission_parser(
    tmp_path: Path, profile_name: str
) -> None:
    profile = validate_render_profile(_PROFILES[profile_name])
    selected_source = _SOURCE_PACKAGE / f"{profile.submission_module}.py"
    bundle = tmp_path / "_runtime"
    bundle.mkdir()
    for filename in ("__init__.py", "models.py", "database.py", "oracle.py"):
        shutil.copyfile(_SOURCE_PACKAGE / filename, bundle / filename)
    shutil.copyfile(_SOURCE_PACKAGE / "profiles.py", bundle / "profiles.py")
    shutil.copyfile(selected_source, bundle / "submission.py")

    assert {path.name for path in bundle.iterdir()} == {
        "__init__.py",
        "database.py",
        "models.py",
        "oracle.py",
        "profiles.py",
        "submission.py",
    }
    submission_source = (bundle / "submission.py").read_text(encoding="utf-8")
    other_module = (
        "submission_duckdb_v3"
        if profile_name == "static_v2"
        else "submission_static_v2"
    )
    assert other_module not in submission_source
    tree = ast.parse(submission_source)
    definitions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert definitions.count("validate_market_metric_submission_contract") == 1
    assert definitions.count("verify_market_metric_submission") == 1
