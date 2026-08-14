from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

import duckdb
import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    digest_file,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    BSM_GREEKS_PUBLIC_TABLES,
    assert_bsm_greeks_database_safe,
    assert_bsm_metric_database_safe,
    bsm_metric_logical_checksum,
    load_bsm_metric_query_payloads,
    market_content_digest,
    market_content_projection,
    project_bsm_metric_database,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS,
    MetricSpec,
    get_metric_spec,
)


def _source_database(packaged_bsm_greeks) -> Path:
    return packaged_bsm_greeks.package.package_root / "public/task.duckdb"


def _rows(database: Path, table_index: int) -> tuple[dict[str, object], ...]:
    table = BSM_GREEKS_PUBLIC_TABLES[table_index]
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return tuple(
            dict(zip(table.column_names, row, strict=True))
            for row in connection.execute(
                f"SELECT * FROM {table.name} ORDER BY {','.join(table.order_by)}"
            ).fetchall()
        )
    finally:
        connection.close()


def _derived_identity(spec: MetricSpec, suffix_source: str) -> tuple[str, str]:
    suffix = sha256(suffix_source.encode("utf-8")).hexdigest()[:24]
    return (
        spec.task_id_prefix + suffix,
        "BSM-MARKET-METRIC-" + suffix.upper(),
    )


def test_metric_projection_changes_only_allowlisted_identity_columns(
    packaged_bsm_greeks,
    tmp_path: Path,
) -> None:
    source = _source_database(packaged_bsm_greeks)
    spec = get_metric_spec("delta")
    task_id, snapshot_id = _derived_identity(spec, "delta-projection")
    derived = tmp_path / "delta.duckdb"

    result = project_bsm_metric_database(
        source,
        derived,
        spec,
        task_id,
        snapshot_id,
    )

    assert_bsm_greeks_database_safe(source)
    assert_bsm_metric_database_safe(
        derived,
        spec,
        expected_task_id=task_id,
        expected_snapshot_id=snapshot_id,
    )
    assert result.source_database_file_digest == digest_file(source)
    assert result.derived_database_file_digest == digest_file(derived)
    assert result.source_market_content_digest == market_content_digest(source)
    assert result.derived_market_content_digest == market_content_digest(derived)
    assert result.source_market_content_digest == result.derived_market_content_digest
    assert market_content_projection(source) == market_content_projection(derived)
    assert result.derived_logical_checksum == bsm_metric_logical_checksum(
        derived,
        spec,
        expected_task_id=task_id,
        expected_snapshot_id=snapshot_id,
    )
    underlyings, options = load_bsm_metric_query_payloads(derived, spec)
    assert len(underlyings) == 8
    assert len(options) == 160
    assert {row["task_id"] for row in underlyings} == {task_id}
    assert {row["task_id"] for row in options} == {task_id}
    assert [row["row_id"] for row in options] == [
        f"row_{index:06d}" for index in range(1, 161)
    ]

    source_metadata = _rows(source, 0)[0]
    derived_metadata = _rows(derived, 0)[0]
    assert {
        name
        for name in source_metadata
        if source_metadata[name] != derived_metadata[name]
    } == {
        "schema_version",
        "task_id",
        "task_version",
        "variant_id",
        "snapshot_id",
    }

    for table_index, identity_columns in (
        (1, {"task_id", "snapshot_id"}),
        (2, {"task_id", "snapshot_id"}),
    ):
        source_rows = _rows(source, table_index)
        derived_rows = _rows(derived, table_index)
        assert len(source_rows) == len(derived_rows)
        for source_row, derived_row in zip(source_rows, derived_rows, strict=True):
            assert {
                name
                for name in source_row
                if source_row[name] != derived_row[name]
            } == identity_columns
            assert derived_row["task_id"] == task_id
            assert derived_row["snapshot_id"] == snapshot_id


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_metric_database_validation_and_checksum_are_target_aware(
    packaged_bsm_greeks,
    tmp_path: Path,
    spec: MetricSpec,
) -> None:
    source = _source_database(packaged_bsm_greeks)
    task_id, snapshot_id = _derived_identity(spec, spec.target)
    derived = tmp_path / f"{spec.target}.duckdb"
    result = project_bsm_metric_database(
        source,
        derived,
        spec,
        task_id,
        snapshot_id,
    )

    assert result.target == spec.target
    assert_bsm_metric_database_safe(derived, spec)
    assert bsm_metric_logical_checksum(derived, spec) == (
        result.derived_logical_checksum
    )
    wrong_spec = next(item for item in METRIC_SPECS if item.target != spec.target)
    with pytest.raises(ValueError, match="metadata identity is invalid"):
        assert_bsm_metric_database_safe(derived, wrong_spec)


def test_market_and_logical_digests_detect_economic_value_change(
    packaged_bsm_greeks,
    tmp_path: Path,
) -> None:
    source = _source_database(packaged_bsm_greeks)
    spec = get_metric_spec("gamma")
    task_id, snapshot_id = _derived_identity(spec, "changed-economic-value")
    derived = tmp_path / "gamma.duckdb"
    project_bsm_metric_database(
        source,
        derived,
        spec,
        task_id,
        snapshot_id,
    )
    baseline_market = market_content_digest(derived)
    baseline_logical = bsm_metric_logical_checksum(derived, spec)

    changed = tmp_path / "gamma-changed.duckdb"
    shutil.copyfile(derived, changed)
    connection = duckdb.connect(str(changed))
    try:
        connection.execute(
            "UPDATE solver_visible.underlying_market_inputs "
            "SET spot = spot + 0.01000000 "
            "WHERE underlying_id = ("
            "SELECT min(underlying_id) "
            "FROM solver_visible.underlying_market_inputs)"
        )
    finally:
        connection.close()

    assert_bsm_metric_database_safe(changed, spec)
    assert market_content_digest(changed) != baseline_market
    assert bsm_metric_logical_checksum(changed, spec) != baseline_logical
