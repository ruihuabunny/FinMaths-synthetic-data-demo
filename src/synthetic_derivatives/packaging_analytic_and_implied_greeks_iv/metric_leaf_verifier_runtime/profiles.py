"""Typed source profiles for the two accepted BSM metric protocols."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any


_TASK_FAMILY = "bsm_greeks"
_SELECTION_POLICY_ID = (
    "two-nearest-expiries-five-abs-log-forward-moneyness-pairs-v1"
)
_LOGICAL_CHECKSUM_ID = "sha256-bsm-greeks-canonical-logical-rows-v2"
_RISK_NEUTRAL_MEASURE_ID = "USD-MONEY-MARKET-Q-v1"
_NUMERAIRE_ID = "USD-MONEY-MARKET-ACCOUNT-v1"
_SUCCESS_IV_STATUS = "CONVERGED_FIXED_ITERATIONS"
_ORACLE_CONFIG_SCHEMA_VERSION = "bsm-market-metric-oracle-config-v1.0.0"
_IV_METHOD_ID = "bsm-bisection-float64-80-v1"
_GREEKS_METHOD_ID = "bsm-analytic-float64-greeks-v1"
_LOWER_VOLATILITY = 0.000001
_UPPER_VOLATILITY = 5.0
_BISECTION_ITERATIONS = 80

STATIC_V2_PROFILE_ID = "static_v2"
DUCKDB_QUERY_V3_PROFILE_ID = "duckdb_query_v3"
METRIC_TARGET_ORDER = (
    "iv",
    "delta",
    "gamma",
    "vega_1volpt",
    "theta_1calendar_day",
    "rho_1pct",
)
_STATIC_DATABASE_SCHEMA_VERSION = "bsm-market-metric-task-duckdb-v1.0.0"
_STATIC_TASK_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One exact metric identity inside a selected runtime profile."""

    target: str
    output_field: str
    variant_id: str
    method_id: str
    submission_schema_version: str
    verifier_id: str
    task_id_pattern: str
    decimal_constraint: str
    needs_iv_status: bool
    database_schema_version: str = _STATIC_DATABASE_SCHEMA_VERSION
    task_version: str = _STATIC_TASK_VERSION


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """Typed source description selected before rendering one leaf bundle."""

    profile_id: str
    submission_module: str
    database_schema_version: str
    task_version: str
    metric_specs: tuple[MetricSpec, ...]


_STATIC_METRIC_SPECS = (
    MetricSpec(
        "iv",
        "market_implied_volatility",
        "bsm_market_implied_iv_v1",
        "bsm-mid-iv-bisection80-v1",
        "bsm-market-implied-iv-submission-v1.0.0",
        "quantlib-bsm-market-implied-iv-verifier-v1",
        r"^bsm-market-iv-v1-[0-9a-f]{24}$",
        "positive_decimal8",
        True,
    ),
    MetricSpec(
        "delta",
        "unit_delta",
        "bsm_market_implied_delta_v1",
        "bsm-mid-iv-bisection80-analytic-delta-v1",
        "bsm-market-implied-delta-submission-v1.0.0",
        "quantlib-bsm-market-implied-delta-verifier-v1",
        r"^bsm-market-delta-v1-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    MetricSpec(
        "gamma",
        "unit_gamma",
        "bsm_market_implied_gamma_v1",
        "bsm-mid-iv-bisection80-analytic-gamma-v1",
        "bsm-market-implied-gamma-submission-v1.0.0",
        "quantlib-bsm-market-implied-gamma-verifier-v1",
        r"^bsm-market-gamma-v1-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    MetricSpec(
        "vega_1volpt",
        "unit_vega_1volpt",
        "bsm_market_implied_vega_1volpt_v1",
        "bsm-mid-iv-bisection80-analytic-vega-1volpt-v1",
        "bsm-market-implied-vega-1volpt-submission-v1.0.0",
        "quantlib-bsm-market-implied-vega-1volpt-verifier-v1",
        r"^bsm-market-vega-1volpt-v1-[0-9a-f]{24}$",
        "nonnegative_decimal8",
        False,
    ),
    MetricSpec(
        "theta_1calendar_day",
        "unit_theta_1calendar_day",
        "bsm_market_implied_theta_1calendar_day_v1",
        "bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1",
        "bsm-market-implied-theta-1calendar-day-submission-v1.0.0",
        "quantlib-bsm-market-implied-theta-1calendar-day-verifier-v1",
        r"^bsm-market-theta-1calendar-day-v1-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
    MetricSpec(
        "rho_1pct",
        "unit_rho_1pct",
        "bsm_market_implied_rho_1pct_v1",
        "bsm-mid-iv-bisection80-analytic-rho-1pct-v1",
        "bsm-market-implied-rho-1pct-submission-v1.0.0",
        "quantlib-bsm-market-implied-rho-1pct-verifier-v1",
        r"^bsm-market-rho-1pct-v1-[0-9a-f]{24}$",
        "signed_decimal8",
        False,
    ),
)


def _db_query_v3_spec(spec: MetricSpec) -> MetricSpec:
    return replace(
        spec,
        submission_schema_version=spec.submission_schema_version.replace(
            "-v1.0.0", "-v2.0.0"
        ),
        verifier_id=spec.verifier_id.removesuffix("-v1") + "-v2",
        task_id_pattern=spec.task_id_pattern.replace("-v1-", "-v2-"),
        database_schema_version="bsm-market-metric-task-duckdb-v2.0.0",
        task_version="2.0.0",
    )


_DB_QUERY_V3_METRIC_SPECS = tuple(
    _db_query_v3_spec(spec) for spec in _STATIC_METRIC_SPECS
)
STATIC_V2_PROFILE = RuntimeProfile(
    profile_id=STATIC_V2_PROFILE_ID,
    submission_module="submission_static_v2",
    database_schema_version=_STATIC_DATABASE_SCHEMA_VERSION,
    task_version=_STATIC_TASK_VERSION,
    metric_specs=_STATIC_METRIC_SPECS,
)
DUCKDB_QUERY_V3_PROFILE = RuntimeProfile(
    profile_id=DUCKDB_QUERY_V3_PROFILE_ID,
    submission_module="submission_duckdb_v3",
    database_schema_version="bsm-market-metric-task-duckdb-v2.0.0",
    task_version="2.0.0",
    metric_specs=_DB_QUERY_V3_METRIC_SPECS,
)
RUNTIME_PROFILES = (STATIC_V2_PROFILE, DUCKDB_QUERY_V3_PROFILE)
_PROFILE_BY_ID = {profile.profile_id: profile for profile in RUNTIME_PROFILES}
_METRIC_SPECS = tuple(
    spec for profile in RUNTIME_PROFILES for spec in profile.metric_specs
)


def validate_render_profile(profile: RuntimeProfile) -> RuntimeProfile:
    """Fail closed before a renderer selects profile and submission sources."""

    if not isinstance(profile, RuntimeProfile):
        raise TypeError("runtime render profile must be a RuntimeProfile")
    canonical = _PROFILE_BY_ID.get(profile.profile_id)
    if canonical is None:
        raise ValueError("runtime profile id is not supported")
    if profile.database_schema_version != canonical.database_schema_version or any(
        spec.database_schema_version != profile.database_schema_version
        for spec in profile.metric_specs
    ):
        raise ValueError("runtime profile database schema version is inconsistent")
    if profile.task_version != canonical.task_version or any(
        spec.task_version != profile.task_version for spec in profile.metric_specs
    ):
        raise ValueError("runtime profile task version is inconsistent")
    targets = tuple(spec.target for spec in profile.metric_specs)
    if targets != METRIC_TARGET_ORDER or len(set(targets)) != len(targets):
        raise ValueError("runtime profile metric keys are inconsistent")
    if profile.submission_module != canonical.submission_module:
        raise ValueError("runtime profile submission module is inconsistent")
    if profile.metric_specs != canonical.metric_specs:
        raise ValueError("runtime profile metric definitions differ from canonical")
    return profile


def get_runtime_profile(profile_id: str) -> RuntimeProfile:
    """Return one exact source profile for later renderer selection."""

    if not isinstance(profile_id, str):
        raise TypeError("runtime profile id must be a string")
    try:
        profile = _PROFILE_BY_ID[profile_id]
    except KeyError as error:
        raise ValueError(f"unsupported runtime profile: {profile_id!r}") from error
    return validate_render_profile(profile)


def metric_spec_for_target(profile: RuntimeProfile, target: str) -> MetricSpec:
    """Resolve an exact metric key only inside one validated profile."""

    canonical = validate_render_profile(profile)
    try:
        return next(spec for spec in canonical.metric_specs if spec.target == target)
    except StopIteration as error:
        raise ValueError(f"unsupported BSM metric target: {target!r}") from error


def oracle_config_for_metric(
    profile: RuntimeProfile, target: str
) -> dict[str, Any]:
    """Return the exact trusted config for one profile/metric pair."""

    return _oracle_config(metric_spec_for_target(profile, target))


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
    profiles = RUNTIME_PROFILES if profile is None else (validate_render_profile(profile),)
    candidate = dict(method_config)
    target = candidate.get("target")
    spec = next(
        (
            item
            for selected in profiles
            for item in selected.metric_specs
            if item.target == target and candidate == _oracle_config(item)
        ),
        None,
    )
    if spec is None:
        raise ValueError("trusted verifier received a different oracle config")
    return spec


__all__ = [
    "DUCKDB_QUERY_V3_PROFILE",
    "DUCKDB_QUERY_V3_PROFILE_ID",
    "METRIC_TARGET_ORDER",
    "MetricSpec",
    "RUNTIME_PROFILES",
    "RuntimeProfile",
    "STATIC_V2_PROFILE",
    "STATIC_V2_PROFILE_ID",
    "get_runtime_profile",
    "metric_spec_for_target",
    "oracle_config_for_metric",
    "validate_render_profile",
]
