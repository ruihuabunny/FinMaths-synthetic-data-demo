"""Ordered DuckDB table and business-key contracts."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class TableSpec:
    """Ordered table contract used by deterministic staging and MERGE calls."""

    name: str
    columns: tuple[str, ...]
    keys: tuple[str, ...]


# Only tables produced by the current authoring contract belong here.  The
# schema-retained option_pricing_audit table is intentionally absent: replay
# compares current materialized rows, while manifest counts can still report
# historical audit rows in a legacy database.
TABLE_SPECS = {
    "intraday_bridge_specs": TableSpec(
        "market.intraday_bridge_specs",
        (
            "snapshot_id", "bridge_spec_id", "method", "steps", "grid",
            "variance_clock", "endpoint_policy", "extrema_policy",
            "cross_asset_policy", "stream_namespace", "generator_config_id",
            "created_run_id",
        ),
        ("snapshot_id", "bridge_spec_id"),
    ),
    "underlying_volume_models": TableSpec(
        "market.underlying_volume_models",
        (
            "snapshot_id", "volume_spec_id", "underlying_id", "measure",
            "method", "base_volume", "volume_log_stddev", "rounding",
            "overflow_policy", "dependence_policy", "stream_namespace",
            "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "volume_spec_id", "underlying_id"),
    ),
    "underlying_dependence": TableSpec(
        "market.underlying_dependence",
        (
            "snapshot_id", "dependence_spec_id", "measure",
            "source_dependence_spec_id", "mapping_id", "mapping_type",
            "risk_neutral_measure_id", "numeraire_id", "rate_path_id",
            "driver_order", "formulation", "factor_loading_matrix",
            "idiosyncratic_diagonal", "correlation_matrix", "matrix_dtype",
            "factorization_method", "factorization_order", "time_grid",
            "regime_id", "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "dependence_spec_id"),
    ),
    "option_chain_specs": TableSpec(
        "market.option_chain_specs",
        (
            "snapshot_id", "chain_id", "listing_rule", "listing_date",
            "roll_rule", "grid_type", "expiry_days", "moneyness_grid",
            "strike_grid", "call_put", "strike_increment", "strike_rounding",
            "exercise_style", "settlement_type", "contract_multiplier",
            "liquidity_filter", "quote_model",
            "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "chain_id"),
    ),
    "underlyings": TableSpec(
        "market.underlyings",
        (
            "snapshot_id", "underlying_id", "currency", "asset_class",
            "initial_spot", "physical_drift", "physical_volatility",
            "risk_free_rate", "dividend_yield", "base_implied_volatility",
            "generator_config_id", "created_run_id",
        ),
        ("snapshot_id", "underlying_id"),
    ),
    "option_contracts": TableSpec(
        "market.option_contracts",
        (
            "snapshot_id", "option_id", "underlying_id", "template_id",
            "call_put", "strike", "expiry", "exercise_style",
            "settlement_type", "contract_multiplier",
            "chain_id", "listing_date", "listing_spot", "strike_moneyness",
            "created_run_id",
        ),
        ("snapshot_id", "option_id"),
    ),
    "underlying_daily": TableSpec(
        "market.underlying_daily",
        (
            "snapshot_id", "date", "underlying_id", "spot_open", "spot_high",
            "spot_low", "spot_close", "adjusted_close", "volume", "dividend",
            "corporate_action", "generated_run_id",
        ),
        ("snapshot_id", "date", "underlying_id"),
    ),
    "option_daily": TableSpec(
        "market.option_daily",
        (
            "snapshot_id", "date", "underlying_id", "option_id", "call_put",
            "strike", "expiry", "exercise_style", "settlement_type",
            "contract_multiplier", "bid", "ask", "mid", "settlement_price",
            "volume", "open_interest", "generated_run_id",
        ),
        ("snapshot_id", "date", "option_id"),
    ),
    "pricing_metadata": TableSpec(
        "market.pricing_metadata",
        (
            "snapshot_id", "valuation_timestamp", "valuation_date",
            "underlying_id", "currency", "discount_curve", "risk_free_rate",
            "dividend_curve", "dividend_yield", "borrow_or_carry_rate",
            "calendar", "day_count", "physical_dynamics", "pricing_dynamics",
            "pricing_model", "pricing_engine", "generator_version", "seed",
            "rng", "input_precision", "canonicalization", "generated_run_id",
        ),
        ("snapshot_id", "valuation_timestamp", "underlying_id"),
    ),
}

