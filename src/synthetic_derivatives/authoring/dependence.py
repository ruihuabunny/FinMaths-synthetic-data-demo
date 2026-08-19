"""Measure-qualified underlying-driver dependence contracts and validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from synthetic_derivatives.authoring.deterministic_functions import finite_float

if TYPE_CHECKING:
    from synthetic_derivatives.authoring.config_models import (
        QPricingConfig,
        UnderlyingConfig,
    )


@dataclass(frozen=True)
class UnderlyingDependenceConfig:
    """Canonical dependence contract for underlying spot drivers under P or Q."""

    dependence_spec_id: str
    measure: str
    driver_order: tuple[str, ...]
    formulation: str
    factor_loading_matrix: tuple[tuple[float, ...], ...]
    idiosyncratic_diagonal: tuple[float, ...]
    correlation_matrix: tuple[tuple[float, ...], ...]
    matrix_dtype: str
    factorization_method: str
    factorization_order: str
    time_grid: str
    regime_id: str
    source_dependence_spec_id: str | None = None
    mapping_id: str | None = None
    mapping_type: str | None = None
    risk_neutral_measure_id: str | None = None
    numeraire_id: str | None = None
    rate_path_id: str | None = None

    @property
    def factor_count(self) -> int:
        return len(self.factor_loading_matrix[0])

    def driver_index(self, underlying_id: str) -> int:
        try:
            return self.driver_order.index(underlying_id)
        except ValueError as error:
            raise ValueError(
                f"underlying driver is not declared: {underlying_id}"
            ) from error


def parse_measure_qualified_dependence_specs(
    raw: Any,
    underlyings: tuple[UnderlyingConfig, ...],
    q_pricing: QPricingConfig,
    *,
    schema_version: str,
) -> tuple[UnderlyingDependenceConfig, UnderlyingDependenceConfig]:
    """Parse a config-1.7+ P/Q pair and verify its measure-change mapping."""

    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(
            f"schema_version {schema_version} requires exactly two "
            "underlying_dependence_specs ordered as P then Q"
        )
    if [item.get("measure") if isinstance(item, dict) else None for item in raw] != [
        "P",
        "Q",
    ]:
        raise ValueError(
            "underlying_dependence_specs must use exact declared order P then Q"
        )
    physical = parse_underlying_dependence_spec(
        raw[0], underlyings, expected_measure="P",
        field_name="underlying_dependence_specs[0]",
    )
    pricing = parse_underlying_dependence_spec(
        raw[1], underlyings, expected_measure="Q",
        field_name="underlying_dependence_specs[1]",
    )
    if pricing.dependence_spec_id == physical.dependence_spec_id:
        raise ValueError("P and Q dependence_spec_id values must be distinct")
    if pricing.source_dependence_spec_id != physical.dependence_spec_id:
        raise ValueError(
            "Q source_dependence_spec_id must identify the declared P spec"
        )
    expected_q_context = (
        q_pricing.risk_neutral_measure_id,
        q_pricing.numeraire_id,
        q_pricing.rate_path_id,
    )
    actual_q_context = (
        pricing.risk_neutral_measure_id,
        pricing.numeraire_id,
        pricing.rate_path_id,
    )
    if actual_q_context != expected_q_context:
        raise ValueError(
            "Q dependence measure/numeraire/rate-path IDs must match q_pricing"
        )
    covariance_fields = (
        "driver_order", "formulation", "factor_loading_matrix",
        "idiosyncratic_diagonal", "correlation_matrix", "matrix_dtype",
        "factorization_method", "factorization_order", "time_grid", "regime_id",
    )
    if any(
        getattr(pricing, field_name) != getattr(physical, field_name)
        for field_name in covariance_fields
    ):
        raise ValueError(
            "girsanov_drift_only_same_brownian_covariance requires identical "
            "P/Q driver order, Lambda, D, R, dtype, factor order, time grid and regime"
        )
    return physical, pricing


def parse_underlying_dependence_spec(
    raw: Any,
    underlyings: tuple[UnderlyingConfig, ...],
    *,
    expected_measure: str,
    field_name: str,
) -> UnderlyingDependenceConfig:
    """Validate Lambda and deterministically derive D and R in float64 order."""

    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be an object")
    dependence_spec_id = raw.get("dependence_spec_id")
    if not isinstance(dependence_spec_id, str) or not dependence_spec_id:
        raise ValueError(f"{field_name}.dependence_spec_id must be non-empty")
    if raw.get("measure") != expected_measure:
        raise ValueError(f"{field_name}.measure must be {expected_measure}")
    fixed_values = {
        "formulation": "factor_loading",
        "idiosyncratic_diagonal": "derive_from_row_norms",
        "matrix_dtype": "float64",
        "factorization_method": "factor_loading_direct",
        "factorization_order": "declared_driver_order",
        "time_grid": "business_daily",
    }
    for name, expected in fixed_values.items():
        if raw.get(name) != expected:
            raise ValueError(f"{field_name}.{name} must be {expected}")
    if "correlation_matrix" in raw:
        raise ValueError(
            f"{field_name}.correlation_matrix is derived from factor loadings"
        )
    regime_id = raw.get("regime_id")
    if not isinstance(regime_id, str) or not regime_id:
        raise ValueError(f"{field_name}.regime_id must be non-empty")

    mapping_fields = (
        "source_dependence_spec_id", "mapping_id", "mapping_type",
        "risk_neutral_measure_id", "numeraire_id", "rate_path_id",
    )
    mapping_values: dict[str, str | None]
    if expected_measure == "P":
        if any(name in raw for name in mapping_fields):
            raise ValueError(f"{field_name} P spec must not contain Q mapping fields")
        mapping_values = {name: None for name in mapping_fields}
    else:
        mapping_values = {}
        for name in mapping_fields:
            value = raw.get(name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name}.{name} must be non-empty")
            mapping_values[name] = value
        if mapping_values["mapping_type"] != (
            "girsanov_drift_only_same_brownian_covariance"
        ):
            raise ValueError(
                f"{field_name}.mapping_type must be "
                "girsanov_drift_only_same_brownian_covariance"
            )

    raw_driver_order = raw.get("driver_order")
    if not isinstance(raw_driver_order, list) or not raw_driver_order or any(
        not isinstance(driver, str) or not driver for driver in raw_driver_order
    ):
        raise ValueError(
            f"{field_name}.driver_order must be a non-empty string array"
        )
    driver_order = tuple(raw_driver_order)
    if len(driver_order) != len(set(driver_order)):
        raise ValueError(f"{field_name}.driver_order must be unique")
    underlying_ids = {underlying.underlying_id for underlying in underlyings}
    if set(driver_order) - underlying_ids:
        raise ValueError(
            f"{field_name}.driver_order may contain underlying IDs only; "
            "option/derivative drivers are forbidden"
        )
    if underlying_ids - set(driver_order):
        raise ValueError(
            f"{field_name}.driver_order must contain every underlying_id exactly once"
        )

    raw_matrix = raw.get("factor_loading_matrix")
    if not isinstance(raw_matrix, list) or len(raw_matrix) != len(driver_order):
        raise ValueError(
            f"{field_name}.factor_loading_matrix row count must match driver_order"
        )
    if not raw_matrix or any(not isinstance(row, list) for row in raw_matrix):
        raise ValueError(f"{field_name}.factor_loading_matrix must be a matrix")
    factor_count = len(raw_matrix[0])
    if factor_count < 1 or any(len(row) != factor_count for row in raw_matrix):
        raise ValueError(
            f"{field_name}.factor_loading_matrix must be non-ragged with at least one factor"
        )

    factor_loading_rows: list[tuple[float, ...]] = []
    idiosyncratic_diagonal: list[float] = []
    for row_index, raw_row in enumerate(raw_matrix):
        row = tuple(
            finite_float(
                value,
                field_name=(
                    f"{field_name}.factor_loading_matrix"
                    f"[{row_index}][{column_index}]"
                ),
            )
            for column_index, value in enumerate(raw_row)
        )
        exact_row_norm_squared = sum(
            (Decimal(str(value)) ** 2 for value in raw_row), Decimal("0")
        )
        if exact_row_norm_squared > Decimal("1"):
            raise ValueError(
                f"{field_name} factor-loading row norm must not exceed 1: "
                f"{driver_order[row_index]}"
            )
        row_norm_squared = math.fsum(value * value for value in row)
        if row_norm_squared > 1.0:
            raise ValueError(
                f"{field_name} factor-loading row norm exceeds 1 in float64: "
                f"{driver_order[row_index]}"
            )
        factor_loading_rows.append(row)
        idiosyncratic_diagonal.append(1.0 - row_norm_squared)

    factor_loading_matrix = tuple(factor_loading_rows)
    diagonal = tuple(idiosyncratic_diagonal)
    correlation_matrix = tuple(
        tuple(
            1.0 if row_index == column_index else math.fsum(
                factor_loading_matrix[row_index][factor_index]
                * factor_loading_matrix[column_index][factor_index]
                for factor_index in range(factor_count)
            )
            for column_index in range(len(driver_order))
        )
        for row_index in range(len(driver_order))
    )
    if any(
        not math.isfinite(value) or not -1.0 <= value <= 1.0
        for row in correlation_matrix for value in row
    ):
        raise ValueError(f"{field_name}.correlation_matrix must be finite and in [-1, 1]")
    if any(
        correlation_matrix[row_index][column_index]
        != correlation_matrix[column_index][row_index]
        for row_index in range(len(driver_order))
        for column_index in range(len(driver_order))
    ):
        raise ValueError(f"{field_name}.correlation_matrix must be exactly symmetric")
    if any(
        correlation_matrix[index][index] != 1.0
        for index in range(len(driver_order))
    ):
        raise ValueError(f"{field_name}.correlation_matrix must have exact unit diagonal")

    return UnderlyingDependenceConfig(
        dependence_spec_id=dependence_spec_id,
        measure=expected_measure,
        driver_order=driver_order,
        formulation="factor_loading",
        factor_loading_matrix=factor_loading_matrix,
        idiosyncratic_diagonal=diagonal,
        correlation_matrix=correlation_matrix,
        matrix_dtype="float64",
        factorization_method="factor_loading_direct",
        factorization_order="declared_driver_order",
        time_grid="business_daily",
        regime_id=regime_id,
        source_dependence_spec_id=mapping_values["source_dependence_spec_id"],
        mapping_id=mapping_values["mapping_id"],
        mapping_type=mapping_values["mapping_type"],
        risk_neutral_measure_id=mapping_values["risk_neutral_measure_id"],
        numeraire_id=mapping_values["numeraire_id"],
        rate_path_id=mapping_values["rate_path_id"],
    )
