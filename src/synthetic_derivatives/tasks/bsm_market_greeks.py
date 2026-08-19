"""Shared input and canonical output contract for market-implied BSM Greeks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re
from typing import Any, Mapping

from synthetic_derivatives.tasks.bsm_greeks import (
    BSMGreeksInput,
    BSMGreeksValues,
    canonical_decimal,
)
from synthetic_derivatives.tasks.bsm_implied_volatility import (
    BSMImpliedVolatilityInput,
    normalize_bsm_iv_input,
)


BSM_MARKET_GREEKS_VARIANT_ID = "bsm_market_implied_greeks_v1"
BSM_MARKET_GREEKS_METHOD_ID = "bsm-mid-iv-bisection80-analytic-greeks-v1"
BSM_MARKET_GREEKS_OUTPUT_CONTRACT_ID = "bsm-market-implied-greeks-output-v1"
BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION = (
    "bsm-market-implied-greeks-submission-v2.0.0"
)
BSM_MARKET_GREEKS_SUCCESS_STATUS = "CONVERGED_FIXED_ITERATIONS"


_ROW_ID_PATTERN = re.compile(r"^row_[0-9]{6}$")
_TASK_ID_PATTERN = re.compile(r"^bsm-mig-v2-[0-9a-f]{24}$")
_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_NONNEGATIVE_DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_INPUT_FIELDS = (
    "row_id",
    "task_id",
    "snapshot_id",
    "valuation_date",
    "underlying_id",
    "option_id",
    "call_put",
    "spot",
    "strike",
    "expiry",
    "time_to_expiry_actual365",
    "bid",
    "ask",
    "contract_multiplier",
    "currency",
    "risk_free_rate",
    "dividend_yield",
    "calendar",
    "day_count",
    "exercise_style",
    "settlement_type",
)
_RESULT_FIELDS = (
    "row_id",
    "iv_status",
    "market_implied_volatility",
    "unit_delta",
    "unit_gamma",
    "unit_vega_1volpt",
    "unit_theta_1calendar_day",
    "unit_rho_1pct",
)
_SUBMISSION_FIELDS = (
    "task_id",
    "submission_schema_version",
    "method_id",
    "status",
    "rows",
)


def _date_value(value: Any, field: str) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} must be an ISO date") from error
    raise ValueError(f"{field} must be a date")


def _decimal8_text(value: Any, field: str) -> str:
    try:
        decimal = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{field} must be decimal-compatible") from error
    if not decimal.is_finite() or decimal.as_tuple().exponent < -8:
        raise ValueError(f"{field} must fit the 8-place public decimal contract")
    return format(decimal.quantize(Decimal("0.00000001")), "f")


@dataclass(frozen=True)
class BSMMarketGreeksInput:
    """One public quote row under the common-Q European BSM contract."""

    row_id: str
    task_id: str
    snapshot_id: str
    valuation_date: date
    underlying_id: str
    option_id: str
    call_put: str
    spot: Any
    strike: Any
    expiry: date
    time_to_expiry_actual365: Any
    bid: Any
    ask: Any
    contract_multiplier: Any
    currency: str
    risk_free_rate: Any
    dividend_yield: Any
    calendar: str
    day_count: str
    exercise_style: str
    settlement_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.row_id, str) or not _ROW_ID_PATTERN.fullmatch(
            self.row_id
        ):
            raise ValueError("row_id must use row_000001 format")
        if not isinstance(self.task_id, str) or not _TASK_ID_PATTERN.fullmatch(
            self.task_id
        ):
            raise ValueError("task_id does not match the market-Greeks identity")
        for field in ("snapshot_id", "underlying_id", "option_id"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field):
                raise ValueError(f"{field} must be a non-empty string")
        object.__setattr__(
            self, "valuation_date", _date_value(self.valuation_date, "valuation_date")
        )
        object.__setattr__(self, "expiry", _date_value(self.expiry, "expiry"))
        if self.calendar != "WeekendsOnly":
            raise ValueError("market-Greeks task requires the WeekendsOnly calendar")
        if self.day_count != "Actual365Fixed":
            raise ValueError("market-Greeks task requires Actual365Fixed")
        normalize_bsm_iv_input(self.as_iv_input())

    @property
    def canonical_order_key(self) -> tuple[Any, ...]:
        return (
            self.valuation_date,
            self.underlying_id,
            self.expiry,
            float(self.strike),
            self.call_put,
            self.option_id,
        )

    def as_iv_input(self) -> BSMImpliedVolatilityInput:
        return BSMImpliedVolatilityInput(
            task_id=self.task_id,
            valuation_date=self.valuation_date,
            underlying_id=self.underlying_id,
            option_id=self.option_id,
            call_put=self.call_put,
            spot=self.spot,
            strike=self.strike,
            expiry=self.expiry,
            time_to_expiry_actual365=self.time_to_expiry_actual365,
            risk_free_rate=self.risk_free_rate,
            dividend_yield=self.dividend_yield,
            bid=self.bid,
            ask=self.ask,
            contract_multiplier=self.contract_multiplier,
            currency=self.currency,
            exercise_style=self.exercise_style,
            settlement_type=self.settlement_type,
        )

    def as_greeks_input(self, sigma: float) -> BSMGreeksInput:
        return BSMGreeksInput(
            task_id=self.task_id,
            valuation_date=self.valuation_date,
            underlying_id=self.underlying_id,
            option_id=self.option_id,
            call_put=self.call_put,
            spot=float(self.spot),
            strike=float(self.strike),
            expiry=self.expiry,
            time_to_expiry_actual365=float(self.time_to_expiry_actual365),
            risk_free_rate=float(self.risk_free_rate),
            dividend_yield=float(self.dividend_yield),
            sigma=sigma,
            currency=self.currency,
            exercise_style=self.exercise_style,
            settlement_type=self.settlement_type,
        )

    def to_tool_mapping(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "task_id": self.task_id,
            "snapshot_id": self.snapshot_id,
            "valuation_date": self.valuation_date.isoformat(),
            "underlying_id": self.underlying_id,
            "option_id": self.option_id,
            "call_put": self.call_put,
            "spot": _decimal8_text(self.spot, "spot"),
            "strike": _decimal8_text(self.strike, "strike"),
            "expiry": self.expiry.isoformat(),
            "time_to_expiry_actual365": float(self.time_to_expiry_actual365),
            "bid": _decimal8_text(self.bid, "bid"),
            "ask": _decimal8_text(self.ask, "ask"),
            "contract_multiplier": _decimal8_text(
                self.contract_multiplier, "contract_multiplier"
            ),
            "currency": self.currency,
            "risk_free_rate": float(self.risk_free_rate),
            "dividend_yield": float(self.dividend_yield),
            "calendar": self.calendar,
            "day_count": self.day_count,
            "exercise_style": self.exercise_style,
            "settlement_type": self.settlement_type,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BSMMarketGreeksInput":
        if set(value) != set(_INPUT_FIELDS):
            raise ValueError("market-Greeks input has missing or extra fields")
        return cls(**{field: value[field] for field in _INPUT_FIELDS})


@dataclass(frozen=True)
class CanonicalMarketGreeksRow:
    row_id: str
    iv_status: str
    market_implied_volatility: str
    unit_delta: str
    unit_gamma: str
    unit_vega_1volpt: str
    unit_theta_1calendar_day: str
    unit_rho_1pct: str

    def __post_init__(self) -> None:
        if not isinstance(self.row_id, str) or not _ROW_ID_PATTERN.fullmatch(
            self.row_id
        ):
            raise ValueError("canonical result has an invalid row_id")
        if self.iv_status != BSM_MARKET_GREEKS_SUCCESS_STATUS:
            raise ValueError("canonical result has an invalid IV status")
        if (
            not isinstance(self.market_implied_volatility, str)
            or not _NONNEGATIVE_DECIMAL_PATTERN.fullmatch(
                self.market_implied_volatility
            )
            or Decimal(self.market_implied_volatility) <= 0
        ):
            raise ValueError("market implied volatility must be positive decimal8")
        for field in (
            "unit_delta",
            "unit_gamma",
            "unit_vega_1volpt",
            "unit_theta_1calendar_day",
            "unit_rho_1pct",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not _DECIMAL_PATTERN.fullmatch(value):
                raise ValueError(f"{field} must be a canonical decimal8 string")
            if value == "-0.00000000":
                raise ValueError(f"{field} must not contain negative zero")
        for field in ("unit_gamma", "unit_vega_1volpt"):
            if not _NONNEGATIVE_DECIMAL_PATTERN.fullmatch(getattr(self, field)):
                raise ValueError(f"{field} must be non-negative")

    def to_dict(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in _RESULT_FIELDS}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanonicalMarketGreeksRow":
        if set(value) != set(_RESULT_FIELDS):
            raise ValueError("market-Greeks row has missing or extra fields")
        return cls(**{field: value[field] for field in _RESULT_FIELDS})


@dataclass(frozen=True)
class MarketGreeksSubmission:
    task_id: str
    rows: tuple[CanonicalMarketGreeksRow, ...]
    submission_schema_version: str = BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION
    method_id: str = BSM_MARKET_GREEKS_METHOD_ID
    status: str = "completed"

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _TASK_ID_PATTERN.fullmatch(
            self.task_id
        ):
            raise ValueError("submission task_id is invalid")
        if self.submission_schema_version != (
            BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION
        ):
            raise ValueError("submission schema version is invalid")
        if self.method_id != BSM_MARKET_GREEKS_METHOD_ID:
            raise ValueError("submission method ID is invalid")
        if self.status != "completed":
            raise ValueError("submission status must be completed")
        if not self.rows:
            raise ValueError("submission must contain rows")
        row_ids = [row.row_id for row in self.rows]
        expected = [f"row_{index:06d}" for index in range(1, len(self.rows) + 1)]
        if row_ids != expected:
            raise ValueError("submission rows are missing, duplicated, or reordered")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "submission_schema_version": self.submission_schema_version,
            "method_id": self.method_id,
            "status": self.status,
            "rows": [row.to_dict() for row in self.rows],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MarketGreeksSubmission":
        if set(value) != set(_SUBMISSION_FIELDS):
            raise ValueError("market-Greeks submission has missing or extra fields")
        raw_rows = value["rows"]
        if not isinstance(raw_rows, list):
            raise ValueError("submission rows must be an array")
        return cls(
            task_id=value["task_id"],
            submission_schema_version=value["submission_schema_version"],
            method_id=value["method_id"],
            status=value["status"],
            rows=tuple(CanonicalMarketGreeksRow.from_mapping(row) for row in raw_rows),
        )


def canonical_market_greeks_row(
    task_input: BSMMarketGreeksInput,
    sigma: float,
    values: BSMGreeksValues,
) -> CanonicalMarketGreeksRow:
    return CanonicalMarketGreeksRow(
        row_id=task_input.row_id,
        iv_status=BSM_MARKET_GREEKS_SUCCESS_STATUS,
        market_implied_volatility=canonical_decimal(sigma),
        unit_delta=canonical_decimal(values.delta),
        unit_gamma=canonical_decimal(values.gamma),
        unit_vega_1volpt=canonical_decimal(values.vega),
        unit_theta_1calendar_day=canonical_decimal(values.theta),
        unit_rho_1pct=canonical_decimal(values.rho),
    )


__all__ = [
    "BSM_MARKET_GREEKS_METHOD_ID",
    "BSM_MARKET_GREEKS_OUTPUT_CONTRACT_ID",
    "BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION",
    "BSM_MARKET_GREEKS_SUCCESS_STATUS",
    "BSM_MARKET_GREEKS_VARIANT_ID",
    "BSMMarketGreeksInput",
    "CanonicalMarketGreeksRow",
    "MarketGreeksSubmission",
    "canonical_market_greeks_row",
]
