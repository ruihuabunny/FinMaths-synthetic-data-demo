"""Frozen public contracts for single-metric BSM delivery tasks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal

from synthetic_derivatives.model_families.adapters import (
    DUCKDB_QUERY_V3_SOLVER_INTERFACE_ID,
    STATIC_JSON_SOLVER_INTERFACE_ID,
    TDGBM_BSM_MODEL_FAMILY_ID,
    get_legacy_variant_identity,
)
from synthetic_derivatives.model_families.capabilities import (
    CapabilityBinding,
    CapabilityKey,
)
from synthetic_derivatives.task_space.models import TaskSpecV3


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


def _db_query_v3_spec(spec: MetricSpec) -> MetricSpec:
    """Version only the solver ABI while preserving the financial method."""

    return replace(
        spec,
        submission_schema_version=spec.submission_schema_version.replace(
            "-v1.0.0", "-v2.0.0"
        ),
        schema_filename=spec.schema_filename.replace(
            "-v1.schema.json", "-v2.schema.json"
        ),
        verifier_id=spec.verifier_id.removesuffix("-v1") + "-v2",
        task_version="2.0.0",
        database_schema_version="bsm-market-metric-task-duckdb-v2.0.0",
        task_id_prefix=spec.task_id_prefix.replace("-v1-", "-v2-"),
        task_id_pattern=spec.task_id_pattern.replace("-v1-", "-v2-"),
    )


METRIC_SPECS_DB_QUERY_V3: tuple[MetricSpec, ...] = tuple(
    _db_query_v3_spec(spec) for spec in METRIC_SPECS
)
METRIC_SPECS_DB_QUERY_V3_BY_TARGET: Mapping[str, MetricSpec] = MappingProxyType(
    {spec.target: spec for spec in METRIC_SPECS_DB_QUERY_V3}
)


def get_metric_spec(target: str) -> MetricSpec:
    """Return the frozen specification for one supported target."""

    try:
        return METRIC_SPECS_BY_TARGET[target]
    except KeyError as error:
        raise ValueError(f"unsupported BSM metric target: {target!r}") from error


def get_metric_spec_db_query_v3(target: str) -> MetricSpec:
    """Return the v3 DuckDB-query ABI for one unchanged metric method."""

    try:
        return METRIC_SPECS_DB_QUERY_V3_BY_TARGET[target]
    except KeyError as error:
        raise ValueError(f"unsupported BSM metric target: {target!r}") from error


def is_registered_metric_spec(spec: object) -> bool:
    """Return whether *spec* is an exact v2-legacy or v3-query registry entry."""

    return isinstance(spec, MetricSpec) and spec in {
        METRIC_SPECS_BY_TARGET.get(spec.target),
        METRIC_SPECS_DB_QUERY_V3_BY_TARGET.get(spec.target),
    }


_METRIC_COORDINATES = get_legacy_variant_identity(
    "bsm_market_implied_greeks_v1"
).coordinates


def metric_capability_binding(spec: MetricSpec) -> CapabilityBinding:
    """Map one exact frozen ``MetricSpec`` to its semantic capability.

    Static v1 and DuckDB-query v3 specs deliberately map to different solver
    interfaces and output contracts even though their financial method IDs are
    unchanged.
    """

    if not isinstance(spec, MetricSpec):
        raise TypeError("metric capability adapter requires MetricSpec")
    if METRIC_SPECS_BY_TARGET.get(spec.target) == spec:
        solver_interface_id = STATIC_JSON_SOLVER_INTERFACE_ID
    elif METRIC_SPECS_DB_QUERY_V3_BY_TARGET.get(spec.target) == spec:
        solver_interface_id = DUCKDB_QUERY_V3_SOLVER_INTERFACE_ID
    else:
        raise ValueError("metric capability adapter requires a frozen registry entry")
    return CapabilityBinding(
        CapabilityKey(
            model_family_id=TDGBM_BSM_MODEL_FAMILY_ID,
            task_family_id="market_implied_metric",
            task_kind_id=spec.target,
            method_id=spec.method_id,
            solver_interface_id=solver_interface_id,
        ),
        output_contract_id=spec.submission_schema_version,
    )


def metric_task_spec_v3(
    spec: MetricSpec,
    *,
    task_id: str,
    snapshot_id: str,
    snapshot_revision: int,
) -> TaskSpecV3:
    """Adapt one materialized metric task without modifying its old artifact."""

    binding = metric_capability_binding(spec)
    return TaskSpecV3(
        task_id=task_id,
        model_family_id=binding.key.model_family_id,
        task_family_id=binding.key.task_family_id,
        task_kind_id=binding.key.task_kind_id,
        solver_interface_id=binding.key.solver_interface_id,
        coordinates=_METRIC_COORDINATES,
        snapshot_id=snapshot_id,
        snapshot_revision=snapshot_revision,
        method_id=binding.key.method_id,
        output_contract_id=binding.output_contract_id,
    )


__all__ = [
    "BSM_METRIC_TARGET_ORDER",
    "DecimalConstraint",
    "METRIC_SPECS",
    "METRIC_SPECS_BY_TARGET",
    "METRIC_SPECS_DB_QUERY_V3",
    "METRIC_SPECS_DB_QUERY_V3_BY_TARGET",
    "MetricSpec",
    "TARGET_ORDER",
    "get_metric_spec",
    "get_metric_spec_db_query_v3",
    "is_registered_metric_spec",
    "metric_capability_binding",
    "metric_task_spec_v3",
]
