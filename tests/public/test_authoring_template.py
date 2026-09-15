from __future__ import annotations

import json
from pathlib import Path

import duckdb

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

    def logical_rows(database: Path, view: str, ordering: str) -> list[tuple]:
        connection = duckdb.connect(str(database), read_only=True)
        try:
            return connection.execute(
                f"SELECT * FROM solver_visible.{view} ORDER BY {ordering}"
            ).fetchall()
        finally:
            connection.close()

    assert logical_rows(
        one_shot_database, "underlying_daily", "date, underlying_id"
    ) == logical_rows(
        incremental_database, "underlying_daily", "date, underlying_id"
    )
    assert logical_rows(
        one_shot_database, "option_daily", "date, option_id"
    ) == logical_rows(
        incremental_database, "option_daily", "date, option_id"
    )


def test_existing_definition_is_identified_by_config_id_and_version(
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
        result = pipeline.sync_config()

    assert result["status"] == "NOOP"
    assert result["summary"]["revision"] == 1


def test_brownian_bridge_volume_template_is_runnable(
    tmp_path: Path, repository_root: Path
) -> None:
    template_path = repository_root / (
        "authoring/templates/quantlib_bsm_brownian_bridge_volume.template.json"
    )
    config = load_generator_config(template_path)

    assert config.schema_version == "1.8.0"
    assert config.intraday_bridge is not None
    assert config.volume_model is not None
    with AuthoringPipeline(tmp_path / "bridge-template.duckdb", config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        pipeline.validate()

    assert result["summary"]["intraday_bridge_spec_count"] == 1
    assert result["summary"]["underlying_volume_model_count"] == 1
