from __future__ import annotations

import ast
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    DUCKDB_QUERY_V3_PROFILE,
    bsm_metric_logical_checksum,
    digest_file,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    profiles as runtime_profiles,
    submission_static_v2,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS,
    METRIC_SPECS_DB_QUERY_V3,
    MetricSpec,
    get_metric_spec,
    get_metric_spec_db_query_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
    METRIC_VERIFIER_FILENAMES,
    expected_metric_submission_v3,
    metric_oracle_config,
    metric_oracle_config_v3,
    metric_verifier_files_v3,
    verify_market_metric_submission_v3,
    write_metric_verifier_v3,
)


def _source_database(repository_root: Path) -> Path:
    tasks = (
        repository_root
        / "task_packages/deliveries/bsm_market_implied_greeks_v1"
        / "20260813_prompt_v2_100/tasks"
    )
    candidates = sorted(tasks.glob("*/task.duckdb"))
    assert len(candidates) == 100
    return candidates[0]


def _project_database(source: Path, output: Path, spec: MetricSpec) -> None:
    shutil.copyfile(source, output)
    task_id = f"{spec.task_id_prefix}{'a' * 24}"
    connection = duckdb.connect(str(output))
    try:
        connection.execute(
            """
            UPDATE metadata.public_task
            SET schema_version = ?, task_id = ?, task_version = ?, variant_id = ?
            """,
            [
                spec.database_schema_version,
                task_id,
                spec.task_version,
                spec.variant_id,
            ],
        )
        connection.execute(
            "UPDATE solver_visible.underlying_market_inputs SET task_id = ?",
            [task_id],
        )
        connection.execute(
            "UPDATE solver_visible.option_quote_inputs SET task_id = ?",
            [task_id],
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


@pytest.fixture(scope="module")
def v3_metric_leaf_tasks(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[Path, dict]]:
    base = tmp_path_factory.mktemp("single-metric-verifiers-v3")
    repository_root = Path(__file__).resolve().parents[2]
    source = _source_database(repository_root)
    result = {}
    for target in ("delta", "iv"):
        spec = get_metric_spec_db_query_v3(target)
        root = base / target
        root.mkdir()
        database = root / "task.duckdb"
        _project_database(source, database, spec)
        write_metric_verifier_v3(root / "verifier", spec)
        config = metric_oracle_config_v3(spec)
        manifest = {
            "public_child_snapshot": {
                "logical_checksum": bsm_metric_logical_checksum(database, config)
            },
            "artifacts": {"task.duckdb": digest_file(database)},
        }
        (root / "delivery_manifest.json").write_bytes(
            canonical_json_bytes(manifest)
        )
        submission = expected_metric_submission_v3(root, config)
        result[target] = (root, submission)
    return result


@pytest.mark.parametrize("spec", METRIC_SPECS_DB_QUERY_V3, ids=lambda item: item.target)
def test_v3_oracle_identity_versions_only_the_solver_abi(spec: MetricSpec) -> None:
    legacy = get_metric_spec(spec.target)
    config = metric_oracle_config_v3(spec)

    assert spec.variant_id == legacy.variant_id
    assert spec.method_id == legacy.method_id
    assert spec.output_field == legacy.output_field
    assert spec.decimal_constraint == legacy.decimal_constraint
    assert spec.submission_schema_version.endswith("-v2.0.0")
    assert spec.verifier_id.endswith("-v2")
    assert spec.task_version == "2.0.0"
    assert spec.database_schema_version == "bsm-market-metric-task-duckdb-v2.0.0"
    assert "-v2-" in spec.task_id_pattern
    assert config["canonical_rounding"] == "ROUND_HALF_EVEN"
    assert config["canonical_precision"] == 8
    assert config["method_id"] == legacy.method_id


def test_v3_accepts_reversed_rows_and_v2_contract_stays_ordered(
    v3_metric_leaf_tasks: dict[str, tuple[Path, dict]],
) -> None:
    root, expected = v3_metric_leaf_tasks["delta"]
    reversed_submission = deepcopy(expected)
    reversed_submission["rows"].reverse()
    verify_market_metric_submission_v3(root, reversed_submission, "delta")

    legacy_spec = get_metric_spec("delta")
    legacy_config = metric_oracle_config(legacy_spec)
    legacy_submission = {
        "task_id": f"bsm-market-delta-v1-{'a' * 24}",
        "submission_schema_version": legacy_spec.submission_schema_version,
        "method_id": legacy_spec.method_id,
        "status": "completed",
        "rows": [
            {"row_id": f"row_{index:06d}", "unit_delta": "0.00000000"}
            for index in range(1, 161)
        ],
    }
    submission_static_v2.validate_market_metric_submission_contract(
        legacy_submission, legacy_config
    )
    legacy_submission["rows"].reverse()
    with pytest.raises(ValueError, match="reordered"):
        submission_static_v2.validate_market_metric_submission_contract(
            legacy_submission, legacy_config
        )


def test_v3_rejects_duplicate_missing_and_extra_row_ids(
    v3_metric_leaf_tasks: dict[str, tuple[Path, dict]],
) -> None:
    root, expected = v3_metric_leaf_tasks["delta"]

    duplicate = deepcopy(expected)
    duplicate["rows"].append(deepcopy(duplicate["rows"][0]))

    missing = deepcopy(expected)
    missing["rows"].pop()

    extra = deepcopy(expected)
    extra["rows"].append(
        {"row_id": "row_999999", "unit_delta": "0.00000000"}
    )

    replaced = deepcopy(expected)
    replaced["rows"][0]["row_id"] = "row_999999"

    for changed in (duplicate, missing, extra, replaced):
        with pytest.raises(ValueError):
            verify_market_metric_submission_v3(root, changed, "delta")


def test_v3_keeps_identity_status_fields_and_exact_strings_strict(
    v3_metric_leaf_tasks: dict[str, tuple[Path, dict]],
) -> None:
    delta_root, delta = v3_metric_leaf_tasks["delta"]
    cases = []

    for field, wrong in (
        ("task_id", f"bsm-market-delta-v2-{'b' * 24}"),
        ("submission_schema_version", "wrong"),
        ("method_id", "wrong"),
        ("status", "wrong"),
    ):
        changed = deepcopy(delta)
        changed[field] = wrong
        cases.append(changed)

    wrong_metric = deepcopy(delta)
    value = wrong_metric["rows"][0]["unit_delta"]
    wrong_metric["rows"][0]["unit_delta"] = value[:-1] + (
        "1" if value[-1] != "1" else "2"
    )
    cases.append(wrong_metric)

    wrong_row_shape = deepcopy(delta)
    wrong_row_shape["rows"][0]["unit_gamma"] = "0.00000000"
    cases.append(wrong_row_shape)

    for changed in cases:
        with pytest.raises(ValueError):
            verify_market_metric_submission_v3(delta_root, changed, "delta")

    iv_root, iv = v3_metric_leaf_tasks["iv"]
    wrong_iv_status = deepcopy(iv)
    wrong_iv_status["rows"][0]["iv_status"] = "OK"
    with pytest.raises(ValueError):
        verify_market_metric_submission_v3(iv_root, wrong_iv_status, "iv")


def test_v3_leaf_source_is_fixed_and_self_contained() -> None:
    iv_files = metric_verifier_files_v3("iv")
    delta_files = metric_verifier_files_v3("delta")
    assert tuple(iv_files) == METRIC_VERIFIER_FILENAMES
    assert iv_files["runtime.py"] == delta_files["runtime.py"]
    assert iv_files["oracle_config.json"] != delta_files["oracle_config.json"]

    python_sources = {
        name: payload.decode("utf-8")
        for name, payload in iv_files.items()
        if name.endswith(".py")
    }
    for name, source in python_sources.items():
        tree = ast.parse(source, filename=name)
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        }
        assert "synthetic_derivatives" not in imported_roots, name
        assert "isclose" not in source, name
        assert "pytest.approx" not in source, name
    submission_source = python_sources["_runtime/submission.py"]
    assert "set(rows_by_id) != set(aligned_ids)" in submission_source
    assert "actual != expected" in submission_source


def test_v3_leaf_verifier_accepts_shuffled_submission_without_project_imports(
    v3_metric_leaf_tasks: dict[str, tuple[Path, dict]],
) -> None:
    root, expected = v3_metric_leaf_tasks["delta"]
    shuffled = deepcopy(expected)
    shuffled["rows"] = shuffled["rows"][::2] + shuffled["rows"][1::2]
    submission_path = root / "submission-v3.json"
    submission_path.write_bytes(canonical_json_bytes(shuffled))

    environment = os.environ.copy()
    environment["BSM_GREEKS_SUBMISSION"] = str(submission_path)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "pytest", "-q", "verifier"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "3 passed" in completed.stdout


def test_v3_registry_and_runtime_have_all_six_exact_identities() -> None:
    assert tuple(spec.target for spec in METRIC_SPECS_DB_QUERY_V3) == tuple(
        spec.target for spec in METRIC_SPECS
    )
    for spec in METRIC_SPECS_DB_QUERY_V3:
        internal = runtime_profiles._spec_from_config(
            metric_oracle_config_v3(spec), profile=DUCKDB_QUERY_V3_PROFILE
        )
        assert internal.target == spec.target
        assert internal.method_id == spec.method_id
        assert internal.submission_schema_version == spec.submission_schema_version
        assert internal.verifier_id == spec.verifier_id
