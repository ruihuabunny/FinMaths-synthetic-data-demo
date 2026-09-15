from __future__ import annotations

import ast
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    STATIC_V2_PROFILE,
    bsm_metric_logical_checksum,
    digest_file,
    load_bsm_market_metric_inputs,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_leaf_verifier_runtime import (
    profiles as runtime_profiles,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS,
    MetricSpec,
    get_metric_spec,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_verifier import (
    METRIC_VERIFIER_FILENAMES,
    expected_metric_submission,
    metric_oracle_config,
    metric_verifier_files,
    verify_market_metric_submission,
    write_metric_verifier,
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
def metric_leaf_tasks(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[Path, dict]]:
    base = tmp_path_factory.mktemp("single-metric-verifiers")
    repository_root = Path(__file__).resolve().parents[2]
    source = _source_database(repository_root)
    result = {}
    for spec in METRIC_SPECS:
        root = base / spec.target
        root.mkdir()
        database = root / "task.duckdb"
        _project_database(source, database, spec)
        write_metric_verifier(root / "verifier", spec)
        config = metric_oracle_config(spec)
        manifest = {
            "public_child_snapshot": {
                "logical_checksum": bsm_metric_logical_checksum(database, config)
            },
            "artifacts": {
                "task.duckdb": digest_file(database),
            },
        }
        (root / "delivery_manifest.json").write_bytes(
            canonical_json_bytes(manifest)
        )
        submission = expected_metric_submission(root, config)
        (root / "submission.json").write_bytes(canonical_json_bytes(submission))
        result[spec.target] = (root, submission)
    return result


def _different_last_place(value: str) -> str:
    return value[:-1] + ("1" if value[-1] != "1" else "2")


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_each_target_recomputes_canonical_truth_and_rejects_last_place_change(
    metric_leaf_tasks: dict[str, tuple[Path, dict]], spec: MetricSpec
) -> None:
    root, submission = metric_leaf_tasks[spec.target]
    verify_market_metric_submission(root, submission, spec)

    changed = deepcopy(submission)
    changed["rows"][0][spec.output_field] = _different_last_place(
        changed["rows"][0][spec.output_field]
    )
    with pytest.raises(ValueError, match="mismatch"):
        verify_market_metric_submission(root, changed, spec)

    expected_fields = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_fields.add("iv_status")
    assert set(submission["rows"][0]) == expected_fields


def test_single_metric_contract_rejects_shape_identity_and_target_attacks(
    metric_leaf_tasks: dict[str, tuple[Path, dict]],
) -> None:
    delta_root, delta = metric_leaf_tasks["delta"]
    cases = []

    missing = deepcopy(delta)
    missing["rows"].pop()
    cases.append(missing)

    duplicate = deepcopy(delta)
    duplicate["rows"][1] = deepcopy(duplicate["rows"][0])
    cases.append(duplicate)

    reordered = deepcopy(delta)
    reordered["rows"][0], reordered["rows"][1] = (
        reordered["rows"][1],
        reordered["rows"][0],
    )
    cases.append(reordered)

    extra = deepcopy(delta)
    extra["rows"][0]["unit_gamma"] = "0.01000000"
    cases.append(extra)

    top_extra = deepcopy(delta)
    top_extra["tolerance"] = "0.00000001"
    cases.append(top_extra)

    negative_zero = deepcopy(delta)
    negative_zero["rows"][0]["unit_delta"] = "-0.00000000"
    cases.append(negative_zero)

    combined = deepcopy(delta)
    for row in combined["rows"]:
        row.update(
            {
                "iv_status": "CONVERGED_FIXED_ITERATIONS",
                "market_implied_volatility": "0.20000000",
                "unit_gamma": "0.01000000",
                "unit_vega_1volpt": "0.10000000",
                "unit_theta_1calendar_day": "-0.01000000",
                "unit_rho_1pct": "0.10000000",
            }
        )
    cases.append(combined)

    for identity_field in (
        "task_id",
        "submission_schema_version",
        "method_id",
        "status",
    ):
        changed = deepcopy(delta)
        changed[identity_field] = "wrong"
        cases.append(changed)

    for changed in cases:
        with pytest.raises(ValueError):
            verify_market_metric_submission(delta_root, changed, "delta")

    gamma_root, _ = metric_leaf_tasks["gamma"]
    with pytest.raises(ValueError):
        verify_market_metric_submission(gamma_root, delta, "gamma")

    disguised_delta = deepcopy(delta)
    gamma_spec = get_metric_spec("gamma")
    disguised_delta.update(
        {
            "task_id": f"{gamma_spec.task_id_prefix}{'a' * 24}",
            "submission_schema_version": gamma_spec.submission_schema_version,
            "method_id": gamma_spec.method_id,
        }
    )
    with pytest.raises(ValueError):
        verify_market_metric_submission(gamma_root, disguised_delta, gamma_spec)

    iv_root, iv = metric_leaf_tasks["iv"]
    invalid_iv = deepcopy(iv)
    invalid_iv["rows"][0]["market_implied_volatility"] = "0.00000000"
    with pytest.raises(ValueError):
        verify_market_metric_submission(iv_root, invalid_iv, "iv")

    wrong_status = deepcopy(iv)
    wrong_status["rows"][0]["iv_status"] = "OK"
    with pytest.raises(ValueError):
        verify_market_metric_submission(iv_root, wrong_status, "iv")

    nonnegative_root, gamma = metric_leaf_tasks["gamma"]
    invalid_gamma = deepcopy(gamma)
    invalid_gamma["rows"][0]["unit_gamma"] = "-0.01000000"
    with pytest.raises(ValueError):
        verify_market_metric_submission(nonnegative_root, invalid_gamma, "gamma")


def test_metric_verifier_rejects_wrong_units_and_contract_multiplier(
    metric_leaf_tasks: dict[str, tuple[Path, dict]],
) -> None:
    scale_cases = (
        ("vega_1volpt", Decimal("100")),
        ("rho_1pct", Decimal("100")),
        ("theta_1calendar_day", Decimal("365")),
    )
    for target, scale in scale_cases:
        root, submission = metric_leaf_tasks[target]
        spec = get_metric_spec(target)
        changed = deepcopy(submission)
        changed["rows"][0][spec.output_field] = format(
            Decimal(changed["rows"][0][spec.output_field]) * scale, ".8f"
        )
        with pytest.raises(ValueError, match="mismatch"):
            verify_market_metric_submission(root, changed, spec)

    theta_root, theta = metric_leaf_tasks["theta_1calendar_day"]
    wrong_sign = deepcopy(theta)
    field = "unit_theta_1calendar_day"
    wrong_sign["rows"][0][field] = format(
        -Decimal(wrong_sign["rows"][0][field]), ".8f"
    )
    with pytest.raises(ValueError, match="mismatch"):
        verify_market_metric_submission(theta_root, wrong_sign, "theta_1calendar_day")

    delta_root, delta = metric_leaf_tasks["delta"]
    config = metric_oracle_config("delta")
    inputs = load_bsm_market_metric_inputs(
        delta_root / "task.duckdb", config
    )
    multiplied = deepcopy(delta)
    multiplied["rows"][0]["unit_delta"] = format(
        Decimal(multiplied["rows"][0]["unit_delta"])
        * Decimal(str(inputs[0].contract_multiplier)),
        ".8f",
    )
    with pytest.raises(ValueError, match="mismatch"):
        verify_market_metric_submission(delta_root, multiplied, "delta")


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_oracle_config_is_an_exact_builtin_whitelist(spec: MetricSpec) -> None:
    config = metric_oracle_config(spec)
    internal = runtime_profiles._spec_from_config(
        config, profile=STATIC_V2_PROFILE
    )
    assert internal.target == spec.target

    for field, wrong in (
        ("output_field", "unit_wrong"),
        ("method_id", "different-method"),
        ("iterations", 79),
        ("volatility_bracket", [0.0, 5.0]),
        ("canonical_rounding", "ROUND_HALF_UP"),
    ):
        changed = dict(config)
        changed[field] = wrong
        with pytest.raises(ValueError, match="different oracle config"):
            runtime_profiles._spec_from_config(
                changed, profile=STATIC_V2_PROFILE
            )


def test_leaf_templates_are_fixed_self_contained_and_use_exact_comparison() -> None:
    iv_files = metric_verifier_files("iv")
    delta_files = metric_verifier_files("delta")
    assert tuple(iv_files) == METRIC_VERIFIER_FILENAMES
    assert set(iv_files) == set(delta_files)
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
        assert "reference/final_submission" not in source, name
    assert "actual != expected" in python_sources["_runtime/submission.py"]


def test_leaf_verifier_runs_without_project_import_path(
    metric_leaf_tasks: dict[str, tuple[Path, dict]],
) -> None:
    root, _ = metric_leaf_tasks["gamma"]
    environment = os.environ.copy()
    environment["BSM_GREEKS_SUBMISSION"] = str(root / "submission.json")
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
    assert not (root / "reference").exists()
