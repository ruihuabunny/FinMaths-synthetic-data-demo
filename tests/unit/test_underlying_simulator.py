from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.option_daily_generator import OptionDailyGenerator
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline
from synthetic_derivatives.authoring.schema import SCHEMA_VERSION, initialize_schema
from synthetic_derivatives.authoring.underlying_daily_generator import (
    UnderlyingDailyGenerator,
)


def _factor_config(
    tmp_path: Path,
    smoke_config_path: Path,
    *,
    name: str = "factor-config.json",
    business_days: int = 5,
    factor_loading_matrix: list[list[float]] | None = None,
) -> Path:
    raw = json.loads(smoke_config_path.read_text(encoding="utf-8"))
    raw.update(
        {
            "schema_version": "1.2.0",
            "generator_config_id": "quantlib-bsm-underlying-factor-v1",
            "generator_version": "0.3.0",
            "snapshot_id": "DERIVATIVES-UNDERLYING-FACTOR-v1",
            "business_days": business_days,
        }
    )
    raw["underlyings"] = raw["underlyings"][:2]
    raw["option_templates"] = raw["option_templates"][:1]
    raw["underlying_simulation"] = {
        "dependence_spec_id": "SYNTH-P-SPOT-FACTOR-v1",
        "measure": "P",
        "driver_order": ["SYNTH-U01", "SYNTH-U02"],
        "formulation": "factor_loading",
        "factor_loading_matrix": (
            factor_loading_matrix
            if factor_loading_matrix is not None
            else [[0.8], [0.5]]
        ),
        "idiosyncratic_diagonal": "derive_from_row_norms",
        "matrix_dtype": "float64",
        "factorization_method": "factor_loading_direct",
        "factorization_order": "declared_driver_order",
        "time_grid": "business_daily",
        "regime_id": "constant",
    }
    path = tmp_path / name
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _rewrite(path: Path, raw: dict[str, Any], *, name: str) -> Path:
    target = path.parent / name
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def test_factor_loading_config_derives_diagonal_and_correlation_matrix(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    config = load_generator_config(_factor_config(tmp_path, smoke_config_path))
    simulation = config.underlying_simulation

    assert simulation is not None
    assert simulation.measure == "P"
    assert simulation.driver_order == ("SYNTH-U01", "SYNTH-U02")
    assert simulation.factor_loading_matrix == ((0.8,), (0.5,))
    assert simulation.idiosyncratic_diagonal == pytest.approx((0.36, 0.75))
    assert simulation.correlation_matrix == ((1.0, 0.4), (0.4, 1.0))


def test_authoring_schema_migrates_additively_from_v2(
    tmp_path: Path,
) -> None:
    connection = duckdb.connect(str(tmp_path / "schema-migration.duckdb"))
    try:
        connection.execute("CREATE SCHEMA metadata")
        connection.execute(
            """
            CREATE TABLE metadata.schema_versions (
                schema_version VARCHAR PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
            )
            """
        )
        connection.execute(
            "INSERT INTO metadata.schema_versions VALUES ('2.0.0', current_timestamp)"
        )
        connection.execute(
            """
            CREATE TABLE metadata.snapshot_revisions (
                snapshot_id VARCHAR NOT NULL,
                revision INTEGER NOT NULL,
                run_id VARCHAR NOT NULL,
                underlying_count BIGINT NOT NULL,
                option_contract_count BIGINT NOT NULL,
                underlying_daily_count BIGINT NOT NULL,
                option_daily_count BIGINT NOT NULL,
                pricing_metadata_count BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
                PRIMARY KEY (snapshot_id, revision)
            )
            """
        )

        initialize_schema(connection)

        current_version = connection.execute(
            """
            SELECT schema_version FROM metadata.schema_versions
            ORDER BY applied_at DESC, schema_version DESC LIMIT 1
            """
        ).fetchone()[0]
        dependence_table_count = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'market'
              AND table_name = 'underlying_dependence'
            """
        ).fetchone()[0]
        option_chain_table_count = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'market'
              AND table_name = 'option_chain_specs'
            """
        ).fetchone()[0]
        revision_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('metadata.snapshot_revisions')"
            ).fetchall()
        }
        option_contract_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('market.option_contracts')"
            ).fetchall()
        }
    finally:
        connection.close()

    assert current_version == SCHEMA_VERSION == "2.2.0"
    assert dependence_table_count == 1
    assert option_chain_table_count == 1
    assert "underlying_dependence_count" in revision_columns
    assert "option_chain_spec_count" in revision_columns
    assert {
        "chain_id", "listing_date", "listing_spot", "strike_moneyness"
    } <= option_contract_columns


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["underlying_simulation"].update(
                {"driver_order": ["SYNTH-U01"]}
            ),
            "driver_order must contain every underlying_id",
        ),
        (
            lambda value: value["underlying_simulation"].update(
                {"factor_loading_matrix": [[0.8], [0.5, 0.1]]}
            ),
            "must be non-ragged",
        ),
        (
            lambda value: value["underlying_simulation"].update(
                {"factor_loading_matrix": [[0.8, 0.8], [0.5, 0.1]]}
            ),
            "row norm must not exceed 1",
        ),
        (
            lambda value: value["underlying_simulation"].update(
                {"correlation_matrix": [[1.0, 0.4], [0.4, 1.0]]}
            ),
            "correlation_matrix is derived from factor loadings",
        ),
    ],
)
def test_invalid_underlying_factor_contract_is_rejected(
    tmp_path: Path,
    smoke_config_path: Path,
    mutation: Any,
    message: str,
) -> None:
    base_path = _factor_config(tmp_path, smoke_config_path)
    raw = json.loads(base_path.read_text(encoding="utf-8"))
    mutation(raw)
    invalid_path = _rewrite(base_path, raw, name="invalid-factor.json")

    with pytest.raises(ValueError, match=message):
        load_generator_config(invalid_path)


def test_underlying_close_shock_uses_factor_plus_idiosyncratic_components(
    tmp_path: Path, smoke_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_generator_config(_factor_config(tmp_path, smoke_config_path))
    generator = UnderlyingDailyGenerator(config)
    market_date = config.start_date

    def fixed_gaussian(*parts: Any) -> float:
        stream_type = parts[3]
        if stream_type == "factor":
            assert parts[4] == 0
            return 2.0
        assert stream_type == "idiosyncratic"
        return 3.0 if parts[4] == "SYNTH-U01" else -1.0

    monkeypatch.setattr(generator, "_gaussian", fixed_gaussian)

    assert generator.underlying_close_shock("SYNTH-U01", market_date) == pytest.approx(
        0.8 * 2.0 + math.sqrt(0.36) * 3.0
    )
    assert generator.underlying_close_shock("SYNTH-U02", market_date) == pytest.approx(
        0.5 * 2.0 - math.sqrt(0.75)
    )


def test_pipeline_persists_private_underlying_dependence_contract(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    config_path = _factor_config(tmp_path, smoke_config_path)
    database = tmp_path / "factor.duckdb"
    with AuthoringPipeline(database, load_generator_config(config_path)) as pipeline:
        result = pipeline.create_smoke_snapshot()
        row = pipeline.connection.execute(
            """
            SELECT measure, driver_order, factor_loading_matrix,
                   idiosyncratic_diagonal, correlation_matrix
            FROM market.underlying_dependence
            """
        ).fetchone()
        physical_dynamics = json.loads(
            pipeline.connection.execute(
                """
                SELECT physical_dynamics FROM market.pricing_metadata
                ORDER BY valuation_date, underlying_id LIMIT 1
                """
            ).fetchone()[0]
        )
        solver_view_count = pipeline.connection.execute(
            """
            SELECT count(*) FROM information_schema.views
            WHERE table_schema = 'solver_visible'
              AND table_name = 'underlying_dependence'
            """
        ).fetchone()[0]

    assert result["table_stats"]["underlying_dependence"]["inserted"] == 1
    assert result["summary"]["underlying_dependence_count"] == 1
    assert row[0] == "P"
    assert json.loads(row[1]) == ["SYNTH-U01", "SYNTH-U02"]
    assert json.loads(row[2]) == [[0.8], [0.5]]
    assert json.loads(row[3]) == pytest.approx([0.36, 0.75])
    assert json.loads(row[4]) == [[1.0, 0.4], [0.4, 1.0]]
    assert physical_dynamics["dependence_spec_id"] == "SYNTH-P-SPOT-FACTOR-v1"
    assert physical_dynamics["measure"] == "P"
    assert solver_view_count == 0


def test_checked_in_correlated_underlying_template_is_runnable(
    tmp_path: Path, repository_root: Path
) -> None:
    template = (
        repository_root
        / "authoring/templates/quantlib_bsm_correlated_underlyings.template.json"
    )
    config = load_generator_config(template)

    assert config.schema_version == "1.2.0"
    assert config.underlying_simulation is not None
    assert len(config.underlyings) == 2
    with AuthoringPipeline(tmp_path / "template.duckdb", config) as pipeline:
        result = pipeline.create_smoke_snapshot()

    assert result["summary"]["underlying_dependence_count"] == 1
    assert result["summary"]["underlying_daily_count"] == 10


def test_correlated_underlying_paths_are_append_invariant(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    one_shot_path = _factor_config(
        tmp_path, smoke_config_path, name="one-shot.json", business_days=6
    )
    incremental_path = _factor_config(
        tmp_path, smoke_config_path, name="incremental.json", business_days=5
    )
    one_shot_database = tmp_path / "one-shot.duckdb"
    incremental_database = tmp_path / "incremental.duckdb"

    with AuthoringPipeline(
        one_shot_database, load_generator_config(one_shot_path)
    ) as pipeline:
        pipeline.create_smoke_snapshot()
    with AuthoringPipeline(
        incremental_database, load_generator_config(incremental_path)
    ) as pipeline:
        pipeline.create_smoke_snapshot()
        pipeline.append_business_days(1)

    def rows(database: Path, view: str, ordering: str) -> list[tuple[Any, ...]]:
        connection = duckdb.connect(str(database), read_only=True)
        try:
            return connection.execute(
                f"SELECT * FROM solver_visible.{view} ORDER BY {ordering}"
            ).fetchall()
        finally:
            connection.close()

    assert rows(
        one_shot_database, "underlying_daily", "date, underlying_id"
    ) == rows(incremental_database, "underlying_daily", "date, underlying_id")
    assert rows(one_shot_database, "option_daily", "date, option_id") == rows(
        incremental_database, "option_daily", "date, option_id"
    )


def test_underlying_dependence_cannot_change_within_a_snapshot(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    config_path = _factor_config(tmp_path, smoke_config_path)
    database = tmp_path / "immutable-factor.duckdb"
    with AuthoringPipeline(database, load_generator_config(config_path)) as pipeline:
        pipeline.create_smoke_snapshot()

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["underlying_simulation"]["factor_loading_matrix"][0][0] = 0.7
    changed_path = _rewrite(config_path, raw, name="changed-factor.json")
    with AuthoringPipeline(database, load_generator_config(changed_path)) as pipeline:
        with pytest.raises(ValueError, match="cannot change within one snapshot"):
            pipeline.sync_config()


def test_option_pricing_does_not_consume_underlying_correlation(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    positive_path = _factor_config(
        tmp_path,
        smoke_config_path,
        name="positive-correlation.json",
        factor_loading_matrix=[[0.8], [0.5]],
    )
    negative_path = _factor_config(
        tmp_path,
        smoke_config_path,
        name="negative-correlation.json",
        factor_loading_matrix=[[-0.8], [0.5]],
    )
    positive_config = load_generator_config(positive_path)
    negative_config = load_generator_config(negative_path)
    positive = OptionDailyGenerator(positive_config)
    negative = OptionDailyGenerator(negative_config)
    assert not hasattr(positive, "underlying_close_shock")
    assert not hasattr(positive, "underlying_daily_row")
    positive_underlying = positive_config.underlyings[0]
    negative_underlying = negative_config.underlyings[0]
    positive_contract = positive.option_contract_row(
        positive_underlying, positive_config.option_templates[0], "same-run"
    )
    negative_contract = negative.option_contract_row(
        negative_underlying, negative_config.option_templates[0], "same-run"
    )
    market_date = positive_config.start_date + timedelta(days=1)

    positive_quote = positive.option_daily_row(
        positive_underlying,
        positive_contract,
        market_date,
        Decimal("100.00000000"),
        "same-run",
    )
    negative_quote = negative.option_daily_row(
        negative_underlying,
        negative_contract,
        market_date,
        Decimal("100.00000000"),
        "same-run",
    )

    assert positive_config.underlying_simulation is not None
    assert negative_config.underlying_simulation is not None
    assert (
        positive_config.underlying_simulation.correlation_matrix
        != negative_config.underlying_simulation.correlation_matrix
    )
    assert positive_quote == negative_quote
