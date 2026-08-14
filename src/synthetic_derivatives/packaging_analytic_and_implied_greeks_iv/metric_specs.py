"""Frozen public contracts for single-metric BSM delivery tasks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


DecimalConstraint = Literal[
    "signed_decimal8",
    "nonnegative_decimal8",
    "positive_decimal8",
]


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """Identity and public-output contract for one delivery target."""

    target: str
    display_name: str
    output_field: str
    variant_id: str
    method_id: str
    submission_schema_version: str
    schema_filename: str
    verifier_id: str
    task_version: str
    database_schema_version: str
    task_id_prefix: str
    task_id_pattern: str
    decimal_constraint: DecimalConstraint
    unit_description: str
    needs_iv_status: bool


METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        target="iv",
        display_name="market-implied volatility",
        output_field="market_implied_volatility",
        variant_id="bsm_market_implied_iv_v1",
        method_id="bsm-mid-iv-bisection80-v1",
        submission_schema_version=(
            "bsm-market-implied-iv-submission-v1.0.0"
        ),
        schema_filename=(
            "bsm-market-implied-iv-submission-v1.schema.json"
        ),
        verifier_id="quantlib-bsm-market-implied-iv-verifier-v1",
        task_version="1.0.0",
        database_schema_version="bsm-market-metric-task-duckdb-v1.0.0",
        task_id_prefix="bsm-market-iv-v1-",
        task_id_pattern="^bsm-market-iv-v1-[0-9a-f]{24}$",
        decimal_constraint="positive_decimal8",
        unit_description=(
            "Annualized volatility expressed as a decimal; 0.20 means 20%."
        ),
        needs_iv_status=True,
    ),
    MetricSpec(
        target="delta",
        display_name="unit Delta",
        output_field="unit_delta",
        variant_id="bsm_market_implied_delta_v1",
        method_id="bsm-mid-iv-bisection80-analytic-delta-v1",
        submission_schema_version=(
            "bsm-market-implied-delta-submission-v1.0.0"
        ),
        schema_filename=(
            "bsm-market-implied-delta-submission-v1.schema.json"
        ),
        verifier_id="quantlib-bsm-market-implied-delta-verifier-v1",
        task_version="1.0.0",
        database_schema_version="bsm-market-metric-task-duckdb-v1.0.0",
        task_id_prefix="bsm-market-delta-v1-",
        task_id_pattern="^bsm-market-delta-v1-[0-9a-f]{24}$",
        decimal_constraint="signed_decimal8",
        unit_description="Reported per +1.00 change in spot.",
        needs_iv_status=False,
    ),
    MetricSpec(
        target="gamma",
        display_name="unit Gamma",
        output_field="unit_gamma",
        variant_id="bsm_market_implied_gamma_v1",
        method_id="bsm-mid-iv-bisection80-analytic-gamma-v1",
        submission_schema_version=(
            "bsm-market-implied-gamma-submission-v1.0.0"
        ),
        schema_filename=(
            "bsm-market-implied-gamma-submission-v1.schema.json"
        ),
        verifier_id="quantlib-bsm-market-implied-gamma-verifier-v1",
        task_version="1.0.0",
        database_schema_version="bsm-market-metric-task-duckdb-v1.0.0",
        task_id_prefix="bsm-market-gamma-v1-",
        task_id_pattern="^bsm-market-gamma-v1-[0-9a-f]{24}$",
        decimal_constraint="nonnegative_decimal8",
        unit_description=(
            "Reported as the Delta change per +1.00 change in spot."
        ),
        needs_iv_status=False,
    ),
    MetricSpec(
        target="vega_1volpt",
        display_name="unit Vega for one volatility point",
        output_field="unit_vega_1volpt",
        variant_id="bsm_market_implied_vega_1volpt_v1",
        method_id=(
            "bsm-mid-iv-bisection80-analytic-vega-1volpt-v1"
        ),
        submission_schema_version=(
            "bsm-market-implied-vega-1volpt-submission-v1.0.0"
        ),
        schema_filename=(
            "bsm-market-implied-vega-1volpt-submission-v1.schema.json"
        ),
        verifier_id=(
            "quantlib-bsm-market-implied-vega-1volpt-verifier-v1"
        ),
        task_version="1.0.0",
        database_schema_version="bsm-market-metric-task-duckdb-v1.0.0",
        task_id_prefix="bsm-market-vega-1volpt-v1-",
        task_id_pattern="^bsm-market-vega-1volpt-v1-[0-9a-f]{24}$",
        decimal_constraint="nonnegative_decimal8",
        unit_description=(
            "Reported for a +0.01 absolute change in volatility."
        ),
        needs_iv_status=False,
    ),
    MetricSpec(
        target="theta_1calendar_day",
        display_name="unit Theta for one calendar day",
        output_field="unit_theta_1calendar_day",
        variant_id="bsm_market_implied_theta_1calendar_day_v1",
        method_id=(
            "bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1"
        ),
        submission_schema_version=(
            "bsm-market-implied-theta-1calendar-day-submission-v1.0.0"
        ),
        schema_filename=(
            "bsm-market-implied-theta-1calendar-day-submission-v1.schema.json"
        ),
        verifier_id=(
            "quantlib-bsm-market-implied-theta-1calendar-day-verifier-v1"
        ),
        task_version="1.0.0",
        database_schema_version="bsm-market-metric-task-duckdb-v1.0.0",
        task_id_prefix="bsm-market-theta-1calendar-day-v1-",
        task_id_pattern=(
            "^bsm-market-theta-1calendar-day-v1-[0-9a-f]{24}$"
        ),
        decimal_constraint="signed_decimal8",
        unit_description=(
            "Reported for one calendar day passing with expiry fixed."
        ),
        needs_iv_status=False,
    ),
    MetricSpec(
        target="rho_1pct",
        display_name="unit Rho for one percentage point",
        output_field="unit_rho_1pct",
        variant_id="bsm_market_implied_rho_1pct_v1",
        method_id="bsm-mid-iv-bisection80-analytic-rho-1pct-v1",
        submission_schema_version=(
            "bsm-market-implied-rho-1pct-submission-v1.0.0"
        ),
        schema_filename=(
            "bsm-market-implied-rho-1pct-submission-v1.schema.json"
        ),
        verifier_id="quantlib-bsm-market-implied-rho-1pct-verifier-v1",
        task_version="1.0.0",
        database_schema_version="bsm-market-metric-task-duckdb-v1.0.0",
        task_id_prefix="bsm-market-rho-1pct-v1-",
        task_id_pattern="^bsm-market-rho-1pct-v1-[0-9a-f]{24}$",
        decimal_constraint="signed_decimal8",
        unit_description=(
            "Reported for a +0.01 absolute change in the continuously "
            "compounded risk-free rate."
        ),
        needs_iv_status=False,
    ),
)

BSM_METRIC_TARGET_ORDER: tuple[str, ...] = tuple(
    spec.target for spec in METRIC_SPECS
)
TARGET_ORDER = BSM_METRIC_TARGET_ORDER
METRIC_SPECS_BY_TARGET: Mapping[str, MetricSpec] = MappingProxyType(
    {spec.target: spec for spec in METRIC_SPECS}
)


def get_metric_spec(target: str) -> MetricSpec:
    """Return the frozen specification for one supported target."""

    try:
        return METRIC_SPECS_BY_TARGET[target]
    except KeyError as error:
        raise ValueError(f"unsupported BSM metric target: {target!r}") from error


__all__ = [
    "BSM_METRIC_TARGET_ORDER",
    "DecimalConstraint",
    "METRIC_SPECS",
    "METRIC_SPECS_BY_TARGET",
    "MetricSpec",
    "TARGET_ORDER",
    "get_metric_spec",
]
