from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType
from typing import Any, Callable

import pytest


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_PATH = (
    _REPOSITORY_ROOT
    / "tests/fixtures/bsm_metric_leaf_verifier_20260819_baseline.json"
)
_BASELINE = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
_PROFILE_NAMES = tuple(_BASELINE["deliveries"])
_TARGETS = (
    "iv",
    "delta",
    "gamma",
    "vega_1volpt",
    "theta_1calendar_day",
    "rho_1pct",
)
_ALL_CASE_KEYS = tuple(
    (profile, assignment[2])
    for profile in _PROFILE_NAMES
    for assignment in _BASELINE["deliveries"][profile]["assignments"]
)
_REPRESENTATIVE_KEYS = tuple(
    (profile, target) for profile in _PROFILE_NAMES for target in _TARGETS
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _tree_identity(root: Path) -> tuple[str, int]:
    """Bind every relative file path and byte digest in one stable identity."""

    hasher = sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(root).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(_file_digest(path).encode("ascii"))
        hasher.update(b"\n")
    return hasher.hexdigest(), len(files)


def _suite_root(profile: str) -> Path:
    return _REPOSITORY_ROOT / _BASELINE["deliveries"][profile]["relative_path"]


def _suite_manifest(profile: str) -> dict[str, Any]:
    return _load_json(_suite_root(profile) / "suite_manifest.json")


def _assert_value_error(
    action: Callable[[], Any], expected_prefix: str
) -> ValueError:
    with pytest.raises(ValueError) as captured:
        action()
    error = captured.value
    assert type(error) is ValueError
    assert str(error).startswith(expected_prefix)
    return error


def _different_last_place(value: str) -> str:
    return value[:-1] + ("1" if value[-1] != "1" else "2")


@dataclass(frozen=True)
class _CharacterizedLeaf:
    profile: str
    root: Path
    assignment: dict[str, Any]
    delivery_manifest: dict[str, Any]
    runtime: ModuleType
    config: dict[str, Any]
    inputs: tuple[Any, ...]
    expected: dict[str, Any]
    logical_checksum: str


@pytest.fixture(scope="module", autouse=True)
def _frozen_delivery_write_guard() -> Any:
    before = {}
    for profile in _PROFILE_NAMES:
        expected = _BASELINE["deliveries"][profile]
        identity = _tree_identity(_suite_root(profile))
        assert identity == (expected["tree_sha256"], expected["file_count"])
        before[profile] = identity
    yield
    after = {profile: _tree_identity(_suite_root(profile)) for profile in _PROFILE_NAMES}
    assert after == before


@pytest.fixture(scope="session")
def frozen_runtime_modules(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, ModuleType]:
    """Import copied golden runtimes without writing into accepted deliveries."""

    temporary = tmp_path_factory.mktemp("bsm-metric-golden-runtimes")
    modules: dict[str, ModuleType] = {}
    module_names: list[str] = []
    for profile in _PROFILE_NAMES:
        first_assignment = _suite_manifest(profile)["assignments"][0]
        source = (
            _suite_root(profile)
            / first_assignment["relative_path"]
            / "verifier/runtime.py"
        )
        copied = temporary / f"{profile}_runtime.py"
        shutil.copyfile(source, copied)
        module_name = f"_frozen_bsm_metric_{profile}_runtime"
        spec = importlib.util.spec_from_file_location(module_name, copied)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        modules[profile] = module
        module_names.append(module_name)
    yield modules
    for module_name in module_names:
        sys.modules.pop(module_name, None)


@pytest.fixture(scope="session")
def characterized_leaves(
    frozen_runtime_modules: dict[str, ModuleType],
) -> dict[tuple[str, str], _CharacterizedLeaf]:
    result = {}
    for profile in _PROFILE_NAMES:
        runtime = frozen_runtime_modules[profile]
        suite_root = _suite_root(profile)
        for assignment in _suite_manifest(profile)["assignments"]:
            leaf_root = suite_root / assignment["relative_path"]
            config = _load_json(leaf_root / "verifier/oracle_config.json")
            inputs = runtime.load_bsm_market_metric_inputs(
                leaf_root / "task.duckdb", config
            )
            expected = runtime.expected_market_metric_submission(inputs, config)
            checksum = runtime.bsm_metric_logical_checksum(
                leaf_root / "task.duckdb", config
            )
            result[(profile, assignment["derived_task_id"])] = _CharacterizedLeaf(
                profile=profile,
                root=leaf_root,
                assignment=assignment,
                delivery_manifest=_load_json(leaf_root / "delivery_manifest.json"),
                runtime=runtime,
                config=config,
                inputs=inputs,
                expected=expected,
                logical_checksum=checksum,
            )
    return result


def _representative_leaf(
    leaves: dict[tuple[str, str], _CharacterizedLeaf],
    profile: str,
    target: str,
) -> _CharacterizedLeaf:
    matches = [
        leaf
        for (candidate_profile, _), leaf in leaves.items()
        if candidate_profile == profile and leaf.assignment["target"] == target
    ]
    assert len(matches) == 4
    return matches[0]


def test_golden_fixture_records_exact_suite_and_assignment_identities() -> None:
    fields = _BASELINE["assignment_fields"]
    assert fields == ["target", "source_task_id", "derived_task_id"]
    for profile in _PROFILE_NAMES:
        expected = _BASELINE["deliveries"][profile]
        root = _suite_root(profile)
        manifest_path = root / "suite_manifest.json"
        manifest = _load_json(manifest_path)

        assert _file_digest(manifest_path) == expected["suite_manifest_sha256"]
        assert manifest["delivery_id"] == expected["delivery_id"]
        assert manifest["suite_id"] == expected["suite_id"]
        assert manifest["suite_schema_version"] == expected["suite_schema_version"]
        assert manifest["assignment_digest"] == expected["assignment_digest"]
        assert manifest["total_task_count"] == expected["total_task_count"] == 24
        assert [
            [assignment[field] for field in fields]
            for assignment in manifest["assignments"]
        ] == expected["assignments"]
        assert Counter(
            assignment["target"] for assignment in manifest["assignments"]
        ) == Counter({target: 4 for target in _TARGETS})
        for assignment in manifest["assignments"]:
            assert assignment["relative_path"] == (
                f"targets/{assignment['target']}/tasks/"
                f"{assignment['derived_task_id']}"
            )


def test_golden_suites_have_the_same_24_source_target_assignments() -> None:
    identities = []
    for profile in _PROFILE_NAMES:
        identities.append(
            {
                (assignment["source_task_id"], assignment["target"])
                for assignment in _suite_manifest(profile)["assignments"]
            }
        )
    assert len(identities[0]) == 24
    assert identities[0] == identities[1]


def test_every_leaf_runtime_matches_its_protocol_golden_bytes() -> None:
    protocol_sources = {}
    for profile in _PROFILE_NAMES:
        sources = {
            (
                _suite_root(profile)
                / assignment["relative_path"]
                / "verifier/runtime.py"
            ).read_bytes()
            for assignment in _suite_manifest(profile)["assignments"]
        }
        assert len(sources) == 1
        protocol_sources[profile] = sources.pop()

    static_source = protocol_sources["static_v2"]
    v3_source = protocol_sources["duckdb_query_v3"]
    assert v3_source.startswith(static_source.rstrip() + b"\n\n")
    assert len(static_source.splitlines()) == 949
    assert len(v3_source.splitlines()) == 1105


@pytest.mark.parametrize(
    ("profile", "task_id"),
    _ALL_CASE_KEYS,
    ids=[f"{profile}:{task_id}" for profile, task_id in _ALL_CASE_KEYS],
)
def test_every_frozen_assignment_recomputes_exact_valid_output_and_identity(
    characterized_leaves: dict[tuple[str, str], _CharacterizedLeaf],
    profile: str,
    task_id: str,
) -> None:
    leaf = characterized_leaves[(profile, task_id)]
    runtime = leaf.runtime
    assignment = leaf.assignment
    database = leaf.root / "task.duckdb"

    assert _file_digest(leaf.root / "delivery_manifest.json") == assignment[
        "delivery_manifest_digest"
    ]
    assert runtime.digest_file(database) == assignment["derived_database_digest"]
    assert runtime.digest_file(database) == leaf.delivery_manifest["artifacts"][
        "task.duckdb"
    ]
    assert leaf.logical_checksum == assignment["derived_logical_checksum"]
    assert leaf.logical_checksum == leaf.delivery_manifest["public_child_snapshot"][
        "logical_checksum"
    ]

    assert len(leaf.inputs) == 160
    assert tuple(row.row_id for row in leaf.inputs) == tuple(
        f"row_{index:06d}" for index in range(1, 161)
    )
    assert {row.task_id for row in leaf.inputs} == {task_id}
    assert leaf.expected["task_id"] == task_id
    assert leaf.expected["submission_schema_version"] == leaf.config[
        "submission_schema_version"
    ]
    assert leaf.expected["method_id"] == leaf.config["method_id"]
    assert leaf.expected["status"] == "completed"
    assert len(leaf.expected["rows"]) == 160

    runtime.validate_market_metric_submission_contract(leaf.expected, leaf.config)
    runtime.verify_market_metric_submission(
        leaf.inputs, leaf.expected, leaf.config
    )


def _identity_free_input(row: Any) -> tuple[tuple[str, Any], ...]:
    return tuple(
        (field, getattr(row, field))
        for field in row.__dataclass_fields__
        if field not in {"task_id", "snapshot_id"}
    )


def test_static_v2_and_v3_match_exactly_on_every_source_market_and_result(
    characterized_leaves: dict[tuple[str, str], _CharacterizedLeaf],
) -> None:
    by_protocol = {}
    for profile in _PROFILE_NAMES:
        by_protocol[profile] = {
            (leaf.assignment["source_task_id"], leaf.assignment["target"]): leaf
            for (candidate_profile, _), leaf in characterized_leaves.items()
            if candidate_profile == profile
        }

    assert set(by_protocol["static_v2"]) == set(by_protocol["duckdb_query_v3"])
    for identity, static_leaf in by_protocol["static_v2"].items():
        v3_leaf = by_protocol["duckdb_query_v3"][identity]
        assert tuple(map(_identity_free_input, static_leaf.inputs)) == tuple(
            map(_identity_free_input, v3_leaf.inputs)
        )
        assert static_leaf.expected["rows"] == v3_leaf.expected["rows"]
        assert static_leaf.expected["method_id"] == v3_leaf.expected["method_id"]
        assert static_leaf.expected["task_id"] != v3_leaf.expected["task_id"]
        assert (
            static_leaf.expected["submission_schema_version"]
            != v3_leaf.expected["submission_schema_version"]
        )
        assert static_leaf.logical_checksum != v3_leaf.logical_checksum


@pytest.mark.parametrize(
    ("profile", "target"),
    _REPRESENTATIVE_KEYS,
    ids=[f"{profile}:{target}" for profile, target in _REPRESENTATIVE_KEYS],
)
def test_each_metric_freezes_malformed_submission_errors_and_order_policy(
    characterized_leaves: dict[tuple[str, str], _CharacterizedLeaf],
    profile: str,
    target: str,
) -> None:
    leaf = _representative_leaf(characterized_leaves, profile, target)
    runtime = leaf.runtime
    config = leaf.config
    errors = _BASELINE["exception_contract"]
    common = errors["common"]
    expected = leaf.expected
    output_field = config["output_field"]

    missing_field = deepcopy(expected)
    missing_field.pop("status")
    _assert_value_error(
        lambda: runtime.validate_market_metric_submission_contract(
            missing_field, config
        ),
        common["top_level_shape"],
    )

    wrong_identity = deepcopy(expected)
    wrong_identity["status"] = "wrong"
    _assert_value_error(
        lambda: runtime.validate_market_metric_submission_contract(
            wrong_identity, config
        ),
        common["identity"],
    )

    wrong_row_shape = deepcopy(expected)
    wrong_row_shape["rows"][0]["unexpected"] = "0.00000000"
    _assert_value_error(
        lambda: runtime.validate_market_metric_submission_contract(
            wrong_row_shape, config
        ),
        common["row_shape"],
    )

    for invalid_decimal in (True, float("nan"), float("inf"), "0.0"):
        invalid_value = deepcopy(expected)
        invalid_value["rows"][0][output_field] = invalid_decimal
        _assert_value_error(
            lambda invalid_value=invalid_value: (
                runtime.validate_market_metric_submission_contract(
                    invalid_value, config
                )
            ),
            f"{output_field} must be a canonical decimal8 string",
        )

    reordered = deepcopy(expected)
    reordered["rows"].reverse()
    duplicate = deepcopy(expected)
    missing_row = deepcopy(expected)
    missing_row["rows"].pop()
    if profile == "static_v2":
        duplicate["rows"][1] = deepcopy(duplicate["rows"][0])
        _assert_value_error(
            lambda: runtime.validate_market_metric_submission_contract(
                reordered, config
            ),
            errors["static_v2"]["row_order"],
        )
        _assert_value_error(
            lambda: runtime.validate_market_metric_submission_contract(
                duplicate, config
            ),
            errors["static_v2"]["row_order"],
        )
        _assert_value_error(
            lambda: runtime.validate_market_metric_submission_contract(
                missing_row, config
            ),
            errors["static_v2"]["row_count"],
        )
        expected_missing_cause = errors["static_v2"]["row_count"]
    else:
        runtime.validate_market_metric_submission_contract(reordered, config)
        runtime.verify_market_metric_submission(leaf.inputs, reordered, config)
        duplicate["rows"].append(deepcopy(duplicate["rows"][0]))
        _assert_value_error(
            lambda: runtime.validate_market_metric_submission_contract(
                duplicate, config
            ),
            errors["duckdb_query_v3"]["duplicate_row_id"],
        )
        runtime.validate_market_metric_submission_contract(missing_row, config)
        expected_missing_cause = errors["duckdb_query_v3"]["row_id_set"]

    wrapped = _assert_value_error(
        lambda: runtime.verify_market_metric_submission(
            leaf.inputs, missing_row, config
        ),
        common["wrapped_schema"],
    )
    assert type(wrapped.__cause__) is ValueError
    assert str(wrapped.__cause__).startswith(expected_missing_cause)

    mismatch = deepcopy(expected)
    mismatch["rows"][0][output_field] = _different_last_place(
        mismatch["rows"][0][output_field]
    )
    runtime.validate_market_metric_submission_contract(mismatch, config)
    _assert_value_error(
        lambda: runtime.verify_market_metric_submission(
            leaf.inputs, mismatch, config
        ),
        common["canonical_mismatch"],
    )


@pytest.mark.parametrize(
    ("profile", "target"),
    _REPRESENTATIVE_KEYS,
    ids=[f"{profile}:{target}" for profile, target in _REPRESENTATIVE_KEYS],
)
def test_each_metric_leaf_passes_its_verifier_after_isolated_relocation(
    characterized_leaves: dict[tuple[str, str], _CharacterizedLeaf],
    tmp_path: Path,
    profile: str,
    target: str,
) -> None:
    leaf = _representative_leaf(characterized_leaves, profile, target)
    copied_root = tmp_path / leaf.assignment["derived_task_id"]
    shutil.copytree(leaf.root, copied_root)
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(
        json.dumps(
            leaf.expected,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["BSM_GREEKS_SUBMISSION"] = str(submission_path)
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "pytest", "-q", "verifier"],
        cwd=copied_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "3 passed" in completed.stdout
    assert _REPOSITORY_ROOT not in copied_root.parents
