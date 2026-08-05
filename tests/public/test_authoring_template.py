from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


def test_quantlib_bsm_authoring_template_is_runnable(
    tmp_path: Path, repository_root: Path
) -> None:
    template_path = (
        repository_root
        / "authoring/templates/quantlib_bsm_generator.template.json"
    )
    config = load_generator_config(template_path)

    assert len(config.underlyings) == 1
    assert {item.call_put for item in config.option_templates} == {"call", "put"}

    with AuthoringPipeline(tmp_path / "template.duckdb", config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        physical_dynamics = [
            json.loads(row[0])
            for row in pipeline.connection.execute(
                """
                SELECT physical_dynamics
                FROM market.pricing_metadata
                ORDER BY valuation_date
                """
            ).fetchall()
        ]

    assert result["status"] == "COMPLETED"
    assert result["summary"]["business_date_count"] == 5
    assert result["summary"]["underlying_daily_count"] == 5
    assert result["summary"]["option_contract_count"] == 2
    assert result["summary"]["option_daily_count"] == 10
    assert len({row["drift"] for row in physical_dynamics}) > 1
    assert len({row["volatility"] for row in physical_dynamics}) > 1
    assert physical_dynamics[0]["drift_function"]["type"] == "piecewise_linear"
    assert physical_dynamics[0]["volatility_function"]["type"] == "piecewise_linear"


def test_time_functions_are_append_invariant(
    tmp_path: Path, repository_root: Path
) -> None:
    template_path = (
        repository_root
        / "authoring/templates/quantlib_bsm_generator.template.json"
    )
    raw = json.loads(template_path.read_text(encoding="utf-8"))
    one_shot_path = tmp_path / "one-shot.json"
    raw["business_days"] = 6
    one_shot_path.write_text(json.dumps(raw), encoding="utf-8")

    incremental_path = tmp_path / "incremental.json"
    raw["business_days"] = 5
    incremental_path.write_text(json.dumps(raw), encoding="utf-8")

    one_shot_database = tmp_path / "one-shot.duckdb"
    with AuthoringPipeline(
        one_shot_database, load_generator_config(one_shot_path)
    ) as pipeline:
        pipeline.create_smoke_snapshot()

    incremental_database = tmp_path / "incremental.duckdb"
    with AuthoringPipeline(
        incremental_database, load_generator_config(incremental_path)
    ) as pipeline:
        pipeline.create_smoke_snapshot()
        pipeline.append_business_days(1)

    def logical_hashes(database: Path, table: str) -> list[tuple]:
        connection = duckdb.connect(str(database), read_only=True)
        try:
            return connection.execute(
                f"SELECT row_sha256 FROM {table} ORDER BY row_sha256"
            ).fetchall()
        finally:
            connection.close()

    assert logical_hashes(
        one_shot_database, "market.underlying_daily"
    ) == logical_hashes(incremental_database, "market.underlying_daily")
    assert logical_hashes(one_shot_database, "market.option_daily") == logical_hashes(
        incremental_database, "market.option_daily"
    )


def test_time_function_definition_is_immutable_within_a_snapshot(
    tmp_path: Path, repository_root: Path
) -> None:
    template_path = (
        repository_root
        / "authoring/templates/quantlib_bsm_generator.template.json"
    )
    database = tmp_path / "immutable-function.duckdb"
    with AuthoringPipeline(database, load_generator_config(template_path)) as pipeline:
        pipeline.create_smoke_snapshot()

    raw = json.loads(template_path.read_text(encoding="utf-8"))
    raw["underlyings"][0]["physical_volatility"]["nodes"][1]["value"] = 0.31
    changed_path = tmp_path / "changed-function.json"
    changed_path.write_text(json.dumps(raw), encoding="utf-8")

    with AuthoringPipeline(database, load_generator_config(changed_path)) as pipeline:
        with pytest.raises(ValueError, match="definitions are immutable"):
            pipeline.sync_config()
