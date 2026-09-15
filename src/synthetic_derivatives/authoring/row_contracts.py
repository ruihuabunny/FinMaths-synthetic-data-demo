"""Tuple-compatible, named contracts for rows staged into authoring tables.

The field order is an explicit counterpart to :mod:`table_specs`.  These types
remain ordinary tuples for DuckDB ``executemany`` while preventing business
logic from depending on opaque numeric positions.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, NamedTuple


class IntradayBridgeSpecRow(NamedTuple):
    snapshot_id: str
    bridge_spec_id: str
    method: str
    steps: int
    grid: str
    variance_clock: str
    endpoint_policy: str
    extrema_policy: str
    cross_asset_policy: str
    stream_namespace: str
    generator_config_id: str
    created_run_id: str


class UnderlyingVolumeModelRow(NamedTuple):
    snapshot_id: str
    volume_spec_id: str
    underlying_id: str
    measure: str
    method: str
    base_volume: int
    volume_log_stddev: float
    rounding: str
    overflow_policy: str
    dependence_policy: str
    stream_namespace: str
    generator_config_id: str
    created_run_id: str


class UnderlyingDependenceRow(NamedTuple):
    snapshot_id: str
    dependence_spec_id: str
    measure: str
    source_dependence_spec_id: str | None
    mapping_id: str | None
    mapping_type: str | None
    risk_neutral_measure_id: str | None
    numeraire_id: str | None
    rate_path_id: str | None
    driver_order: str
    formulation: str
    factor_loading_matrix: str
    idiosyncratic_diagonal: str
    correlation_matrix: str
    matrix_dtype: str
    factorization_method: str
    factorization_order: str
    time_grid: str
    regime_id: str
    generator_config_id: str
    created_run_id: str


class OptionChainSpecRow(NamedTuple):
    snapshot_id: str
    chain_id: str
    listing_rule: str
    listing_date: date
    roll_rule: str
    grid_type: str
    expiry_days: str
    moneyness_grid: str | None
    strike_grid: str | None
    call_put: str
    strike_increment: Decimal
    strike_rounding: str
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal
    liquidity_filter: str | None
    quote_model: str | None
    generator_config_id: str
    created_run_id: str


class UnderlyingMasterRow(NamedTuple):
    snapshot_id: str
    underlying_id: str
    currency: str
    asset_class: str
    initial_spot: Decimal
    physical_drift: float
    physical_volatility: float
    risk_free_rate: float
    dividend_yield: float
    base_implied_volatility: float | None
    generator_config_id: str
    created_run_id: str


class OptionContractRow(NamedTuple):
    snapshot_id: str
    option_id: str
    underlying_id: str
    template_id: str
    call_put: str
    strike: Decimal
    expiry: date
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal
    chain_id: str | None
    listing_date: date | None
    listing_spot: Decimal | None
    strike_moneyness: Decimal | None
    created_run_id: str


class UnderlyingDailyRow(NamedTuple):
    snapshot_id: str
    date: date
    underlying_id: str
    spot_open: Decimal
    spot_high: Decimal
    spot_low: Decimal
    spot_close: Decimal
    adjusted_close: Decimal
    volume: int
    dividend: Decimal
    corporate_action: str
    generated_run_id: str


class OptionDailyRow(NamedTuple):
    snapshot_id: str
    date: date
    underlying_id: str
    option_id: str
    call_put: str
    strike: Decimal
    expiry: date
    exercise_style: str
    settlement_type: str
    contract_multiplier: Decimal
    bid: Decimal
    ask: Decimal
    mid: Decimal
    settlement_price: Decimal
    volume: int
    open_interest: int
    generated_run_id: str


class PricingMetadataRow(NamedTuple):
    snapshot_id: str
    valuation_timestamp: datetime
    valuation_date: date
    underlying_id: str
    currency: str
    discount_curve: str
    risk_free_rate: float
    dividend_curve: str
    dividend_yield: float
    borrow_or_carry_rate: float
    calendar: str
    day_count: str
    physical_dynamics: str
    pricing_dynamics: str
    pricing_model: str
    pricing_engine: str
    generator_version: str
    seed: int
    rng: str
    input_precision: str
    canonicalization: str
    generated_run_id: str


ROW_TYPES_BY_TABLE: dict[str, type[tuple[Any, ...]]] = {
    "intraday_bridge_specs": IntradayBridgeSpecRow,
    "underlying_volume_models": UnderlyingVolumeModelRow,
    "underlying_dependence": UnderlyingDependenceRow,
    "option_chain_specs": OptionChainSpecRow,
    "underlyings": UnderlyingMasterRow,
    "option_contracts": OptionContractRow,
    "underlying_daily": UnderlyingDailyRow,
    "option_daily": OptionDailyRow,
    "pricing_metadata": PricingMetadataRow,
}
