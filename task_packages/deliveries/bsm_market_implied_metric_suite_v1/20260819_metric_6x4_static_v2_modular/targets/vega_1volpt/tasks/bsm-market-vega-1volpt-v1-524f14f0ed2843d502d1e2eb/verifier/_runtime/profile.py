"""Frozen identities for one rendered BSM metric protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_TASK_FAMILY = 'bsm_greeks'
_SELECTION_POLICY_ID = 'two-nearest-expiries-five-abs-log-forward-moneyness-pairs-v1'
_LOGICAL_CHECKSUM_ID = 'sha256-bsm-greeks-canonical-logical-rows-v2'
_RISK_NEUTRAL_MEASURE_ID = 'USD-MONEY-MARKET-Q-v1'
_NUMERAIRE_ID = 'USD-MONEY-MARKET-ACCOUNT-v1'
_SUCCESS_IV_STATUS = 'CONVERGED_FIXED_ITERATIONS'
_ORACLE_CONFIG_SCHEMA_VERSION = 'bsm-market-metric-oracle-config-v1.0.0'
_IV_METHOD_ID = 'bsm-bisection-float64-80-v1'
_GREEKS_METHOD_ID = 'bsm-analytic-float64-greeks-v1'
_LOWER_VOLATILITY = 1e-06
_UPPER_VOLATILITY = 5.0
_BISECTION_ITERATIONS = 80
METRIC_TARGET_ORDER = ('iv', 'delta', 'gamma', 'vega_1volpt', 'theta_1calendar_day', 'rho_1pct')


@dataclass(frozen=True, slots=True)
class MetricSpec:
    target: str
    output_field: str
    variant_id: str
    method_id: str
    submission_schema_version: str
    verifier_id: str
    task_id_pattern: str
    decimal_constraint: str
    needs_iv_status: bool
    database_schema_version: str
    task_version: str


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    profile_id: str
    submission_module: str
    database_schema_version: str
    task_version: str
    metric_specs: tuple[MetricSpec, ...]


_METRIC_SPECS = (
    MetricSpec(
        target='iv',
        output_field='market_implied_volatility',
        variant_id='bsm_market_implied_iv_v1',
        method_id='bsm-mid-iv-bisection80-v1',
        submission_schema_version='bsm-market-implied-iv-submission-v1.0.0',
        verifier_id='quantlib-bsm-market-implied-iv-verifier-v1',
        task_id_pattern='^bsm-market-iv-v1-[0-9a-f]{24}$',
        decimal_constraint='positive_decimal8',
        needs_iv_status=True,
        database_schema_version='bsm-market-metric-task-duckdb-v1.0.0',
        task_version='1.0.0',
    ),
    MetricSpec(
        target='delta',
        output_field='unit_delta',
        variant_id='bsm_market_implied_delta_v1',
        method_id='bsm-mid-iv-bisection80-analytic-delta-v1',
        submission_schema_version='bsm-market-implied-delta-submission-v1.0.0',
        verifier_id='quantlib-bsm-market-implied-delta-verifier-v1',
        task_id_pattern='^bsm-market-delta-v1-[0-9a-f]{24}$',
        decimal_constraint='signed_decimal8',
        needs_iv_status=False,
        database_schema_version='bsm-market-metric-task-duckdb-v1.0.0',
        task_version='1.0.0',
    ),
    MetricSpec(
        target='gamma',
        output_field='unit_gamma',
        variant_id='bsm_market_implied_gamma_v1',
        method_id='bsm-mid-iv-bisection80-analytic-gamma-v1',
        submission_schema_version='bsm-market-implied-gamma-submission-v1.0.0',
        verifier_id='quantlib-bsm-market-implied-gamma-verifier-v1',
        task_id_pattern='^bsm-market-gamma-v1-[0-9a-f]{24}$',
        decimal_constraint='nonnegative_decimal8',
        needs_iv_status=False,
        database_schema_version='bsm-market-metric-task-duckdb-v1.0.0',
        task_version='1.0.0',
    ),
    MetricSpec(
        target='vega_1volpt',
        output_field='unit_vega_1volpt',
        variant_id='bsm_market_implied_vega_1volpt_v1',
        method_id='bsm-mid-iv-bisection80-analytic-vega-1volpt-v1',
        submission_schema_version='bsm-market-implied-vega-1volpt-submission-v1.0.0',
        verifier_id='quantlib-bsm-market-implied-vega-1volpt-verifier-v1',
        task_id_pattern='^bsm-market-vega-1volpt-v1-[0-9a-f]{24}$',
        decimal_constraint='nonnegative_decimal8',
        needs_iv_status=False,
        database_schema_version='bsm-market-metric-task-duckdb-v1.0.0',
        task_version='1.0.0',
    ),
    MetricSpec(
        target='theta_1calendar_day',
        output_field='unit_theta_1calendar_day',
        variant_id='bsm_market_implied_theta_1calendar_day_v1',
        method_id='bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1',
        submission_schema_version='bsm-market-implied-theta-1calendar-day-submission-v1.0.0',
        verifier_id='quantlib-bsm-market-implied-theta-1calendar-day-verifier-v1',
        task_id_pattern='^bsm-market-theta-1calendar-day-v1-[0-9a-f]{24}$',
        decimal_constraint='signed_decimal8',
        needs_iv_status=False,
        database_schema_version='bsm-market-metric-task-duckdb-v1.0.0',
        task_version='1.0.0',
    ),
    MetricSpec(
        target='rho_1pct',
        output_field='unit_rho_1pct',
        variant_id='bsm_market_implied_rho_1pct_v1',
        method_id='bsm-mid-iv-bisection80-analytic-rho-1pct-v1',
        submission_schema_version='bsm-market-implied-rho-1pct-submission-v1.0.0',
        verifier_id='quantlib-bsm-market-implied-rho-1pct-verifier-v1',
        task_id_pattern='^bsm-market-rho-1pct-v1-[0-9a-f]{24}$',
        decimal_constraint='signed_decimal8',
        needs_iv_status=False,
        database_schema_version='bsm-market-metric-task-duckdb-v1.0.0',
        task_version='1.0.0',
    ),
)
STATIC_V2_PROFILE = RuntimeProfile(
    profile_id='static_v2',
    submission_module='submission_static_v2',
    database_schema_version='bsm-market-metric-task-duckdb-v1.0.0',
    task_version='1.0.0',
    metric_specs=_METRIC_SPECS,
)
_RUNTIME_PROFILE = STATIC_V2_PROFILE


def _oracle_config(spec: MetricSpec) -> dict[str, Any]:
    return {
        "oracle_config_schema_version": _ORACLE_CONFIG_SCHEMA_VERSION,
        "target": spec.target,
        "output_field": spec.output_field,
        "variant_id": spec.variant_id,
        "method_id": spec.method_id,
        "submission_schema_version": spec.submission_schema_version,
        "verifier_id": spec.verifier_id,
        "database_schema_version": spec.database_schema_version,
        "task_version": spec.task_version,
        "task_id_pattern": spec.task_id_pattern,
        "decimal_constraint": spec.decimal_constraint,
        "needs_iv_status": spec.needs_iv_status,
        "quantlib_version": "1.39",
        "pricing_engine": "QuantLib.AnalyticEuropeanEngine",
        "iv_method_id": _IV_METHOD_ID,
        "greeks_method_id": _GREEKS_METHOD_ID,
        "pricing_measure_id": _RISK_NEUTRAL_MEASURE_ID,
        "numeraire_id": _NUMERAIRE_ID,
        "day_count": "Actual365Fixed",
        "rate_compounding": "continuous",
        "iterations": _BISECTION_ITERATIONS,
        "volatility_bracket": [_LOWER_VOLATILITY, _UPPER_VOLATILITY],
        "canonical_precision": 8,
        "canonical_rounding": "ROUND_HALF_EVEN",
    }


def _spec_from_config(
    method_config: Mapping[str, Any],
    *,
    profile: RuntimeProfile | None = None,
) -> MetricSpec:
    if not isinstance(method_config, Mapping):
        raise ValueError("trusted verifier requires an oracle config object")
    if profile is not None and profile != _RUNTIME_PROFILE:
        raise ValueError("trusted verifier received a different oracle config")
    candidate = dict(method_config)
    target = candidate.get("target")
    spec = next(
        (
            item
            for item in _METRIC_SPECS
            if item.target == target and candidate == _oracle_config(item)
        ),
        None,
    )
    if spec is None:
        raise ValueError("trusted verifier received a different oracle config")
    return spec
