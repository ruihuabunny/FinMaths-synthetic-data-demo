"""Shared input models and scalar validation for BSM metric runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import math
import re
from typing import Any

from .profile import MetricSpec, _METRIC_SPECS

_QUOTE_QUANTUM = Decimal("0.00000001")
_DECIMAL_24_8_ABSOLUTE_LIMIT = Decimal("10000000000000000")
_ROW_ID_PATTERN = re.compile(r"^row_[0-9]{6}$")
_SIGNED_DECIMAL8_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_NONNEGATIVE_DECIMAL8_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.[0-9]{8}$"
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


def _binary64(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _quote_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{field} must be a decimal-compatible value")
    try:
        result = Decimal(str(value))
        stored = result.quantize(_QUOTE_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a finite DECIMAL(24,8) value") from error
    if not result.is_finite() or result != stored:
        raise ValueError(f"{field} must be exactly representable at 8 decimal places")
    if abs(stored) >= _DECIMAL_24_8_ABSOLUTE_LIMIT:
        raise ValueError(f"{field} must fit DECIMAL(24,8)")
    if stored < 0:
        raise ValueError(f"{field} must be non-negative")
    return stored


def _validate_decimal8(value: Any, spec: MetricSpec) -> None:
    if not isinstance(value, str) or not _SIGNED_DECIMAL8_PATTERN.fullmatch(value):
        raise ValueError(f"{spec.output_field} must be a canonical decimal8 string")
    if value == "-0.00000000":
        raise ValueError(f"{spec.output_field} must not contain negative zero")
    decimal_value = Decimal(value)
    if spec.decimal_constraint == "positive_decimal8" and decimal_value <= 0:
        raise ValueError(f"{spec.output_field} must be positive")
    if (
        spec.decimal_constraint == "nonnegative_decimal8"
        and not _NONNEGATIVE_DECIMAL8_PATTERN.fullmatch(value)
    ):
        raise ValueError(f"{spec.output_field} must be non-negative")


def _matches_any_task_id(value: str) -> bool:
    return any(re.fullmatch(spec.task_id_pattern, value) for spec in _METRIC_SPECS)


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
_SUBMISSION_FIELDS = (
    "task_id",
    "submission_schema_version",
    "method_id",
    "status",
    "rows",
)


@dataclass(frozen=True)
class BSMMarketMetricInput:
    """One validated solver-visible option quote joined to its market state."""

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
        if not isinstance(self.row_id, str) or not _ROW_ID_PATTERN.fullmatch(self.row_id):
            raise ValueError("row_id must use row_000001 format")
        if not isinstance(self.task_id, str) or not _matches_any_task_id(self.task_id):
            raise ValueError("task_id does not match a single-metric identity")
        for field in ("snapshot_id", "underlying_id", "option_id"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field):
                raise ValueError(f"{field} must be a non-empty string")
        object.__setattr__(
            self, "valuation_date", _date_value(self.valuation_date, "valuation_date")
        )
        object.__setattr__(self, "expiry", _date_value(self.expiry, "expiry"))
        if self.call_put not in {"call", "put"}:
            raise ValueError("call_put must be 'call' or 'put'")
        if self.currency != "USD":
            raise ValueError("the declared pricing measure and numeraire require USD")
        if self.calendar != "WeekendsOnly" or self.day_count != "Actual365Fixed":
            raise ValueError("single-metric task requires WeekendsOnly/Actual365Fixed")
        if self.exercise_style != "european" or self.settlement_type != "cash":
            raise ValueError("single-metric task requires cash-settled European options")

        spot = _binary64(self.spot, "spot")
        strike = _binary64(self.strike, "strike")
        tau = _binary64(self.time_to_expiry_actual365, "time_to_expiry_actual365")
        _binary64(self.risk_free_rate, "risk_free_rate")
        _binary64(self.dividend_yield, "dividend_yield")
        multiplier = _binary64(self.contract_multiplier, "contract_multiplier")
        bid = _quote_decimal(self.bid, "bid")
        ask = _quote_decimal(self.ask, "ask")
        if spot <= 0.0 or strike <= 0.0:
            raise ValueError("spot and strike must be positive")
        if tau <= 0.0:
            raise ValueError("time_to_expiry_actual365 must be positive")
        if multiplier <= 0.0:
            raise ValueError("contract_multiplier must be positive")
        elapsed_days = (self.expiry - self.valuation_date).days
        if elapsed_days <= 0 or tau != elapsed_days / 365.0:
            raise ValueError(
                "time_to_expiry_actual365 must equal positive calendar days / 365"
            )
        if bid > ask:
            raise ValueError("bid must not exceed ask")

    @property
    def canonical_order_key(self) -> tuple[Any, ...]:
        return (
            self.valuation_date,
            self.underlying_id,
            self.expiry,
            Decimal(str(self.strike)),
            self.call_put,
            self.option_id,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BSMMarketMetricInput:
        if not isinstance(value, Mapping) or set(value) != set(_INPUT_FIELDS):
            raise ValueError("single-metric input has missing or extra fields")
        return cls(**{field: value[field] for field in _INPUT_FIELDS})


__all__ = ["BSMMarketMetricInput"]
