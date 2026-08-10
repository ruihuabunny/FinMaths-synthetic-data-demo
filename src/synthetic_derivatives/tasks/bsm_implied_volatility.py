"""Shared contract and canonicalization for scalar BSM IV inversion.

This module owns task-input validation, Decimal quote midpoint semantics,
method identities, statuses, and output serialization.  It deliberately owns
no BSM pricing function and no root-finding implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
import math
import re
from typing import Any, Mapping

from synthetic_derivatives.tasks.bsm_greeks import canonical_decimal


BSM_IV_VARIANT_ID = "bsm_iv_scalar_v1"
BSM_IV_METHOD_ID = "bsm-bisection-float64-80-v1"
BSM_IV_CONVENTION_ID = "bsm-iv-actual365-flat-continuous-v1"
BSM_IV_OUTPUT_CONTRACT_ID = "bsm-iv-scalar-output-v1"
BSM_IV_SCHEMA_VERSION = "bsm-implied-volatility-output-v1.0.0"
RISK_NEUTRAL_MEASURE_ID = "USD-MONEY-MARKET-Q-v1"
NUMERAIRE_ID = "USD-MONEY-MARKET-ACCOUNT-v1"

BSM_IV_OK = "OK"
BSM_IV_INVALID_INPUT = "INVALID_INPUT"
BSM_IV_OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
BSM_IV_NO_BRACKET = "NO_BRACKET"
BSM_IV_STATUSES = frozenset(
    {
        BSM_IV_OK,
        BSM_IV_INVALID_INPUT,
        BSM_IV_OUT_OF_BOUNDS,
        BSM_IV_NO_BRACKET,
    }
)

LOWER_VOLATILITY = 0.000001
UPPER_VOLATILITY = 5.0
BISECTION_ITERATIONS = 80
QUOTE_QUANTUM = Decimal("0.00000001")
OBSERVED_PRICE_QUANTUM = Decimal("0.000000001")
DECIMAL_24_8_ABSOLUTE_LIMIT = Decimal("10000000000000000")


_OBSERVED_PRICE_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{9}$")
_IV_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_OUTPUT_FIELDS = (
    "task_id",
    "valuation_date",
    "underlying_id",
    "option_id",
    "call_put",
    "expiry",
    "status",
    "observed_price",
    "implied_volatility",
    "iterations",
    "method_id",
    "convention_id",
    "output_contract_id",
)


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _binary64(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _quote_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(
        value, (str, int, float, Decimal)
    ):
        raise ValueError(f"{field} must be a decimal-compatible value")
    try:
        result = Decimal(str(value))
        stored = result.quantize(QUOTE_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a finite DECIMAL(24,8) value") from error
    if not result.is_finite() or result != stored:
        raise ValueError(f"{field} must be exactly representable at 8 decimal places")
    if abs(stored) >= DECIMAL_24_8_ABSOLUTE_LIMIT:
        raise ValueError(f"{field} must fit DECIMAL(24,8)")
    if result < 0:
        raise ValueError(f"{field} must be non-negative")
    return stored


@dataclass(frozen=True)
class BSMImpliedVolatilityInput:
    """One solver-visible quote row for a scalar European BSM IV inverse.

    Numerical fields are intentionally validated by ``normalize_bsm_iv_input``
    so model-domain failures have a canonical ``INVALID_INPUT`` result.  Row
    identities and non-numerical contract conventions must still be valid to
    identify such a result unambiguously.
    """

    task_id: str
    valuation_date: date
    underlying_id: str
    option_id: str
    call_put: str
    spot: Any
    strike: Any
    expiry: date
    time_to_expiry_actual365: Any
    risk_free_rate: Any
    dividend_yield: Any
    bid: Any
    ask: Any
    contract_multiplier: Any = 1.0
    currency: str = "USD"
    exercise_style: str = "european"
    settlement_type: str = "cash"

    def __post_init__(self) -> None:
        for field in ("task_id", "underlying_id", "option_id"):
            _nonempty_string(getattr(self, field), field)
        if type(self.valuation_date) is not date or type(self.expiry) is not date:
            raise ValueError("valuation_date and expiry must be datetime.date values")
        if self.call_put not in {"call", "put"}:
            raise ValueError("call_put must be 'call' or 'put'")
        if self.currency != "USD":
            raise ValueError("the declared pricing measure and numeraire require USD")
        if self.exercise_style != "european":
            raise ValueError("BSM IV inversion requires European exercise")
        if self.settlement_type != "cash":
            raise ValueError("BSM IV inversion requires cash settlement")


@dataclass(frozen=True)
class NormalizedBSMImpliedVolatilityInput:
    """Validated binary64 inputs and the exact Decimal quote midpoint."""

    task_id: str
    valuation_date: date
    underlying_id: str
    option_id: str
    call_put: str
    spot: float
    strike: float
    expiry: date
    time_to_expiry_actual365: float
    risk_free_rate: float
    dividend_yield: float
    bid: Decimal
    ask: Decimal
    observed_price_decimal: Decimal
    observed_price: float
    contract_multiplier: float


def normalize_bsm_iv_input(
    task_input: BSMImpliedVolatilityInput,
) -> NormalizedBSMImpliedVolatilityInput:
    """Apply the sole public-quote midpoint and binary64 cast checkpoints."""

    spot = _binary64(task_input.spot, "spot")
    strike = _binary64(task_input.strike, "strike")
    tau = _binary64(
        task_input.time_to_expiry_actual365,
        "time_to_expiry_actual365",
    )
    risk_free_rate = _binary64(task_input.risk_free_rate, "risk_free_rate")
    dividend_yield = _binary64(task_input.dividend_yield, "dividend_yield")
    contract_multiplier = _binary64(
        task_input.contract_multiplier,
        "contract_multiplier",
    )
    bid = _quote_decimal(task_input.bid, "bid")
    ask = _quote_decimal(task_input.ask, "ask")

    if spot <= 0.0:
        raise ValueError("spot must be positive")
    if strike <= 0.0:
        raise ValueError("strike must be positive")
    if tau <= 0.0:
        raise ValueError("time_to_expiry_actual365 must be positive")
    if contract_multiplier <= 0.0:
        raise ValueError("contract_multiplier must be positive")
    elapsed_days = (task_input.expiry - task_input.valuation_date).days
    if elapsed_days <= 0:
        raise ValueError("expiry must be after valuation_date")
    if tau != elapsed_days / 365.0:
        raise ValueError(
            "time_to_expiry_actual365 must equal calendar days / 365"
        )
    if bid > ask:
        raise ValueError("bid must not exceed ask")

    observed_decimal = (bid + ask) / Decimal(2)
    observed_price = float(observed_decimal)
    if not math.isfinite(observed_price):
        raise ValueError("observed midpoint must be finite in binary64")

    return NormalizedBSMImpliedVolatilityInput(
        task_id=task_input.task_id,
        valuation_date=task_input.valuation_date,
        underlying_id=task_input.underlying_id,
        option_id=task_input.option_id,
        call_put=task_input.call_put,
        spot=spot,
        strike=strike,
        expiry=task_input.expiry,
        time_to_expiry_actual365=tau,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        bid=bid,
        ask=ask,
        observed_price_decimal=observed_decimal,
        observed_price=observed_price,
        contract_multiplier=contract_multiplier,
    )


@dataclass(frozen=True)
class BSMImpliedVolatilityContract:
    """Immutable inverse-problem and failure-status contract."""

    pricing_measure_id: str = RISK_NEUTRAL_MEASURE_ID
    numeraire_id: str = NUMERAIRE_ID
    pricing_law: str = "exact_analytic_constant_parameter_bsm"
    method_id: str = BSM_IV_METHOD_ID
    input_price_rule: str = "decimal_bid_ask_midpoint_then_one_binary64_cast"
    lower_volatility: float = LOWER_VOLATILITY
    upper_volatility: float = UPPER_VOLATILITY
    iterations: int = BISECTION_ITERATIONS
    midpoint_rule: str = "binary64_(low_plus_high)_divided_by_2"
    comparison_rule: str = "price_mid_lt_observed_updates_low_else_high"
    final_root_rule: str = "binary64_(low_80_plus_high_80)_divided_by_2"
    price_bounds_rule: str = "discounted_european_bsm_finite_iv_bounds_v1"
    bracket_endpoint_rule: str = "closed"
    early_stop: bool = False
    fallback_method: None = None
    input_dtype: str = "binary64_after_declared_decimal_midpoint"
    output_precision: int = 8
    rounding: str = "ROUND_HALF_EVEN"

    def __post_init__(self) -> None:
        expected = {
            "pricing_measure_id": RISK_NEUTRAL_MEASURE_ID,
            "numeraire_id": NUMERAIRE_ID,
            "pricing_law": "exact_analytic_constant_parameter_bsm",
            "method_id": BSM_IV_METHOD_ID,
            "input_price_rule": (
                "decimal_bid_ask_midpoint_then_one_binary64_cast"
            ),
            "midpoint_rule": "binary64_(low_plus_high)_divided_by_2",
            "comparison_rule": "price_mid_lt_observed_updates_low_else_high",
            "final_root_rule": (
                "binary64_(low_80_plus_high_80)_divided_by_2"
            ),
            "price_bounds_rule": (
                "discounted_european_bsm_finite_iv_bounds_v1"
            ),
            "bracket_endpoint_rule": "closed",
            "input_dtype": "binary64_after_declared_decimal_midpoint",
            "rounding": "ROUND_HALF_EVEN",
        }
        if any(getattr(self, field) != value for field, value in expected.items()):
            raise ValueError("unknown frozen BSM IV method contract")
        if (
            self.lower_volatility != LOWER_VOLATILITY
            or self.upper_volatility != UPPER_VOLATILITY
            or not 0.0 < self.lower_volatility < self.upper_volatility
        ):
            raise ValueError("BSM IV volatility bracket must be [1e-6, 5.0]")
        if type(self.iterations) is not int or self.iterations != BISECTION_ITERATIONS:
            raise ValueError("BSM IV inversion requires exactly 80 iterations")
        if self.early_stop is not False or self.fallback_method is not None:
            raise ValueError("BSM IV inversion forbids early stop and fallback")
        if type(self.output_precision) is not int or self.output_precision != 8:
            raise ValueError("BSM IV output precision must be 8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pricing_measure_id": self.pricing_measure_id,
            "numeraire_id": self.numeraire_id,
            "pricing_law": self.pricing_law,
            "method_id": self.method_id,
            "input_price_rule": self.input_price_rule,
            "volatility_bracket": [
                self.lower_volatility,
                self.upper_volatility,
            ],
            "iterations": self.iterations,
            "midpoint_rule": self.midpoint_rule,
            "comparison_rule": self.comparison_rule,
            "final_root_rule": self.final_root_rule,
            "price_bounds_rule": self.price_bounds_rule,
            "finite_iv_price_domain": "lower_inclusive_upper_exclusive",
            "bracket_endpoint_rule": self.bracket_endpoint_rule,
            "early_stop": self.early_stop,
            "fallback_method": self.fallback_method,
            "input_dtype": self.input_dtype,
            "output_precision": self.output_precision,
            "rounding": self.rounding,
            "statuses": {
                BSM_IV_OK: "finite_root_in_closed_bracket_after_80_updates",
                BSM_IV_INVALID_INPUT: "input_domain_or_quote_contract_failure",
                BSM_IV_OUT_OF_BOUNDS: (
                    "outside_discounted_BSM_finite_iv_price_domain"
                ),
                BSM_IV_NO_BRACKET: (
                    "inside_price_domain_but_not_numerically_bracketed_by_[1e-6,5.0]"
                ),
            },
            "failure_iterations": 0,
            "persisted_intermediates": [],
        }

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any]
    ) -> BSMImpliedVolatilityContract:
        expected_fields = set(cls().to_dict())
        if set(raw) != expected_fields:
            raise ValueError("BSM IV contract has missing or extra fields")
        bracket = raw["volatility_bracket"]
        if not isinstance(bracket, list) or len(bracket) != 2:
            raise ValueError("volatility_bracket must contain two endpoints")
        if raw["statuses"] != cls().to_dict()["statuses"]:
            raise ValueError("unknown BSM IV status contract")
        if raw["finite_iv_price_domain"] != "lower_inclusive_upper_exclusive":
            raise ValueError("unknown BSM IV finite-price domain")
        if raw["failure_iterations"] != 0:
            raise ValueError("failed BSM IV inversions must report zero iterations")
        if raw["persisted_intermediates"] != []:
            raise ValueError("BSM IV contract cannot persist pricing intermediates")
        return cls(
            pricing_measure_id=raw["pricing_measure_id"],
            numeraire_id=raw["numeraire_id"],
            pricing_law=raw["pricing_law"],
            method_id=raw["method_id"],
            input_price_rule=raw["input_price_rule"],
            lower_volatility=bracket[0],
            upper_volatility=bracket[1],
            iterations=raw["iterations"],
            midpoint_rule=raw["midpoint_rule"],
            comparison_rule=raw["comparison_rule"],
            final_root_rule=raw["final_root_rule"],
            price_bounds_rule=raw["price_bounds_rule"],
            bracket_endpoint_rule=raw["bracket_endpoint_rule"],
            early_stop=raw["early_stop"],
            fallback_method=raw["fallback_method"],
            input_dtype=raw["input_dtype"],
            output_precision=raw["output_precision"],
            rounding=raw["rounding"],
        )


DEFAULT_BSM_IV_CONTRACT = BSMImpliedVolatilityContract()


def bsm_iv_contract() -> dict[str, Any]:
    """Return the canonical JSON-ready IV inverse-problem contract."""

    return DEFAULT_BSM_IV_CONTRACT.to_dict()


def bsm_iv_contract_json() -> str:
    return json.dumps(
        bsm_iv_contract(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_observed_price(value: Decimal) -> str:
    """Serialize the exact midpoint of two 8-place public quotes."""

    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("observed price must be a finite non-negative Decimal")
    try:
        quantized = value.quantize(
            OBSERVED_PRICE_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
    except InvalidOperation as error:
        raise ValueError("observed price exceeds decimal contract") from error
    if value != quantized:
        raise ValueError("observed midpoint must be exact at 9 decimal places")
    return format(quantized, "f")


@dataclass(frozen=True)
class CanonicalBSMImpliedVolatilityResult:
    """Exact-comparison scalar IV result, including canonical failures."""

    task_id: str
    valuation_date: str
    underlying_id: str
    option_id: str
    call_put: str
    expiry: str
    status: str
    observed_price: str | None
    implied_volatility: str | None
    iterations: int
    method_id: str = BSM_IV_METHOD_ID
    convention_id: str = BSM_IV_CONVENTION_ID
    output_contract_id: str = BSM_IV_OUTPUT_CONTRACT_ID

    def __post_init__(self) -> None:
        for field in ("task_id", "underlying_id", "option_id"):
            _nonempty_string(getattr(self, field), field)
        for field in ("valuation_date", "expiry"):
            try:
                date.fromisoformat(getattr(self, field))
            except (TypeError, ValueError) as error:
                raise ValueError(f"{field} must be an ISO date") from error
        if self.call_put not in {"call", "put"}:
            raise ValueError("call_put must be 'call' or 'put'")
        if self.status not in BSM_IV_STATUSES:
            raise ValueError("unknown BSM IV status")
        if self.method_id != BSM_IV_METHOD_ID:
            raise ValueError("method_id does not match the BSM IV contract")
        if self.convention_id != BSM_IV_CONVENTION_ID:
            raise ValueError("convention_id does not match the BSM IV contract")
        if self.output_contract_id != BSM_IV_OUTPUT_CONTRACT_ID:
            raise ValueError("output_contract_id does not match the BSM IV schema")

        if self.status == BSM_IV_INVALID_INPUT:
            if self.observed_price is not None:
                raise ValueError("INVALID_INPUT must not publish an observed price")
        elif not isinstance(self.observed_price, str) or not (
            _OBSERVED_PRICE_PATTERN.fullmatch(self.observed_price)
        ):
            raise ValueError("status requires a canonical 9-place observed price")

        if self.status == BSM_IV_OK:
            if not isinstance(self.implied_volatility, str) or not (
                _IV_PATTERN.fullmatch(self.implied_volatility)
            ):
                raise ValueError("OK requires a canonical 8-place IV")
            implied_volatility = Decimal(self.implied_volatility)
            if not Decimal(str(LOWER_VOLATILITY)) <= implied_volatility <= Decimal(
                str(UPPER_VOLATILITY)
            ):
                raise ValueError("canonical IV lies outside the frozen bracket")
            if self.iterations != BISECTION_ITERATIONS:
                raise ValueError("OK requires exactly 80 iterations")
        elif self.implied_volatility is not None or self.iterations != 0:
            raise ValueError("failed IV inversion must publish null IV and 0 iterations")

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in _OUTPUT_FIELDS}

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> CanonicalBSMImpliedVolatilityResult:
        if set(value) != set(_OUTPUT_FIELDS):
            raise ValueError("canonical BSM IV row has missing or extra fields")
        return cls(**{field: value[field] for field in _OUTPUT_FIELDS})


def canonicalize_bsm_iv_result(
    task_input: BSMImpliedVolatilityInput,
    *,
    status: str,
    observed_price: Decimal | None = None,
    implied_volatility: float | None = None,
    iterations: int = 0,
) -> CanonicalBSMImpliedVolatilityResult:
    """Apply the sole IV float-to-public-decimal publication checkpoint."""

    return CanonicalBSMImpliedVolatilityResult(
        task_id=task_input.task_id,
        valuation_date=task_input.valuation_date.isoformat(),
        underlying_id=task_input.underlying_id,
        option_id=task_input.option_id,
        call_put=task_input.call_put,
        expiry=task_input.expiry.isoformat(),
        status=status,
        observed_price=(
            None
            if observed_price is None
            else canonical_observed_price(observed_price)
        ),
        implied_volatility=(
            None
            if implied_volatility is None
            else canonical_decimal(implied_volatility)
        ),
        iterations=iterations,
    )


__all__ = [
    "BISECTION_ITERATIONS",
    "BSM_IV_CONVENTION_ID",
    "BSM_IV_INVALID_INPUT",
    "BSM_IV_METHOD_ID",
    "BSM_IV_NO_BRACKET",
    "BSM_IV_OK",
    "BSM_IV_OUT_OF_BOUNDS",
    "BSM_IV_OUTPUT_CONTRACT_ID",
    "BSM_IV_SCHEMA_VERSION",
    "BSM_IV_STATUSES",
    "BSM_IV_VARIANT_ID",
    "BSMImpliedVolatilityContract",
    "BSMImpliedVolatilityInput",
    "CanonicalBSMImpliedVolatilityResult",
    "DEFAULT_BSM_IV_CONTRACT",
    "LOWER_VOLATILITY",
    "NormalizedBSMImpliedVolatilityInput",
    "UPPER_VOLATILITY",
    "bsm_iv_contract",
    "bsm_iv_contract_json",
    "canonical_observed_price",
    "canonicalize_bsm_iv_result",
    "normalize_bsm_iv_input",
]
