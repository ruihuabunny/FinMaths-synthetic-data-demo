from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import shutil
from typing import Any

import duckdb
import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline
from synthetic_derivatives.export import (
    SolverDatabaseExportContract,
    assert_public_database_safe,
    canonical_authoring_logical_checksum,
    canonical_logical_checksum,
    export_solver_database,
    open_solver_database,
)


def _write_joint_parent_config(target: Path) -> Path:
    repository_root = Path(__file__).resolve().parents[2]
    source = (
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json"
    )
    raw: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    raw.update(
        {
            "schema_version": "1.7.0",
            "generator_config_id": "quantlib-bsm-public-export-integration-v1",
            "generator_version": "0.9.0",
            "snapshot_id": "DERIVATIVES-PUBLIC-EXPORT-INTEGRATION-v1",
            "business_days": 2,
        }
    )
    physical = raw.pop("underlying_simulation")
    physical["dependence_spec_id"] = "PUBLIC-EXPORT-P-SPOT-FACTOR-v1"
    pricing = deepcopy(physical)
    pricing.update(
        {
            "dependence_spec_id": "PUBLIC-EXPORT-Q-SPOT-FACTOR-v1",
            "measure": "Q",
            "source_dependence_spec_id": physical["dependence_spec_id"],
            "mapping_id": "PUBLIC-EXPORT-GIRSANOV-COVARIANCE-v1",
            "mapping_type": "girsanov_drift_only_same_brownian_covariance",
            "risk_neutral_measure_id": raw["q_pricing"][
                "risk_neutral_measure_id"
            ],
            "numeraire_id": raw["q_pricing"]["numeraire_id"],
            "rate_path_id": raw["q_pricing"]["rate_path_id"],
        }
    )
    raw["underlying_dependence_specs"] = [physical, pricing]
    target.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


@pytest.fixture(scope="module")
def frozen_joint_parent(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, date]:
    root = tmp_path_factory.mktemp("solver-export-parent")
    config = load_generator_config(_write_joint_parent_config(root / "parent.json"))
    parent = root / "parent.duckdb"
    with AuthoringPipeline(parent, config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        frozen = pipeline.freeze()
    assert result["summary"]["underlying_count"] == 22
    assert frozen["status"] == "FROZEN"
    return parent, config.start_date


def _public_contract(valuation_date: date, seed: int = 17) -> SolverDatabaseExportContract:
    return SolverDatabaseExportContract(
        variant_id="bsm_market_implied_greeks_v1",
        valuation_date=valuation_date,
        sampling_seed=seed,
    )


def test_frozen_parent_exports_a_replayable_public_only_child(
    frozen_joint_parent: tuple[Path, date], tmp_path: Path
) -> None:
    parent, valuation_date = frozen_joint_parent
    parent_bytes = parent.read_bytes()
    parent_checksum = canonical_authoring_logical_checksum(parent)
    first_path = tmp_path / "first.duckdb"
    second_path = tmp_path / "second.duckdb"

    first = export_solver_database(
        parent, first_path, _public_contract(valuation_date)
    )
    second = export_solver_database(
        parent, second_path, _public_contract(valuation_date)
    )

    assert parent.read_bytes() == parent_bytes
    assert canonical_authoring_logical_checksum(parent) == parent_checksum
    assert first.task_id == second.task_id
    assert first.sample_id == second.sample_id
    assert first.child_snapshot_id == second.child_snapshot_id
    assert first.selected_underlyings == second.selected_underlyings
    assert first.selected_option_ids == second.selected_option_ids
    assert first.public_logical_checksum == second.public_logical_checksum
    assert canonical_logical_checksum(first_path) == first.public_logical_checksum
    assert canonical_logical_checksum(second_path) == second.public_logical_checksum
    assert first.table_row_counts == {
        "metadata.public_task": 1,
        "solver_visible.option_chain_quotes": 448,
        "solver_visible.option_contracts": 448,
        "solver_visible.pricing_context": 8,
        "solver_visible.underlying_dependence": 2,
        "solver_visible.underlying_state": 8,
    }
    assert len(first.selected_underlyings) == 8
    assert len(first.selected_option_ids) == 448
    assert "sampling_seed" not in json.dumps(first.to_dict(), sort_keys=True)
    assert_public_database_safe(first_path)

    connection = duckdb.connect(str(first_path), read_only=True)
    try:
        relations = connection.execute(
            """
            SELECT table_schema || '.' || table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_schema, table_name
            """
        ).fetchall()
        assert relations == [
            ("metadata.public_task",),
            ("solver_visible.option_chain_quotes",),
            ("solver_visible.option_contracts",),
            ("solver_visible.pricing_context",),
            ("solver_visible.underlying_dependence",),
            ("solver_visible.underlying_state",),
        ]
        assert connection.execute(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'market'"
        ).fetchone()[0] == 0
        public_sql = "\n".join(
            str(row[0])
            for row in connection.execute(
                "SELECT sql FROM duckdb_tables() ORDER BY schema_name, table_name"
            ).fetchall()
        )
        assert str(parent.resolve()) not in public_sql
        assert "authoring_parent" not in public_sql
        quote_description = [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "DESCRIBE solver_visible.option_chain_quotes"
            ).fetchall()
        ]
        assert quote_description == [
            ("snapshot_id", "VARCHAR"),
            ("valuation_date", "DATE"),
            ("underlying_id", "VARCHAR"),
            ("option_id", "VARCHAR"),
            ("bid", "DECIMAL(24,8)"),
            ("ask", "DECIMAL(24,8)"),
        ]
        columns = {name for name, _ in quote_description}
        assert {"mid", "settlement_price", "implied_volatility", "d1", "d2"}.isdisjoint(
            columns
        )
        canonicalization = json.loads(
            connection.execute(
                "SELECT canonicalization FROM metadata.public_task"
            ).fetchone()[0]
        )
        assert canonicalization["row_order"][
            "solver_visible.option_chain_quotes"
        ] == ["valuation_date", "underlying_id", "option_id"]
        dependence = connection.execute(
            """
            SELECT measure, source_dependence_spec_id, dependence_spec_id,
                   driver_order, factor_loading_matrix,
                   idiosyncratic_diagonal, correlation_matrix
            FROM solver_visible.underlying_dependence
            ORDER BY measure
            """
        ).fetchall()
        assert [row[0] for row in dependence] == ["P", "Q"]
        assert dependence[1][1] == dependence[0][2]
        assert dependence[0][3:] == dependence[1][3:]
        drivers = json.loads(dependence[0][3])
        loadings = json.loads(dependence[0][4])
        diagonal = json.loads(dependence[0][5])
        correlation = json.loads(dependence[0][6])
        reconstructed = [
            [
                sum(left * right for left, right in zip(loadings[row], loadings[column]))
                + (diagonal[row] if row == column else 0.0)
                for column in range(len(drivers))
            ]
            for row in range(len(drivers))
        ]
        assert drivers == list(first.selected_underlyings)
        assert reconstructed == correlation
    finally:
        connection.close()

    with open_solver_database(first_path) as solver_connection:
        assert solver_connection.execute(
            "SELECT count(*) FROM solver_visible.option_chain_quotes"
        ).fetchone()[0] == 448
        with pytest.raises(duckdb.Error, match="read-only"):
            solver_connection.execute(
                "UPDATE solver_visible.option_chain_quotes SET ask = ask"
            )


def test_logical_checksum_ignores_file_mtime_but_detects_a_public_value_change(
    frozen_joint_parent: tuple[Path, date], tmp_path: Path
) -> None:
    parent, valuation_date = frozen_joint_parent
    public_path = tmp_path / "public.duckdb"
    export_solver_database(parent, public_path, _public_contract(valuation_date))
    baseline = canonical_logical_checksum(public_path)

    os.utime(public_path, (1_700_000_000, 1_700_000_000))
    assert canonical_logical_checksum(public_path) == baseline

    changed_path = tmp_path / "changed.duckdb"
    shutil.copyfile(public_path, changed_path)
    connection = duckdb.connect(str(changed_path))
    try:
        option_id = connection.execute(
            """
            SELECT option_id FROM solver_visible.option_chain_quotes
            ORDER BY valuation_date, underlying_id, option_id LIMIT 1
            """
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE solver_visible.option_chain_quotes
            SET ask = ask + CAST('0.00000001' AS DECIMAL(24, 8))
            WHERE option_id = ?
            """,
            [option_id],
        )
    finally:
        connection.close()

    assert_public_database_safe(changed_path)
    assert canonical_logical_checksum(changed_path) != baseline

    leaked_path = tmp_path / "nested-leak.duckdb"
    shutil.copyfile(public_path, leaked_path)
    connection = duckdb.connect(str(leaked_path))
    try:
        subset = json.loads(
            connection.execute(
                "SELECT subset_manifest FROM metadata.public_task"
            ).fetchone()[0]
        )
        subset["wrapper"] = {"deeper": {"samplingSeed": 17}}
        connection.execute(
            "UPDATE metadata.public_task SET subset_manifest = ?",
            [json.dumps(subset, sort_keys=True)],
        )
    finally:
        connection.close()
    with pytest.raises(ValueError, match="private field leaked"):
        assert_public_database_safe(leaked_path)


def test_explicit_option_selector_copies_only_declared_task_rows(
    frozen_joint_parent: tuple[Path, date], tmp_path: Path
) -> None:
    parent, valuation_date = frozen_joint_parent
    full_path = tmp_path / "full.duckdb"
    export_solver_database(parent, full_path, _public_contract(valuation_date))
    connection = duckdb.connect(str(full_path), read_only=True)
    try:
        option_ids = tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT option_id
                FROM (
                    SELECT option_id, underlying_id,
                           row_number() OVER (
                               PARTITION BY underlying_id ORDER BY option_id
                           ) AS rank
                    FROM solver_visible.option_contracts
                )
                WHERE rank = 1
                ORDER BY option_id
                """
            ).fetchall()
        )
    finally:
        connection.close()
    subset_path = tmp_path / "explicit-subset.duckdb"
    manifest = export_solver_database(
        parent,
        subset_path,
        SolverDatabaseExportContract(
            variant_id="bsm_market_implied_greeks_v1",
            valuation_date=valuation_date,
            sampling_seed=17,
            option_ids=option_ids,
        ),
    )

    assert manifest.selected_option_ids == tuple(sorted(option_ids))
    assert manifest.table_row_counts == {
        "metadata.public_task": 1,
        "solver_visible.option_chain_quotes": 8,
        "solver_visible.option_contracts": 8,
        "solver_visible.pricing_context": 8,
        "solver_visible.underlying_dependence": 2,
        "solver_visible.underlying_state": 8,
    }
    assert_public_database_safe(subset_path)


def test_export_identity_changes_with_private_selection_seed_and_never_overwrites(
    frozen_joint_parent: tuple[Path, date], tmp_path: Path
) -> None:
    parent, valuation_date = frozen_joint_parent
    first_path = tmp_path / "seed-17.duckdb"
    second_path = tmp_path / "seed-18.duckdb"
    first = export_solver_database(
        parent, first_path, _public_contract(valuation_date, seed=17)
    )
    second = export_solver_database(
        parent, second_path, _public_contract(valuation_date, seed=18)
    )

    assert first.sample_id != second.sample_id
    assert first.task_id != second.task_id
    assert first.child_snapshot_id != second.child_snapshot_id
    assert first.selected_underlyings != second.selected_underlyings
    with pytest.raises(FileExistsError, match="overwrite"):
        export_solver_database(parent, first_path, _public_contract(valuation_date))

    draft_parent = tmp_path / "draft-parent.duckdb"
    shutil.copyfile(parent, draft_parent)
    connection = duckdb.connect(str(draft_parent))
    try:
        connection.execute("UPDATE metadata.snapshots SET status = 'DRAFT'")
    finally:
        connection.close()
    rejected_output = tmp_path / "rejected.duckdb"
    with pytest.raises(ValueError, match="FROZEN"):
        export_solver_database(
            draft_parent, rejected_output, _public_contract(valuation_date)
        )
    assert not rejected_output.exists()
