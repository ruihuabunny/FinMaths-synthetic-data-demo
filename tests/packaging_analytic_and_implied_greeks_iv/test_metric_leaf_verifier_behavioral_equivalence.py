from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType
from typing import Any

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
    write_metric_verifier,
    write_metric_verifier_v3,
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


def _load_package_runtime(
    verifier: Path, package_name: str
) -> tuple[ModuleType, tuple[str, ...]]:
    package_spec = importlib.util.spec_from_file_location(
        package_name,
        verifier / "__init__.py",
        submodule_search_locations=[str(verifier)],
    )
    assert package_spec is not None and package_spec.loader is not None
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = package
    package_spec.loader.exec_module(package)
    runtime = importlib.import_module(f"{package_name}.runtime")
    loaded_names = tuple(
        name
        for name in sys.modules
        if name == package_name or name.startswith(f"{package_name}.")
    )
    return runtime, loaded_names


def _load_frozen_runtime(source: Path, module_name: str) -> ModuleType:
    runtime_spec = importlib.util.spec_from_file_location(module_name, source)
    assert runtime_spec is not None and runtime_spec.loader is not None
    runtime = importlib.util.module_from_spec(runtime_spec)
    sys.modules[module_name] = runtime
    runtime_spec.loader.exec_module(runtime)
    return runtime


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
) -> tuple[Any, ...]:
    old_outcome = _capture_outcome(old_action)
    new_outcome = _capture_outcome(new_action)
    # Full messages and causes are compared; this is stricter than comparing
    # only the rejection category and stable message prefix required by Phase 5.
    assert new_outcome == old_outcome
    return old_outcome


def _normalized_inputs(inputs: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(asdict(item) for item in inputs)


def _different_last_place(value: str) -> str:
    return value[:-1] + ("1" if value[-1] != "1" else "2")


@dataclass(frozen=True)
class _RuntimePair:
    old: ModuleType
    new: ModuleType


@dataclass(frozen=True)
class _EquivalenceRecord:
    profile: str
    root: Path
    assignment: dict[str, Any]
    config: dict[str, Any]
    runtimes: _RuntimePair
    old_inputs: tuple[Any, ...]
    new_inputs: tuple[Any, ...]
    old_checksum: str
    new_checksum: str
    old_expected: dict[str, Any]
    new_expected: dict[str, Any]


@pytest.fixture(scope="session")
def production_runtime_pairs(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, _RuntimePair]:
    temporary = tmp_path_factory.mktemp("bsm-metric-phase5-runtimes")
    result: dict[str, _RuntimePair] = {}
    loaded_module_names: list[str] = []

    for profile in _PROFILE_NAMES:
        first_assignment = _suite_manifest(profile)["assignments"][0]
        frozen_source = (
            _suite_root(profile)
            / first_assignment["relative_path"]
            / "verifier/runtime.py"
        )
        copied_source = temporary / f"{profile}_frozen_runtime.py"
        shutil.copyfile(frozen_source, copied_source)
        old_module_name = f"_phase5_frozen_bsm_metric_{profile}_runtime"
        old_runtime = _load_frozen_runtime(copied_source, old_module_name)
        loaded_module_names.append(old_module_name)

        verifier = temporary / f"{profile}_modular/verifier"
        writer = (
            write_metric_verifier_v3
            if profile == "duckdb_query_v3"
            else write_metric_verifier
        )
        writer(verifier, "iv")
        package_name = f"_phase5_modular_bsm_metric_{profile}"
        new_runtime, package_modules = _load_package_runtime(
            verifier, package_name
        )
        loaded_module_names.extend(package_modules)
        result[profile] = _RuntimePair(old=old_runtime, new=new_runtime)

    yield result

    for module_name in reversed(loaded_module_names):
        sys.modules.pop(module_name, None)


@pytest.fixture(scope="session")
def equivalence_records(
    production_runtime_pairs: dict[str, _RuntimePair],
) -> dict[tuple[str, str], _EquivalenceRecord]:
    records: dict[tuple[str, str], _EquivalenceRecord] = {}
    for profile in _PROFILE_NAMES:
        runtimes = production_runtime_pairs[profile]
        suite_root = _suite_root(profile)
        for assignment in _suite_manifest(profile)["assignments"]:
            root = suite_root / assignment["relative_path"]
            database = root / "task.duckdb"
            config = _load_json(root / "verifier/oracle_config.json")
            old_inputs = runtimes.old.load_bsm_market_metric_inputs(
                database, config
            )
            new_inputs = runtimes.new.load_bsm_market_metric_inputs(
                database, config
            )
            old_expected = runtimes.old.expected_market_metric_submission(
                old_inputs, config
            )
            new_expected = runtimes.new.expected_market_metric_submission(
                new_inputs, config
            )
            task_id = assignment["derived_task_id"]
            records[(profile, task_id)] = _EquivalenceRecord(
                profile=profile,
                root=root,
                assignment=assignment,
                config=config,
                runtimes=runtimes,
                old_inputs=old_inputs,
                new_inputs=new_inputs,
                old_checksum=runtimes.old.bsm_metric_logical_checksum(
                    database, config
                ),
                new_checksum=runtimes.new.bsm_metric_logical_checksum(
                    database, config
                ),
                old_expected=old_expected,
                new_expected=new_expected,
            )
    return records


def _submission_cases(
    profile: str,
    expected: Mapping[str, Any],
    output_field: str,
) -> Iterator[tuple[str, Any]]:
    yield "malformed_non_mapping", []

    missing_top_level = deepcopy(expected)
    missing_top_level.pop("status")
    yield "missing_top_level_field", missing_top_level

    extra_top_level = deepcopy(expected)
    extra_top_level["tolerance"] = "0.00000001"
    yield "extra_top_level_field", extra_top_level

    for field in (
        "task_id",
        "submission_schema_version",
        "method_id",
        "status",
    ):
        wrong_identity = deepcopy(expected)
        wrong_identity[field] = "wrong"
        yield f"wrong_{field}", wrong_identity

    missing_row_field = deepcopy(expected)
    missing_row_field["rows"][0].pop(output_field)
    yield "missing_row_field", missing_row_field

    extra_row_field = deepcopy(expected)
    extra_row_field["rows"][0]["unexpected"] = "0.00000000"
    yield "extra_row_field", extra_row_field

    missing_row = deepcopy(expected)
    missing_row["rows"].pop()
    yield "missing_row", missing_row

    extra_row = deepcopy(expected)
    extra = deepcopy(extra_row["rows"][-1])
    if profile == "duckdb_query_v3":
        extra["row_id"] = "row_999999"
    extra_row["rows"].append(extra)
    yield "extra_row", extra_row

    duplicate_row = deepcopy(expected)
    if profile == "duckdb_query_v3":
        duplicate_row["rows"].append(deepcopy(duplicate_row["rows"][0]))
    else:
        duplicate_row["rows"][1] = deepcopy(duplicate_row["rows"][0])
    yield "duplicate_row_identity", duplicate_row

    reordered = deepcopy(expected)
    reordered["rows"].reverse()
    yield "reordered_rows", reordered

    for label, value in (
        ("short_decimal", "0.0"),
        ("leading_zero_decimal", "01.00000000"),
        ("negative_zero", "-0.00000000"),
        ("numeric_decimal", 0.0),
        ("boolean_decimal", True),
        ("nan_decimal", float("nan")),
        ("infinite_decimal", float("inf")),
    ):
        noncanonical = deepcopy(expected)
        noncanonical["rows"][0][output_field] = value
        yield label, noncanonical

    if "iv_status" in expected["rows"][0]:
        wrong_iv_status = deepcopy(expected)
        wrong_iv_status["rows"][0]["iv_status"] = "wrong"
        yield "wrong_iv_status", wrong_iv_status

    canonical_mismatch = deepcopy(expected)
    value = canonical_mismatch["rows"][0][output_field]
    canonical_mismatch["rows"][0][output_field] = _different_last_place(value)
    yield "canonical_value_mismatch", canonical_mismatch


@contextmanager
def _cached_expected_submission(
    runtime: ModuleType, expected: dict[str, Any]
) -> Iterator[None]:
    submission_module = sys.modules[
        runtime.verify_market_metric_submission.__module__
    ]
    original = submission_module._expected_submission
    submission_module._expected_submission = lambda *_: expected
    try:
        yield
    finally:
        submission_module._expected_submission = original


def test_phase5_matrix_contains_all_24_assignments_from_both_suites(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
) -> None:
    assert len(_ALL_CASE_KEYS) == 48
    assert set(equivalence_records) == set(_ALL_CASE_KEYS)
    assert Counter(profile for profile, _ in equivalence_records) == Counter(
        {"static_v2": 24, "duckdb_query_v3": 24}
    )


@pytest.mark.parametrize(
    ("profile", "task_id"),
    _ALL_CASE_KEYS,
    ids=[f"{profile}:{task_id}" for profile, task_id in _ALL_CASE_KEYS],
)
def test_every_assignment_has_exact_parsing_checksum_truth_and_valid_verification(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    profile: str,
    task_id: str,
) -> None:
    record = equivalence_records[(profile, task_id)]
    old_runtime = record.runtimes.old
    new_runtime = record.runtimes.new

    assert _normalized_inputs(record.new_inputs) == _normalized_inputs(
        record.old_inputs
    )
    assert record.new_checksum == record.old_checksum
    assert record.new_checksum == record.assignment["derived_logical_checksum"]
    assert record.new_expected == record.old_expected

    assert _assert_same_outcome(
        lambda: old_runtime.validate_market_metric_submission_contract(
            record.old_expected, record.config
        ),
        lambda: new_runtime.validate_market_metric_submission_contract(
            record.new_expected, record.config
        ),
    ) == ("returned", None)
    assert _assert_same_outcome(
        lambda: old_runtime.verify_market_metric_submission(
            record.old_inputs, record.old_expected, record.config
        ),
        lambda: new_runtime.verify_market_metric_submission(
            record.new_inputs, record.new_expected, record.config
        ),
    ) == ("returned", None)


@pytest.mark.parametrize(
    ("profile", "task_id"),
    _ALL_CASE_KEYS,
    ids=[f"{profile}:{task_id}" for profile, task_id in _ALL_CASE_KEYS],
)
def test_every_assignment_has_exact_invalid_and_ordering_outcomes(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    profile: str,
    task_id: str,
) -> None:
    record = equivalence_records[(profile, task_id)]
    old_runtime = record.runtimes.old
    new_runtime = record.runtimes.new
    output_field = record.config["output_field"]

    with _cached_expected_submission(
        old_runtime, record.old_expected
    ), _cached_expected_submission(new_runtime, record.new_expected):
        outcomes = {}
        for name, submission in _submission_cases(
            profile, record.old_expected, output_field
        ):
            validation = _assert_same_outcome(
                lambda submission=submission: (
                    old_runtime.validate_market_metric_submission_contract(
                        submission, record.config
                    )
                ),
                lambda submission=submission: (
                    new_runtime.validate_market_metric_submission_contract(
                        submission, record.config
                    )
                ),
            )
            verification = _assert_same_outcome(
                lambda submission=submission: (
                    old_runtime.verify_market_metric_submission(
                        record.old_inputs, submission, record.config
                    )
                ),
                lambda submission=submission: (
                    new_runtime.verify_market_metric_submission(
                        record.new_inputs, submission, record.config
                    )
                ),
            )
            outcomes[name] = (validation, verification)

    reordered = outcomes["reordered_rows"]
    if profile == "duckdb_query_v3":
        assert reordered == (("returned", None), ("returned", None))
    else:
        assert reordered[0][0] == "raised"
        assert reordered[1][0] == "raised"
    assert outcomes["canonical_value_mismatch"][0] == ("returned", None)
    assert outcomes["canonical_value_mismatch"][1][0:3] == (
        "raised",
        ValueError,
        "single-metric canonical submission mismatch",
    )


@pytest.mark.parametrize("profile", _PROFILE_NAMES)
def test_rendered_dependency_pin_failures_match_and_remain_fail_closed(
    equivalence_records: dict[tuple[str, str], _EquivalenceRecord],
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    record = next(
        item
        for (candidate_profile, _), item in equivalence_records.items()
        if candidate_profile == profile
    )
    database = record.root / "task.duckdb"
    old_runtime = record.runtimes.old
    new_runtime = record.runtimes.new
    old_database_module = sys.modules[
        old_runtime.load_bsm_market_metric_inputs.__module__
    ]
    new_database_module = sys.modules[
        new_runtime.load_bsm_market_metric_inputs.__module__
    ]
    old_oracle_module = sys.modules[
        old_runtime.expected_market_metric_submission.__module__
    ]
    new_oracle_module = sys.modules[
        new_runtime.expected_market_metric_submission.__module__
    ]

    with monkeypatch.context() as patch:
        patch.setattr(old_database_module.duckdb, "__version__", "wrong")
        old_duckdb = _capture_outcome(
            lambda: old_runtime.load_bsm_market_metric_inputs(
                database, record.config
            )
        )
    with monkeypatch.context() as patch:
        patch.setattr(new_database_module.duckdb, "__version__", "wrong")
        new_duckdb = _capture_outcome(
            lambda: new_runtime.load_bsm_market_metric_inputs(
                database, record.config
            )
        )
    assert new_duckdb == old_duckdb
    assert new_duckdb[0:2] == ("raised", RuntimeError)

    with monkeypatch.context() as patch:
        patch.setattr(old_oracle_module.ql, "__version__", "wrong")
        old_quantlib = _capture_outcome(
            lambda: old_runtime.expected_market_metric_submission(
                record.old_inputs, record.config
            )
        )
    with monkeypatch.context() as patch:
        patch.setattr(new_oracle_module.ql, "__version__", "wrong")
        new_quantlib = _capture_outcome(
            lambda: new_runtime.expected_market_metric_submission(
                record.new_inputs, record.config
            )
        )
    assert new_quantlib == old_quantlib
    assert new_quantlib[0:2] == ("raised", RuntimeError)
