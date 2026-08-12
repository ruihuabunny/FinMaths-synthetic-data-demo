"""Shared mathematical, unit, and canonical-output contract for BSM Greeks.

This module deliberately contains no pricing or Greek formula.  It is the only
code shared by the stdlib solver and the independent QuantLib verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
import math
import re
from typing import Any, Mapping


BSM_ANALYTIC_GREEKS_VARIANT_ID = "bsm_analytic_greeks_v1"
BSM_ANALYTIC_GREEKS_METHOD_ID = "bsm-analytic-float64-greeks-v1"
BSM_GREEKS_CONVENTION_ID = "bsm-spot-greeks-actual365-flat-continuous-v1"
BSM_GREEKS_OUTPUT_CONTRACT_ID = "bsm-analytic-greeks-output-v1"
BSM_GREEKS_SCHEMA_VERSION = "bsm-greeks-output-v1.0.0"
RISK_NEUTRAL_MEASURE_ID = "USD-MONEY-MARKET-Q-v1"
NUMERAIRE_ID = "USD-MONEY-MARKET-ACCOUNT-v1"
OUTPUT_QUANTUM = Decimal("0.00000001")


_CANONICAL_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{8}$")
_OUTPUT_FIELDS = (
    "task_id",
    "valuation_date",
    "underlying_id",
    "option_id",
    "call_put",
    "expiry",
    "strike",
    "price",
    "delta",
    "gamma",
    "vega",
    "theta",
    "rho",
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


@dataclass(frozen=True)
class BSMGreeksInput:
    """One direct-volatility European BSM task row.

    The spot is the known ex-dividend state at ``valuation_date``.  ``sigma``
    is an annualized Q-measure instantaneous return standard deviation under
    Actual/365 Fixed, not a P-measure parameter and not an IV solver output.
    """

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
    sigma: float
    currency: str = "USD"
    exercise_style: str = "european"
    settlement_type: str = "cash"

    def __post_init__(self) -> None:
        for field in ("task_id", "underlying_id", "option_id", "currency"):
            object.__setattr__(self, field, _nonempty_string(getattr(self, field), field))
        if type(self.valuation_date) is not date or type(self.expiry) is not date:
            raise ValueError("valuation_date and expiry must be datetime.date values")
        if self.call_put not in {"call", "put"}:
            raise ValueError("call_put must be 'call' or 'put'")
        if self.exercise_style != "european":
            raise ValueError("analytic BSM Greeks require European exercise")
        if self.settlement_type != "cash":
            raise ValueError("analytic BSM Greeks require cash settlement")
        if self.currency != "USD":
            raise ValueError("the declared pricing measure and numeraire require USD")
        for field in (
            "spot",
            "strike",
            "time_to_expiry_actual365",
            "risk_free_rate",
            "dividend_yield",
            "sigma",
        ):
            object.__setattr__(self, field, _binary64(getattr(self, field), field))
        if self.spot <= 0.0:
            raise ValueError("spot must be positive")
        if self.strike <= 0.0:
            raise ValueError("strike must be positive")
        if self.sigma <= 0.0:
            raise ValueError("sigma must be positive")
        if self.time_to_expiry_actual365 <= 0.0:
            raise ValueError("time_to_expiry_actual365 must be positive")
        elapsed_days = (self.expiry - self.valuation_date).days
        if elapsed_days <= 0:
            raise ValueError("expiry must be after valuation_date")
        declared_tau = elapsed_days / 365.0
        if self.time_to_expiry_actual365 != declared_tau:
            raise ValueError(
                "time_to_expiry_actual365 must equal calendar days / 365"
            )

    @property
    def canonical_order_key(self) -> tuple[Any, ...]:
        return (
            self.valuation_date,
            self.underlying_id,
            self.expiry,
            self.strike,
            self.call_put,
            self.option_id,
        )


@dataclass(frozen=True)
class BSMGreeksValues:
    """Raw binary64 values before the shared publication checkpoint."""

    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float

    def __post_init__(self) -> None:
        for field in ("price", "delta", "gamma", "vega", "theta", "rho"):
            if not isinstance(getattr(self, field), float) or not math.isfinite(
                getattr(self, field)
            ):
                raise ValueError(f"{field} must be a finite binary64 value")


def canonical_decimal(value: float) -> str:
    """Serialize one finite binary64 with 8-place half-even and no negative zero."""

    if not isinstance(value, float) or not math.isfinite(value):
        raise ValueError("canonical output must be a finite binary64 value")
    try:
        quantized = Decimal(str(value)).quantize(
            OUTPUT_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    except InvalidOperation as error:
        raise ValueError("canonical output exceeds decimal contract") from error
    if quantized == 0:
        quantized = Decimal("0").quantize(OUTPUT_QUANTUM)
    return format(quantized, "f")


@dataclass(frozen=True)
class CanonicalBSMGreeksResult:
    """One exact-comparison row; all numerical fields are decimal strings."""

    task_id: str
    valuation_date: str
    underlying_id: str
    option_id: str
    call_put: str
    expiry: str
    strike: str
    price: str
    delta: str
    gamma: str
    vega: str
    theta: str
    rho: str
    method_id: str = BSM_ANALYTIC_GREEKS_METHOD_ID
    convention_id: str = BSM_GREEKS_CONVENTION_ID
    output_contract_id: str = BSM_GREEKS_OUTPUT_CONTRACT_ID

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
        for field in ("strike", "price", "delta", "gamma", "vega", "theta", "rho"):
            value = getattr(self, field)
            if not isinstance(value, str) or not _CANONICAL_DECIMAL_PATTERN.fullmatch(
                value
            ):
                raise ValueError(f"{field} is not a canonical 8-place decimal")
            if value == "-0.00000000":
                raise ValueError(f"{field} must not contain negative zero")
        if Decimal(self.strike) <= 0:
            raise ValueError("strike must be positive")
        for field in ("price", "gamma", "vega"):
            if Decimal(getattr(self, field)) < 0:
                raise ValueError(f"{field} must be non-negative")
        if self.method_id != BSM_ANALYTIC_GREEKS_METHOD_ID:
            raise ValueError("method_id does not match the analytic BSM contract")
        if self.convention_id != BSM_GREEKS_CONVENTION_ID:
            raise ValueError("convention_id does not match the spot Greek contract")
        if self.output_contract_id != BSM_GREEKS_OUTPUT_CONTRACT_ID:
            raise ValueError("output_contract_id does not match the schema contract")

    def to_dict(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in _OUTPUT_FIELDS}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CanonicalBSMGreeksResult:
        if set(value) != set(_OUTPUT_FIELDS):
            raise ValueError("canonical BSM Greek row has missing or extra fields")
        return cls(**{field: value[field] for field in _OUTPUT_FIELDS})


def canonicalize_bsm_greeks(
    task_input: BSMGreeksInput, values: BSMGreeksValues
) -> CanonicalBSMGreeksResult:
    """Apply the sole float-to-public-decimal checkpoint."""

    return CanonicalBSMGreeksResult(
        task_id=task_input.task_id,
        valuation_date=task_input.valuation_date.isoformat(),
        underlying_id=task_input.underlying_id,
        option_id=task_input.option_id,
        call_put=task_input.call_put,
        expiry=task_input.expiry.isoformat(),
        strike=canonical_decimal(task_input.strike),
        price=canonical_decimal(values.price),
        delta=canonical_decimal(values.delta),
        gamma=canonical_decimal(values.gamma),
        vega=canonical_decimal(values.vega),
        theta=canonical_decimal(values.theta),
        rho=canonical_decimal(values.rho),
    )


def analytic_bsm_greeks_contract() -> dict[str, Any]:
    """Return the immutable method/convention contract as JSON-ready data."""

    return {
        "variant_id": BSM_ANALYTIC_GREEKS_VARIANT_ID,
        "method_id": BSM_ANALYTIC_GREEKS_METHOD_ID,
        "convention_id": BSM_GREEKS_CONVENTION_ID,
        "output_contract_id": BSM_GREEKS_OUTPUT_CONTRACT_ID,
        "pricing_measure_id": RISK_NEUTRAL_MEASURE_ID,
        "numeraire_id": NUMERAIRE_ID,
        "probability_measure": "Q associated with the USD money-market numeraire",
        "economic_object": "ex_dividend_spot",
        "state_variables": ["ex_dividend_spot"],
        "filtration": "market_filtration_at_the_declared_valuation_timestamp",
        "conditioning_information": (
            "valuation_timestamp_spot_contract_flat_curves_and_Q_sigma"
        ),
        "valuation_timestamp": "valuation_date_00:00:00_UTC",
        "model": "European Black-Scholes-Merton",
        "risk_neutral_dynamics": "dS_t/S_t=(r-q)dt+sigma*dW_t^Q",
        "payoff": "unit_cash_max(sign*(S_T-K),0)",
        "exercise_style": "european",
        "settlement_type": "cash",
        "rate_curve": "flat_continuously_compounded",
        "dividend_curve": "flat_continuously_compounded",
        "time_axis": "calendar_time",
        "day_count": "Actual365Fixed",
        "transition_or_pricing_law": "exact_analytic_constant_parameter_bsm",
        "sigma_semantics": "annualized_Q_instantaneous_return_standard_deviation",
        "input_units": {
            "spot": "USD_per_underlying_unit",
            "strike": "USD_per_underlying_unit",
            "tau": "Actual365Fixed_years",
            "r": "annualized_continuously_compounded_inverse_year",
            "q": "annualized_continuous_yield_inverse_year",
            "sigma": "annualized_inverse_square_root_year",
        },
        "input_dtype": "binary64_cast_once_at_contract_boundary",
        "operation_order_id": "bsm-analytic-float64-operation-order-v1",
        "solver_normal_cdf": "0.5 * erfc(-x * (1 / sqrt(2)))",
        "solver_normal_pdf": "exp(-0.5 * x * x) * (1 / sqrt(2 * pi))",
        "operation_sequence": [
            "sqrt_tau",
            "variance",
            "root_variance",
            "carry",
            "log_moneyness",
            "standardized_drift",
            "standardized_normal_arguments",
            "discount_factors",
            "discounted_spot_and_strike",
            "normal_density",
            "unscaled_price_and_greeks",
            "vega_rho_0.01_scaling",
            "theta_365_scaling",
        ],
        "operation_formulas": {
            "constants": [
                "inverse_sqrt_two = 1.0 / sqrt(2.0)",
                "inverse_sqrt_two_pi = 1.0 / sqrt(2.0 * pi)",
            ],
            "common": [
                "sqrt_tau = sqrt(tau)",
                "variance = sigma * sigma",
                "root_variance = sigma * sqrt_tau",
                "carry = r - q",
                "log_moneyness = log(spot / strike)",
                "standardized_drift = (carry + 0.5 * variance) * tau",
                "d1 = (log_moneyness + standardized_drift) / root_variance",
                "d2 = d1 - root_variance",
                "spot_discount = exp(-q * tau)",
                "strike_discount = exp(-r * tau)",
                "discounted_spot = spot * spot_discount",
                "discounted_strike = strike * strike_discount",
                "density = exp(-0.5 * d1 * d1) * inverse_sqrt_two_pi",
                "gamma = spot_discount * density / (spot * root_variance)",
                "vega_per_unit = discounted_spot * density * sqrt_tau",
                "diffusion_theta = -discounted_spot * density * sigma / (2.0 * sqrt_tau)",
            ],
            "call": [
                "cdf_d1 = 0.5 * erfc(-d1 * inverse_sqrt_two)",
                "cdf_d2 = 0.5 * erfc(-d2 * inverse_sqrt_two)",
                "price = discounted_spot * cdf_d1 - discounted_strike * cdf_d2",
                "delta = spot_discount * cdf_d1",
                "theta_per_year = diffusion_theta - r * discounted_strike * cdf_d2 + q * discounted_spot * cdf_d1",
                "rho_per_unit = strike * tau * strike_discount * cdf_d2",
            ],
            "put": [
                "cdf_minus_d1 = 0.5 * erfc(d1 * inverse_sqrt_two)",
                "cdf_minus_d2 = 0.5 * erfc(d2 * inverse_sqrt_two)",
                "price = discounted_strike * cdf_minus_d2 - discounted_spot * cdf_minus_d1",
                "delta = -spot_discount * cdf_minus_d1",
                "theta_per_year = diffusion_theta + r * discounted_strike * cdf_minus_d2 - q * discounted_spot * cdf_minus_d1",
                "rho_per_unit = -strike * tau * strike_discount * cdf_minus_d2",
            ],
            "scaling": [
                "unit_vega_1volpt = 0.01 * vega_per_unit",
                "unit_theta_1calendar_day = theta_per_year / 365.0",
                "unit_rho_1pct = 0.01 * rho_per_unit",
            ],
        },
        "greeks": {
            "delta": {
                "definition": "spot_first_derivative",
                "scale": "per_1_spot_unit",
                "holding_fixed": ["strike", "tau", "r", "q", "sigma"],
            },
            "gamma": {
                "definition": "spot_second_derivative",
                "scale": "per_1_spot_unit_squared",
                "holding_fixed": ["strike", "tau", "r", "q", "sigma"],
            },
            "vega": {
                "definition": "volatility_first_derivative",
                "scale": "per_0.01_absolute_volatility",
                "holding_fixed": ["spot", "strike", "tau", "r", "q"],
            },
            "theta": {
                "definition": "valuation_time_first_derivative_expiry_fixed",
                "scale": "per_1_calendar_day",
                "holding_fixed": ["spot", "strike", "r", "q", "sigma", "expiry"],
            },
            "rho": {
                "definition": "continuous_risk_free_rate_first_derivative",
                "scale": "per_0.01_absolute_rate",
                "holding_fixed": ["spot", "strike", "tau", "q", "sigma"],
            },
        },
        "contract_multiplier_policy": "unit_option_not_applied",
        "output_precision": 8,
        "rounding": "ROUND_HALF_EVEN",
        "float_to_decimal": "Decimal(str(binary64))",
        "negative_zero": "canonical_positive_zero",
        "row_order": [
            "valuation_date",
            "underlying_id",
            "expiry",
            "strike",
            "call_put",
            "option_id",
        ],
        "invalid_input_behavior": "reject_before_evaluation",
        "persisted_intermediates": [],
    }


def analytic_bsm_greeks_contract_json() -> str:
    return json.dumps(
        analytic_bsm_greeks_contract(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


__all__ = [
    "BSM_ANALYTIC_GREEKS_METHOD_ID",
    "BSM_ANALYTIC_GREEKS_VARIANT_ID",
    "BSM_GREEKS_CONVENTION_ID",
    "BSM_GREEKS_OUTPUT_CONTRACT_ID",
    "BSM_GREEKS_SCHEMA_VERSION",
    "BSMGreeksInput",
    "BSMGreeksValues",
    "CanonicalBSMGreeksResult",
    "analytic_bsm_greeks_contract",
    "analytic_bsm_greeks_contract_json",
    "canonical_decimal",
    "canonicalize_bsm_greeks",
]
