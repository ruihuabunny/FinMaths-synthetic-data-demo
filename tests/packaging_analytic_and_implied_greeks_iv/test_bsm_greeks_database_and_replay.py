from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import load_json_object
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    assert_bsm_greeks_database_safe,
    bsm_greeks_logical_checksum,
    load_bsm_greeks_inputs,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.package import (
    build_bsm_greeks_package,
    verify_bsm_greeks_package,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    CapabilityViolation,
    replay_solver_source,
)
from synthetic_derivatives.verifier.bsm_market_greeks import (
    verify_market_greeks_submission,
)


def test_temp_directory_e2e_build_has_only_three_public_relations(
    packaged_bsm_greeks,
) -> None:
    package = packaged_bsm_greeks.package
    database = package.package_root / "public/task.duckdb"
    assert_bsm_greeks_database_safe(database)
    verify_bsm_greeks_package(
        package.package_root,
        repository_root=packaged_bsm_greeks.repository_root,
    )
    connection = duckdb.connect(str(database), read_only=True)
    try:
        relations = connection.execute(
            """
            SELECT table_schema || '.' || table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY 1
            """
        ).fetchall()
        metadata = connection.execute(
            """
            SELECT underlying_count, option_row_count,
                   p_dependence_spec_id, q_dependence_spec_id,
                   dependence_policy_id
            FROM metadata.public_task
            """
        ).fetchone()
        grids = connection.execute(
            """
            SELECT underlying_id, count(*), count(DISTINCT expiry)
            FROM solver_visible.greeks_task_inputs
            GROUP BY underlying_id ORDER BY underlying_id
            """
        ).fetchall()
    finally:
        connection.close()

    assert relations == [
        ("metadata.public_task",),
        ("solver_visible.greeks_task_contract",),
        ("solver_visible.greeks_task_inputs",),
    ]
    assert metadata[:2] == (8, 160)
    assert metadata[2].startswith("PUBLIC-P-DEPENDENCE-")
    assert metadata[3].startswith("PUBLIC-Q-DEPENDENCE-")
    assert metadata[4] == "girsanov_drift_only_same_brownian_covariance"
    assert len(grids) == 8
    assert all(row[1:] == (20, 2) for row in grids)


def test_two_independent_package_builds_are_byte_identical(
    packaged_bsm_greeks, tmp_path: Path
) -> None:
    first = packaged_bsm_greeks.package
    second = build_bsm_greeks_package(
        repository_root=packaged_bsm_greeks.repository_root,
        parent_database=packaged_bsm_greeks.parent_database,
        output_root=tmp_path / "replay",
        package_config_path=(
            packaged_bsm_greeks.repository_root
            / "configs/task_packages/bsm_market_implied_greeks_v1.json"
        ),
    )

    assert first.database_manifest.task_id == second.database_manifest.task_id
    assert first.database_manifest.public_logical_checksum == (
        second.database_manifest.public_logical_checksum
    )
    for relative in (
        "public/prompt.md",
        "public/runtime_contract.json",
        "public/submission.schema.json",
        "reference/final_submission.json",
        "reference/trajectory.jsonl",
    ):
        assert (first.package_root / relative).read_bytes() == (
            second.package_root / relative
        ).read_bytes()
    first_manifest = load_json_object(first.package_root / "manifest.json")
    second_manifest = load_json_object(second.package_root / "manifest.json")
    # DuckDB physical storage bytes are not a stable identity surface.  The
    # canonical logical checksum above freezes table contracts and values; each
    # manifest separately authenticates the physical file it actually ships.
    first_manifest["artifacts"].pop("public/task.duckdb")
    second_manifest["artifacts"].pop("public/task.duckdb")
    assert first_manifest == second_manifest


def test_reference_solver_replay_uses_exact_tool_budget_and_verifies(
    packaged_bsm_greeks, tmp_path: Path
) -> None:
    package_root = packaged_bsm_greeks.package.package_root
    runtime = load_json_object(package_root / "public/runtime_contract.json")
    result = replay_solver_source(
        source_path=package_root / "reference/artifacts/solver.py",
        database=package_root / "public/task.duckdb",
        submission_directory=tmp_path / "submission",
        runtime_contract=runtime,
    )
    expected = (package_root / "reference/final_submission.json").read_bytes()

    assert result.submission_bytes == expected
    assert result.tool_calls == {
        "query_greeks_task_contract_v1": 1,
        "query_greeks_task_inputs_v1": 1,
        "submit_greeks_submission_v1": 1,
    }
    inputs = load_bsm_greeks_inputs(package_root / "public/task.duckdb")
    verify_market_greeks_submission(inputs, result.submission)


def test_runtime_harness_enforces_dynamic_import_and_wall_clock(
    packaged_bsm_greeks, tmp_path: Path
) -> None:
    package_root = packaged_bsm_greeks.package.package_root
    database = package_root / "public/task.duckdb"
    runtime = load_json_object(package_root / "public/runtime_contract.json")
    dynamic_import = tmp_path / "dynamic_import.py"
    dynamic_import.write_text(
        "def solve(tools):\n"
        "    __builtins__['__import__']('os')\n",
        encoding="utf-8",
    )
    with pytest.raises(CapabilityViolation, match="runtime import is not allowed"):
        replay_solver_source(
            source_path=dynamic_import,
            database=database,
            submission_directory=tmp_path / "dynamic-output",
            runtime_contract=runtime,
        )

    timeout_runtime = json.loads(json.dumps(runtime))
    timeout_runtime["resource_budget"]["wall_clock_seconds"] = 1
    infinite_loop = tmp_path / "infinite_loop.py"
    infinite_loop.write_text(
        "def solve(tools):\n"
        "    while True:\n"
        "        pass\n",
        encoding="utf-8",
    )
    with pytest.raises(CapabilityViolation, match="wall-clock budget"):
        replay_solver_source(
            source_path=infinite_loop,
            database=database,
            submission_directory=tmp_path / "timeout-output",
            runtime_contract=timeout_runtime,
        )


def test_parent_has_non_diagonal_q_drivers_but_task_inputs_have_no_correlation(
    packaged_bsm_greeks,
) -> None:
    parent = duckdb.connect(
        str(packaged_bsm_greeks.parent_database), read_only=True
    )
    try:
        q_row = parent.execute(
            """
            SELECT driver_order, correlation_matrix
            FROM market.underlying_dependence WHERE measure = 'Q'
            """
        ).fetchone()
    finally:
        parent.close()
    drivers = json.loads(q_row[0])
    correlation = json.loads(q_row[1])
    input_keys = set(
        load_bsm_greeks_inputs(
            packaged_bsm_greeks.package.package_root / "public/task.duckdb"
        )[0].to_tool_mapping()
    )

    assert len(drivers) == 22
    assert all("OPTION" not in driver for driver in drivers)
    assert any(
        abs(correlation[row][column]) > 0
        for row in range(len(drivers))
        for column in range(row)
    )
    assert {"correlation", "factor_loading_matrix", "driver_order"}.isdisjoint(
        input_keys
    )


def test_database_logical_checksum_matches_manifest(packaged_bsm_greeks) -> None:
    package = packaged_bsm_greeks.package
    manifest = load_json_object(package.package_root / "manifest.json")
    assert bsm_greeks_logical_checksum(
        package.package_root / "public/task.duckdb"
    ) == manifest["public_child_snapshot"]["logical_checksum"]
