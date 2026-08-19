from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import duckdb

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline
from synthetic_derivatives.authoring.schema import SCHEMA_VERSION


EXPECTED_UNDERLYING_FIELDS = {
    "date",
    "underlying_id",
    "spot_open",
    "spot_high",
    "spot_low",
    "spot_close",
    "adjusted_close",
    "volume",
    "dividend",
    "corporate_action",
}

EXPECTED_OPTION_FIELDS = {
    "date",
    "underlying_id",
    "option_id",
    "call_put",
    "strike",
    "expiry",
    "exercise_style",
    "settlement_type",
    "contract_multiplier",
    "bid",
    "ask",
    "mid",
    "settlement_price",
    "volume",
    "open_interest",
}

EXPECTED_METADATA_FIELDS = {
    "valuation_timestamp",
    "currency",
    "discount_curve",
    "risk_free_rate",
    "dividend_curve",
    "dividend_yield",
    "borrow_or_carry_rate",
    "calendar",
    "day_count",
    "physical_dynamics",
    "pricing_dynamics",
    "pricing_model",
    "pricing_engine",
    "generator_version",
    "seed",
    "rng",
    "input_precision",
    "canonicalization",
}

EXPECTED_DEPENDENCE_FIELDS = {
    "snapshot_id",
    "dependence_spec_id",
    "measure",
    "source_dependence_spec_id",
    "mapping_id",
    "mapping_type",
    "risk_neutral_measure_id",
    "numeraire_id",
    "rate_path_id",
    "driver_order",
    "formulation",
    "factor_loading_matrix",
    "idiosyncratic_diagonal",
    "correlation_matrix",
    "matrix_dtype",
    "factorization_method",
    "factorization_order",
    "time_grid",
    "regime_id",
}


def _columns(connection: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()}


def test_solver_visible_views_cover_framework_fields(
    tmp_path: Path, smoke_config_path: Path
) -> None:
    database = tmp_path / "schema.duckdb"
    with AuthoringPipeline(database, load_generator_config(smoke_config_path)) as pipeline:
        pipeline.create_smoke_snapshot()

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert EXPECTED_UNDERLYING_FIELDS <= _columns(
            connection, "solver_visible.underlying_daily"
        )
        assert EXPECTED_OPTION_FIELDS <= _columns(
            connection, "solver_visible.option_daily"
        )
        assert EXPECTED_METADATA_FIELDS <= _columns(
            connection, "solver_visible.pricing_metadata"
        )
        dependence_columns = _columns(
            connection, "solver_visible.underlying_dependence"
        )
        assert EXPECTED_DEPENDENCE_FIELDS == dependence_columns
        assert {"seed", "generator_config_id", "created_run_id"}.isdisjoint(
            dependence_columns
        )
    finally:
        connection.close()


def test_schema_26_has_private_observation_tables_without_public_views(
    tmp_path: Path, repository_root: Path
) -> None:
    config = load_generator_config(
        repository_root / (
            "authoring/templates/quantlib_bsm_brownian_bridge_volume.template.json"
        )
    )
    database = tmp_path / "schema-26-observation.duckdb"
    with AuthoringPipeline(database, replace(config, business_days=2)) as pipeline:
        pipeline.create_smoke_snapshot()

    connection = duckdb.connect(str(database), read_only=True)
    try:
        schema_version = connection.execute(
            """
            SELECT schema_version FROM metadata.schema_versions
            ORDER BY applied_at DESC, schema_version DESC LIMIT 1
            """
        ).fetchone()[0]
        bridge_columns = _columns(connection, "market.intraday_bridge_specs")
        volume_columns = _columns(connection, "market.underlying_volume_models")
        revision_columns = _columns(connection, "metadata.snapshot_revisions")
        solver_relations = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'solver_visible'
                """
            ).fetchall()
        }
    finally:
        connection.close()

    assert schema_version == SCHEMA_VERSION == "2.6.0"
    assert bridge_columns == {
        "snapshot_id",
        "bridge_spec_id",
        "method",
        "steps",
        "grid",
        "variance_clock",
        "endpoint_policy",
        "extrema_policy",
        "cross_asset_policy",
        "stream_namespace",
        "generator_config_id",
        "created_run_id",
    }
    assert volume_columns == {
        "snapshot_id",
        "volume_spec_id",
        "underlying_id",
        "measure",
        "method",
        "base_volume",
        "volume_log_stddev",
        "rounding",
        "overflow_policy",
        "dependence_policy",
        "stream_namespace",
        "generator_config_id",
        "created_run_id",
    }
    assert {
        "intraday_bridge_spec_count",
        "underlying_volume_model_count",
    } <= revision_columns
    assert "intraday_bridge_specs" not in solver_relations
    assert "underlying_volume_models" not in solver_relations


def test_checked_in_smoke_snapshot_matches_its_manifest(repository_root: Path) -> None:
    database = repository_root / "snapshots/public/quantlib_bsm_smoke_v1.duckdb"
    manifest = json.loads(
        database.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["database_file"] == database.name
    connection = duckdb.connect(str(database), read_only=True)
    try:
        catalog = connection.execute(
            """
            SELECT status, current_revision
            FROM metadata.snapshots WHERE snapshot_id = ?
            """,
            [manifest["snapshot_id"]],
        ).fetchone()
        assert catalog == (
            manifest["status"],
            manifest["revision"],
        )
        assert connection.execute(
            "SELECT count(*) FROM market.underlying_daily"
        ).fetchone()[0] == manifest["underlying_daily_count"]
        assert connection.execute(
            "SELECT count(*) FROM market.option_daily"
        ).fetchone()[0] == manifest["option_daily_count"]
        assert connection.execute(
            "SELECT count(*) FROM market.option_pricing_audit"
        ).fetchone()[0] == manifest["option_pricing_audit_count"]
    finally:
        connection.close()


def test_checked_in_snapshot_is_the_22_metal_liquid_bsm_profile(
    repository_root: Path,
) -> None:
    database = repository_root / "snapshots/public/quantlib_bsm_smoke_v1.duckdb"
    manifest = json.loads(
        database.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == "2.4.0"
    assert manifest["snapshot_id"] == (
        "DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v3"
    )
    assert manifest["business_date_count"] == 65
    assert manifest["underlying_count"] == 22
    assert manifest["option_contract_count"] == 1_232
    assert manifest["option_daily_count"] == 60_368
    assert manifest["option_pricing_audit_count"] == 60_368

    connection = duckdb.connect(str(database), read_only=True)
    try:
        chain_shape = connection.execute(
            """
            SELECT count(DISTINCT expiry), count(DISTINCT strike_moneyness),
                   min(strike_moneyness), max(strike_moneyness)
            FROM market.option_contracts
            """
        ).fetchone()
        assert chain_shape == (
            4,
            7,
            Decimal("0.85000000"),
            Decimal("1.15000000"),
        )
        assert connection.execute(
            """
            SELECT count(*) FROM market.option_daily
            WHERE NOT (bid <= mid AND mid <= ask AND mid = settlement_price)
            """
        ).fetchone()[0] == 0
        liquidity_filter, quote_model = connection.execute(
            """
            SELECT liquidity_filter, quote_model
            FROM market.option_chain_specs
            """
        ).fetchone()
        assert json.loads(liquidity_filter)["max_expiry_days"] == 180
        assert json.loads(quote_model)["bid_ask_noise"]["type"] == (
            "clipped_gaussian_half_spread_multiplier"
        )
        assert connection.execute(
            """
            SELECT DISTINCT pricing_model, pricing_engine
            FROM market.pricing_metadata
            """
        ).fetchall() == [
            ("Black-Scholes-Merton", "QuantLib.AnalyticEuropeanEngine")
        ]
        assert connection.execute(
            """
            SELECT count(*) FROM information_schema.views
            WHERE table_schema = 'solver_visible'
              AND table_name = 'option_pricing_audit'
            """
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT count(*) FROM market.option_pricing_audit
            WHERE iv_status = 'CONVERGED'
            """
        ).fetchone()[0] == 59_836
    finally:
        connection.close()


def test_current_generator_uses_new_identity_without_legacy_iv_audit(
    tmp_path: Path, repository_root: Path
) -> None:
    public_database = (
        repository_root / "snapshots/public/quantlib_bsm_smoke_v1.duckdb"
    )
    config = load_generator_config(
        repository_root
        / "configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json"
    )
    public_manifest = json.loads(
        public_database.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert config.snapshot_id != public_manifest["snapshot_id"]
    assert config.generator_config_id != public_manifest["generator_config_id"]
    assert config.generator_version != public_manifest["generator_version"]
    replay_config = replace(
        config,
        business_days=2,
        option_templates=config.option_templates[:2],
    )
    replay_database = tmp_path / "public-snapshot-replay.duckdb"
    with AuthoringPipeline(replay_database, replay_config) as pipeline:
        result = pipeline.create_smoke_snapshot()
        audit_count = pipeline.connection.execute(
            "SELECT count(*) FROM market.option_pricing_audit"
        ).fetchone()[0]

    assert result["summary"]["option_daily_count"] > 0
    assert result["summary"]["option_pricing_audit_count"] == audit_count == 0


def test_checked_in_bsm_mids_satisfy_discounted_bounds_and_put_call_parity(
    repository_root: Path,
) -> None:
    database = repository_root / "snapshots/public/quantlib_bsm_smoke_v1.duckdb"
    connection = duckdb.connect(str(database), read_only=True)
    try:
        max_parity_error = connection.execute(
            """
            WITH paired AS (
                SELECT
                    quote.date,
                    quote.underlying_id,
                    quote.expiry,
                    quote.strike,
                    max(CASE WHEN quote.call_put = 'call' THEN quote.mid END)
                        AS call_mid,
                    max(CASE WHEN quote.call_put = 'put' THEN quote.mid END)
                        AS put_mid,
                    max(underlying.spot_close) AS spot,
                    max(pricing.risk_free_rate) AS rate,
                    max(pricing.dividend_yield) AS dividend_yield
                FROM market.option_daily AS quote
                JOIN market.underlying_daily AS underlying
                  USING (snapshot_id, date, underlying_id)
                JOIN market.pricing_metadata AS pricing
                  ON pricing.snapshot_id = quote.snapshot_id
                 AND pricing.valuation_date = quote.date
                 AND pricing.underlying_id = quote.underlying_id
                GROUP BY quote.date, quote.underlying_id,
                         quote.expiry, quote.strike
            )
            SELECT max(abs(
                (call_mid - put_mid)
                - (
                    spot * exp(
                        -dividend_yield
                        * date_diff('day', date, expiry) / 365.0
                    )
                    - strike * exp(
                        -rate * date_diff('day', date, expiry) / 365.0
                    )
                )
            ))
            FROM paired
            """
        ).fetchone()[0]
        assert max_parity_error <= 2e-8

        bound_failures = connection.execute(
            """
            WITH priced AS (
                SELECT
                    quote.*,
                    underlying.spot_close * exp(
                        -pricing.dividend_yield
                        * date_diff('day', quote.date, quote.expiry) / 365.0
                    ) AS spot_pv,
                    quote.strike * exp(
                        -pricing.risk_free_rate
                        * date_diff('day', quote.date, quote.expiry) / 365.0
                    ) AS strike_pv
                FROM market.option_daily AS quote
                JOIN market.underlying_daily AS underlying
                  USING (snapshot_id, date, underlying_id)
                JOIN market.pricing_metadata AS pricing
                  ON pricing.snapshot_id = quote.snapshot_id
                 AND pricing.valuation_date = quote.date
                 AND pricing.underlying_id = quote.underlying_id
            )
            SELECT count(*)
            FROM priced
            WHERE CASE WHEN call_put = 'call'
                THEN mid < greatest(0, spot_pv - strike_pv) - 0.00000002
                  OR mid > spot_pv + 0.00000002
                ELSE mid < greatest(0, strike_pv - spot_pv) - 0.00000002
                  OR mid > strike_pv + 0.00000002
            END
            """
        ).fetchone()[0]
        assert bound_failures == 0
    finally:
        connection.close()
