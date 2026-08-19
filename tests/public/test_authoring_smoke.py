from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


def _create(database: Path, config_path: Path) -> dict:
    config = load_generator_config(config_path)
    with AuthoringPipeline(database, config) as pipeline:
        return pipeline.create_smoke_snapshot()


def _rows(database: Path, view: str, ordering: str) -> list[tuple]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return connection.execute(
            f"SELECT * FROM solver_visible.{view} ORDER BY {ordering}"
        ).fetchall()
    finally:
        connection.close()


def _expanded_config(
    source: Path, target: Path, *, add_underlying: bool = False, add_option: bool = False
) -> Path:
    config = json.loads(source.read_text(encoding="utf-8"))
    if add_underlying:
        config["underlyings"].append(
            {
                "underlying_id": "SYNTH-U06",
                "initial_spot": 130.0,
                "physical_drift": 0.085,
                "physical_volatility": 0.28,
                "risk_free_rate": 0.0375,
                "dividend_yield": 0.0175,
                "base_implied_volatility": 0.29,
            }
        )
    if add_option:
        config["option_templates"].append(
            {
                "template_id": "PUT-115-240D",
                "call_put": "put",
                "strike_moneyness": 1.15,
                "expiry_days": 240,
                "exercise_style": "european",
                "settlement_type": "cash",
                "contract_multiplier": 100,
            }
        )
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def test_five_underlyings_five_options_five_days_smoke(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "smoke.duckdb"
    result = _create(database, smoke_config_path)

    assert result["status"] == "COMPLETED"
    assert result["summary"]["underlying_count"] == 5
    assert result["summary"]["option_contract_count"] == 25
    assert result["summary"]["business_date_count"] == 5
    assert result["summary"]["underlying_daily_count"] == 25
    assert result["summary"]["option_daily_count"] == 125
    assert result["summary"]["pricing_metadata_count"] == 25
    manifest = json.loads(database.with_suffix(".manifest.json").read_text())
    assert manifest["snapshot_id"] == result["summary"]["snapshot_id"]
    assert manifest["revision"] == result["summary"]["revision"] == 1

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute(
            "SELECT count(DISTINCT call_put) FROM market.option_contracts"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT bool_and(bid <= mid AND mid <= ask) FROM market.option_daily"
        ).fetchone()[0]
        assert connection.execute(
            """
            SELECT bool_and(
                spot_open = round(spot_open, 2)
                AND spot_high = round(spot_high, 2)
                AND spot_low = round(spot_low, 2)
                AND spot_close = round(spot_close, 2)
                AND adjusted_close = round(adjusted_close, 2)
            )
            FROM market.underlying_daily
            """
        ).fetchone()[0]
        assert connection.execute(
            """
            SELECT bool_and(
                bid = round(bid, 2)
                AND ask = round(ask, 2)
                AND mid = round(mid, 2)
                AND settlement_price = round(settlement_price, 2)
            )
            FROM market.option_daily
            """
        ).fetchone()[0]
        assert connection.execute(
            "SELECT count(DISTINCT pricing_engine) FROM market.pricing_metadata"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_rerun_is_a_noop(tmp_path: Path, smoke_config_path: Path) -> None:
    database = tmp_path / "noop.duckdb"
    _create(database, smoke_config_path)
    second = _create(database, smoke_config_path)

    assert second["status"] == "NOOP"
    assert second["summary"]["revision"] == 1
    assert all(
        stats["inserted"] == 0 for stats in second["table_stats"].values()
    )


def test_append_one_day_only_inserts_one_daily_slice(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "append.duckdb"
    _create(database, smoke_config_path)
    old_underlying_rows = _rows(database, "underlying_daily", "date, underlying_id")
    old_option_rows = _rows(database, "option_daily", "date, option_id")

    config = load_generator_config(smoke_config_path)
    with AuthoringPipeline(database, config) as pipeline:
        result = pipeline.append_business_days(1)

    assert result["table_stats"]["underlying_daily"]["inserted"] == 5
    assert result["table_stats"]["option_daily"]["inserted"] == 25
    assert result["table_stats"]["pricing_metadata"]["inserted"] == 5
    assert result["summary"]["underlying_daily_count"] == 30
    assert result["summary"]["option_daily_count"] == 150
    assert set(old_underlying_rows) <= set(
        _rows(database, "underlying_daily", "date, underlying_id")
    )
    assert set(old_option_rows) <= set(
        _rows(database, "option_daily", "date, option_id")
    )


def test_add_underlying_backfills_only_the_new_entity(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "add-underlying.duckdb"
    _create(database, smoke_config_path)
    old_daily_rows = _rows(database, "underlying_daily", "date, underlying_id")
    expanded_path = _expanded_config(
        smoke_config_path, tmp_path / "with-u06.json", add_underlying=True
    )

    with AuthoringPipeline(database, load_generator_config(expanded_path)) as pipeline:
        result = pipeline.sync_config()

    assert result["table_stats"]["underlyings"]["inserted"] == 1
    assert result["table_stats"]["option_contracts"]["inserted"] == 5
    assert result["table_stats"]["underlying_daily"]["inserted"] == 5
    assert result["table_stats"]["option_daily"]["inserted"] == 25
    assert result["summary"]["underlying_count"] == 6
    assert result["summary"]["underlying_daily_count"] == 30
    assert set(old_daily_rows) <= set(
        _rows(database, "underlying_daily", "date, underlying_id")
    )


def test_add_option_backfills_only_the_new_contract_family(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "add-option.duckdb"
    _create(database, smoke_config_path)
    old_option_rows = _rows(database, "option_daily", "date, option_id")
    expanded_path = _expanded_config(
        smoke_config_path, tmp_path / "with-option.json", add_option=True
    )

    with AuthoringPipeline(database, load_generator_config(expanded_path)) as pipeline:
        result = pipeline.sync_config()

    assert result["table_stats"]["option_contracts"]["inserted"] == 5
    assert result["table_stats"]["underlying_daily"]["inserted"] == 0
    assert result["table_stats"]["option_daily"]["inserted"] == 25
    assert result["summary"]["option_contract_count"] == 30
    assert result["summary"]["option_daily_count"] == 150
    assert set(old_option_rows) <= set(
        _rows(database, "option_daily", "date, option_id")
    )


def test_frozen_snapshot_rejects_incremental_writes(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "frozen.duckdb"
    _create(database, smoke_config_path)
    config = load_generator_config(smoke_config_path)
    with AuthoringPipeline(database, config) as pipeline:
        frozen = pipeline.freeze()
        assert frozen["status"] == "FROZEN"
        run_count = pipeline.connection.execute(
            "SELECT count(*) FROM metadata.generation_runs"
        ).fetchone()[0]
        revision = pipeline.connection.execute(
            "SELECT current_revision FROM metadata.snapshots"
        ).fetchone()[0]
        with pytest.raises(RuntimeError, match="FROZEN"):
            pipeline.append_business_days(1)
        assert pipeline.connection.execute(
            "SELECT count(*) FROM metadata.generation_runs"
        ).fetchone()[0] == run_count
        assert pipeline.connection.execute(
            "SELECT current_revision FROM metadata.snapshots"
        ).fetchone()[0] == revision


def test_config_18_bridge_volume_snapshot_is_runnable_private_and_validated(
    tmp_path: Path, repository_root: Path
) -> None:
    config = load_generator_config(
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v3.json"
    )
    config = replace(config, business_days=2, option_templates=config.option_templates[:2])
    database = tmp_path / "bridge-volume-v3.duckdb"
    with AuthoringPipeline(database, config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        validated = pipeline.validate()
        private_counts = pipeline.connection.execute(
            """
            SELECT
                (SELECT count(*) FROM market.intraday_bridge_specs),
                (SELECT count(*) FROM market.underlying_volume_models)
            """
        ).fetchone()
        solver_relations = {
            row[0]
            for row in pipeline.connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'solver_visible'
                """
            ).fetchall()
        }
        solver_metadata = pipeline.connection.execute(
            """
            SELECT physical_dynamics, seed, rng
            FROM solver_visible.pricing_metadata
            ORDER BY underlying_id LIMIT 1
            """
        ).fetchone()

    assert config.intraday_bridge is not None
    assert config.volume_model is not None
    assert result["summary"]["intraday_bridge_spec_count"] == 1
    assert result["summary"]["underlying_volume_model_count"] == 22
    assert validated["underlying_daily_count"] == 44
    assert private_counts == (1, 22)
    assert "intraday_bridge_specs" not in solver_relations
    assert "underlying_volume_models" not in solver_relations
    assert config.intraday_bridge.bridge_spec_id not in solver_metadata[0]
    assert config.volume_model.volume_spec_id not in solver_metadata[0]
    assert solver_metadata[1:] == (None, None)

    manifest_text = database.with_suffix(".manifest.json").read_text(
        encoding="utf-8"
    )
    manifest = json.loads(manifest_text)
    assert manifest["intraday_bridge_spec_count"] == 1
    assert manifest["underlying_volume_model_count"] == 22
    for private_marker in (
        "seed",
        "rng",
        "bridge_spec_id",
        "volume_spec_id",
        "stream_namespace",
        "base_volume",
        "volume_log_stddev",
        config.intraday_bridge.bridge_spec_id,
        config.volume_model.volume_spec_id,
    ):
        assert private_marker not in manifest_text


def test_config_18_one_shot_and_append_have_exact_logical_rows(
    tmp_path: Path, repository_root: Path
) -> None:
    template = repository_root / (
        "authoring/templates/quantlib_bsm_brownian_bridge_volume.template.json"
    )
    config = load_generator_config(template)
    one_shot = replace(config, business_days=3, option_templates=config.option_templates[:2])
    incremental = replace(
        config, business_days=2, option_templates=config.option_templates[:2]
    )
    one_shot_database = tmp_path / "bridge-one-shot.duckdb"
    incremental_database = tmp_path / "bridge-incremental.duckdb"
    with AuthoringPipeline(one_shot_database, one_shot) as pipeline:
        pipeline.create_smoke_snapshot()
    with AuthoringPipeline(incremental_database, incremental) as pipeline:
        pipeline.create_smoke_snapshot()
        pipeline.append_business_days(1)
        pipeline.validate()

    assert _rows(
        one_shot_database, "underlying_daily", "date, underlying_id"
    ) == _rows(
        incremental_database, "underlying_daily", "date, underlying_id"
    )
    assert _rows(one_shot_database, "option_daily", "date, option_id") == _rows(
        incremental_database, "option_daily", "date, option_id"
    )


def test_config_18_validate_detects_private_specs_and_ohlcv_tampering(
    tmp_path: Path, repository_root: Path
) -> None:
    template = repository_root / (
        "authoring/templates/quantlib_bsm_brownian_bridge_volume.template.json"
    )
    config = replace(
        load_generator_config(template),
        business_days=2,
        option_templates=load_generator_config(template).option_templates[:1],
    )
    database = tmp_path / "bridge-tamper.duckdb"
    with AuthoringPipeline(database, config) as pipeline:
        pipeline.create_smoke_snapshot()
        pipeline.connection.execute(
            "UPDATE market.intraday_bridge_specs SET steps = steps + 1"
        )
        with pytest.raises(ValueError, match="intraday bridge contract conflicts"):
            pipeline.validate()
        pipeline.connection.execute(
            "UPDATE market.intraday_bridge_specs SET steps = 64"
        )

        pipeline.connection.execute(
            "UPDATE market.underlying_volume_models SET base_volume = base_volume + 1"
        )
        with pytest.raises(
            ValueError, match="underlying volume model contract conflicts"
        ):
            pipeline.validate()
        pipeline.connection.execute(
            "UPDATE market.underlying_volume_models SET base_volume = 1000000"
        )

        pipeline.connection.execute(
            """
            UPDATE market.underlying_daily
            SET spot_high = spot_high + 0.01
            WHERE date > ?
            """,
            [config.start_date],
        )
        with pytest.raises(ValueError, match="exact config-1.8 replay"):
            pipeline.validate()
