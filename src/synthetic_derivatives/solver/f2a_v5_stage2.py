"""Independent Solver BS inversion and linked-diffusion validation for F2A v5.1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from hashlib import sha256
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from synthetic_derivatives.solver.f2a_v5_stage1 import (
    PhysicalFittingContract,
    UnderlyingFit,
    integrate_hat_basis,
)


BSM_FORMULA_ID = "bsm-integrated-variance-european-v1"
BSM_INVERSION_METHOD_ID = "bsm-bisection-float64-80-v1"
LINKED_DIFFUSION_VALIDATION_ID = "bsm-linked-stage1-diffusion-v1"
PRICE_UNCERTAINTY_ID = "delta-method-full-diffusion-covariance-v1"
LOCALISATION_ID = "standardized-residual-contract-date-grouping-v1"
MARKET_IV_CONVERGED = "converged"
MARKET_IV_INVALID_BRACKET = "invalid_bracket"


def _quantize(value: float, precision: int) -> float:
    if not math.isfinite(value):
        raise ValueError("canonical Stage-2 output must be finite")
    return float(
        Decimal(str(value)).quantize(
            Decimal(1).scaleb(-precision),
            rounding=ROUND_HALF_EVEN,
        )
    )


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def stable_row_id(underlying_id: str, valuation_date: str, option_id: str) -> str:
    """Composite identity used when an option id repeats across valuation dates."""

    return f"{underlying_id}|{valuation_date}|{option_id}"


@dataclass(frozen=True)
class BSMInversionContract:
    measure: str = "USD-MONEY-MARKET-Q-v1"
    numeraire_id: str = "USD-MONEY-MARKET-ACCOUNT-v1"
    currency: str = "USD"
    valuation_time_utc: str = "16:00:00"
    pricing_family: str = "BSM"
    formula_id: str = BSM_FORMULA_ID
    method_id: str = BSM_INVERSION_METHOD_ID
    input_price_rule: str = "bid_ask_midpoint"
    lower_volatility: float = 1e-6
    upper_volatility: float = 5.0
    iterations: int = 80
    input_dtype: str = "binary64"
    midpoint_rule: str = "mid_equals_low_plus_high_over_two"
    update_rule: str = "price_mid_lt_observed_updates_low_else_high"
    early_stop: bool = False
    fallback_method: str | None = None
    final_root_rule: str = "low_80_plus_high_80_over_two"
    invalid_bracket_behavior: str = MARKET_IV_INVALID_BRACKET
    discounted_price_bounds_rule: str = "european_continuous_carry_bsm_v1"
    canonicalization_rule: str = "all_internal_binary64_then_half_even"
    output_precision: int = 10

    def __post_init__(self) -> None:
        if (
            self.measure != "USD-MONEY-MARKET-Q-v1"
            or self.numeraire_id != "USD-MONEY-MARKET-ACCOUNT-v1"
            or self.currency != "USD"
            or self.valuation_time_utc != "16:00:00"
        ):
            raise ValueError("unknown common-Q/numeraire BSM inversion contract")
        frozen = (
            (self.pricing_family, "BSM"),
            (self.formula_id, BSM_FORMULA_ID),
            (self.method_id, BSM_INVERSION_METHOD_ID),
            (self.input_price_rule, "bid_ask_midpoint"),
            (self.input_dtype, "binary64"),
            (self.midpoint_rule, "mid_equals_low_plus_high_over_two"),
            (self.update_rule, "price_mid_lt_observed_updates_low_else_high"),
            (self.final_root_rule, "low_80_plus_high_80_over_two"),
            (self.invalid_bracket_behavior, MARKET_IV_INVALID_BRACKET),
            (
                self.discounted_price_bounds_rule,
                "european_continuous_carry_bsm_v1",
            ),
            (
                self.canonicalization_rule,
                "all_internal_binary64_then_half_even",
            ),
        )
        if any(actual != expected for actual, expected in frozen):
            raise ValueError("unknown frozen BSM inversion method contract")
        if (
            not math.isfinite(self.lower_volatility)
            or not math.isfinite(self.upper_volatility)
            or not 0.0 < self.lower_volatility < self.upper_volatility
        ):
            raise ValueError("BSM inversion volatility bracket is invalid")
        if self.iterations != 80:
            raise ValueError("the canonical BSM inversion requires exactly 80 iterations")
        if self.early_stop or self.fallback_method is not None:
            raise ValueError("the canonical BSM inversion forbids early stop and fallback")
        if self.output_precision < 0:
            raise ValueError("BSM inversion output precision must be nonnegative")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BSMInversionContract":
        bracket = raw.get("volatility_bracket", (1e-6, 5.0))
        if not isinstance(bracket, (list, tuple)) or len(bracket) != 2:
            raise ValueError("BSM inversion volatility_bracket must have two endpoints")
        return cls(
            measure=str(raw.get("measure", "USD-MONEY-MARKET-Q-v1")),
            numeraire_id=str(
                raw.get("numeraire_id", "USD-MONEY-MARKET-ACCOUNT-v1")
            ),
            currency=str(raw.get("currency", "USD")),
            valuation_time_utc=str(raw.get("valuation_time_utc", "16:00:00")),
            pricing_family=str(raw.get("pricing_family", "BSM")),
            formula_id=str(raw.get("formula_id", BSM_FORMULA_ID)),
            method_id=str(raw.get("method_id", BSM_INVERSION_METHOD_ID)),
            input_price_rule=str(raw.get("input_price_rule", "bid_ask_midpoint")),
            lower_volatility=float(bracket[0]),
            upper_volatility=float(bracket[1]),
            iterations=int(raw.get("iterations", 80)),
            input_dtype=str(raw.get("input_dtype", "binary64")),
            midpoint_rule=str(
                raw.get("midpoint_rule", "mid_equals_low_plus_high_over_two")
            ),
            update_rule=str(
                raw.get(
                    "update_rule",
                    "price_mid_lt_observed_updates_low_else_high",
                )
            ),
            early_stop=bool(raw.get("early_stop", False)),
            fallback_method=raw.get("fallback_method"),
            final_root_rule=str(
                raw.get("final_root_rule", "low_80_plus_high_80_over_two")
            ),
            invalid_bracket_behavior=str(
                raw.get("invalid_bracket_behavior", MARKET_IV_INVALID_BRACKET)
            ),
            discounted_price_bounds_rule=str(
                raw.get(
                    "discounted_price_bounds_rule",
                    "european_continuous_carry_bsm_v1",
                )
            ),
            canonicalization_rule=str(
                raw.get(
                    "canonicalization_rule",
                    "all_internal_binary64_then_half_even",
                )
            ),
            output_precision=int(raw.get("output_precision", 10)),
        )


@dataclass(frozen=True)
class LinkedDiffusionValidationContract:
    measure: str = "USD-MONEY-MARKET-Q-v1"
    numeraire_id: str = "USD-MONEY-MARKET-ACCOUNT-v1"
    currency: str = "USD"
    valuation_time_utc: str = "16:00:00"
    measure_change: str = "girsanov_drift_only"
    volatility_measure_mapping: str = "same_deterministic_diffusion_coefficient"
    pricing_family: str = "BSM"
    formula_id: str = BSM_FORMULA_ID
    validation_contract_id: str = LINKED_DIFFUSION_VALIDATION_ID
    quote_noise_scale: float = 0.05
    residual_threshold: float = 4.0
    minimum_series_observations: int = 5
    row_weight: float = 1.0
    validation_loss: str = "squared"
    quote_observation_rule: str = "bid_ask_midpoint"
    rate_curve_integration_rule: str = "flat_continuous_scalar_times_actual365"
    dividend_or_carry_integration_rule: str = "flat_continuous_scalar_times_actual365"
    integrated_variance_rule: str = "exact_squared_piecewise_linear_actual365_v1"
    interpolation: str = "linear"
    extrapolation: str = "flat"
    uncertainty_method: str = PRICE_UNCERTAINTY_ID
    localisation_id: str = LOCALISATION_ID
    residual_standardization_rule: str = "quote_noise_plus_parameter_variance"
    clean_max_outlier_fraction: float = 0.01
    output_precision: int = 10

    def __post_init__(self) -> None:
        if (
            self.measure != "USD-MONEY-MARKET-Q-v1"
            or self.numeraire_id != "USD-MONEY-MARKET-ACCOUNT-v1"
            or self.currency != "USD"
            or self.valuation_time_utc != "16:00:00"
            or self.measure_change != "girsanov_drift_only"
            or self.volatility_measure_mapping
            != "same_deterministic_diffusion_coefficient"
        ):
            raise ValueError("unknown common-Q linked-diffusion validation contract")
        if (
            self.pricing_family != "BSM"
            or self.formula_id != BSM_FORMULA_ID
            or self.validation_contract_id != LINKED_DIFFUSION_VALIDATION_ID
        ):
            raise ValueError("unknown linked-diffusion BSM validation identity")
        if self.quote_noise_scale <= 0.0 or not math.isfinite(self.quote_noise_scale):
            raise ValueError("quote-noise scale must be finite and positive")
        if self.residual_threshold <= 0.0 or not math.isfinite(self.residual_threshold):
            raise ValueError("residual threshold must be finite and positive")
        if self.minimum_series_observations < 2:
            raise ValueError("eligible option series need at least two observations")
        if self.row_weight <= 0.0 or not math.isfinite(self.row_weight):
            raise ValueError("Stage-2 row weight must be finite and positive")
        frozen = (
            (self.validation_loss, "squared"),
            (self.quote_observation_rule, "bid_ask_midpoint"),
            (
                self.rate_curve_integration_rule,
                "flat_continuous_scalar_times_actual365",
            ),
            (
                self.dividend_or_carry_integration_rule,
                "flat_continuous_scalar_times_actual365",
            ),
            (
                self.integrated_variance_rule,
                "exact_squared_piecewise_linear_actual365_v1",
            ),
            (self.interpolation, "linear"),
            (self.extrapolation, "flat"),
            (self.uncertainty_method, PRICE_UNCERTAINTY_ID),
            (self.localisation_id, LOCALISATION_ID),
            (
                self.residual_standardization_rule,
                "quote_noise_plus_parameter_variance",
            ),
        )
        if any(actual != expected for actual, expected in frozen):
            raise ValueError("unknown linked-diffusion validation method")
        if not 0.0 <= self.clean_max_outlier_fraction <= 1.0:
            raise ValueError("clean residual outlier fraction must lie in [0, 1]")
        if self.output_precision < 0:
            raise ValueError("Stage-2 output precision must be nonnegative")

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any]
    ) -> "LinkedDiffusionValidationContract":
        return cls(
            measure=str(raw.get("measure", "USD-MONEY-MARKET-Q-v1")),
            numeraire_id=str(
                raw.get("numeraire_id", "USD-MONEY-MARKET-ACCOUNT-v1")
            ),
            currency=str(raw.get("currency", "USD")),
            valuation_time_utc=str(raw.get("valuation_time_utc", "16:00:00")),
            measure_change=str(raw.get("measure_change", "girsanov_drift_only")),
            volatility_measure_mapping=str(
                raw.get(
                    "volatility_measure_mapping",
                    "same_deterministic_diffusion_coefficient",
                )
            ),
            pricing_family=str(raw.get("pricing_family", "BSM")),
            formula_id=str(raw.get("formula_id", BSM_FORMULA_ID)),
            validation_contract_id=str(
                raw.get("validation_contract_id", LINKED_DIFFUSION_VALIDATION_ID)
            ),
            quote_noise_scale=float(
                raw.get("quote_noise_scale", raw.get("quote_noise_normalization", 0.05))
            ),
            residual_threshold=float(raw.get("residual_threshold", 4.0)),
            minimum_series_observations=int(raw.get("minimum_series_observations", 5)),
            row_weight=float(raw.get("row_weight", 1.0)),
            validation_loss=str(raw.get("validation_statistic", "squared")),
            quote_observation_rule=str(
                raw.get("quote_observation_rule", "bid_ask_midpoint")
            ),
            rate_curve_integration_rule=str(
                raw.get(
                    "rate_curve_integration_rule",
                    "flat_continuous_scalar_times_actual365",
                )
            ),
            dividend_or_carry_integration_rule=str(
                raw.get(
                    "dividend_or_carry_integration_rule",
                    "flat_continuous_scalar_times_actual365",
                )
            ),
            integrated_variance_rule=str(
                raw.get(
                    "integrated_variance_rule",
                    "exact_squared_piecewise_linear_actual365_v1",
                )
            ),
            interpolation=str(raw.get("interpolation", "linear")),
            extrapolation=str(raw.get("extrapolation", "flat")),
            uncertainty_method=str(
                raw.get("price_uncertainty_method", PRICE_UNCERTAINTY_ID)
            ),
            localisation_id=str(raw.get("grouping_rule", LOCALISATION_ID)),
            residual_standardization_rule=str(
                raw.get(
                    "residual_standardization_rule",
                    "quote_noise_plus_parameter_variance",
                )
            ),
            clean_max_outlier_fraction=float(
                raw.get("clean_max_outlier_fraction", 0.01)
            ),
            output_precision=int(raw.get("output_precision", 10)),
        )


@dataclass(frozen=True)
class OptionObservation:
    row_id: str
    option_contract_id: str
    option_id: str
    underlying_id: str
    valuation_date: str
    expiry: str
    option_type: str
    strike: float
    spot: float
    bid: float
    ask: float
    contract_multiplier: float
    integrated_rate: float
    integrated_dividend_or_carry: float
    settlement_style: str = "cash"
    exercise_style: str = "european"

    def __post_init__(self) -> None:
        valuation = date.fromisoformat(self.valuation_date)
        expiry = date.fromisoformat(self.expiry)
        if expiry <= valuation:
            raise ValueError("Stage-2 option must be live at its valuation date")
        if self.option_type not in {"call", "put"}:
            raise ValueError("option_type must be call or put")
        values = (
            self.strike,
            self.spot,
            self.bid,
            self.ask,
            self.contract_multiplier,
            self.integrated_rate,
            self.integrated_dividend_or_carry,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("Stage-2 option inputs must be finite")
        if self.strike <= 0.0 or self.spot <= 0.0 or self.contract_multiplier <= 0.0:
            raise ValueError("spot, strike and multiplier must be positive")
        if self.bid < 0.0 or self.ask < self.bid:
            raise ValueError("observable quotes require 0 <= bid <= ask")
        if self.exercise_style != "european" or self.settlement_style != "cash":
            raise ValueError("the v5 BSM contract supports European cash-settled options")

    @property
    def observed_price(self) -> float:
        midpoint = (Decimal(str(self.bid)) + Decimal(str(self.ask))) / Decimal(2)
        return float(midpoint)

    @property
    def remaining_years(self) -> float:
        valuation = date.fromisoformat(self.valuation_date)
        expiry = date.fromisoformat(self.expiry)
        return (expiry - valuation).days / 365.0


@dataclass(frozen=True)
class BSMInversionResult:
    status: str
    implied_volatility: float | None
    d1: float | None
    d2: float | None
    iterations: int
    method_id: str = BSM_INVERSION_METHOD_ID


@dataclass(frozen=True)
class Stage2RowResult:
    observation: OptionObservation
    market_iv_status: str
    market_implied_volatility: float | None
    market_d1: float | None
    market_d2: float | None
    linked_integrated_variance: float
    linked_effective_volatility: float
    linked_d1: float
    linked_d2: float
    linked_counterfactual_price: float
    linked_counterfactual_price_se: float
    price_gradient: tuple[float, ...]
    price_residual: float
    standardized_residual: float

    def to_dict(self, precision: int = 10) -> dict[str, Any]:
        q = lambda value: _quantize(value, precision)
        return {
            "row_id": self.observation.row_id,
            "observed_price": q(self.observation.observed_price),
            "market_iv_status": self.market_iv_status,
            "market_implied_volatility": (
                None
                if self.market_implied_volatility is None
                else q(self.market_implied_volatility)
            ),
            "market_d1": None if self.market_d1 is None else q(self.market_d1),
            "market_d2": None if self.market_d2 is None else q(self.market_d2),
            "linked_integrated_variance": q(self.linked_integrated_variance),
            "linked_effective_volatility": q(self.linked_effective_volatility),
            "linked_d1": q(self.linked_d1),
            "linked_d2": q(self.linked_d2),
            "linked_counterfactual_price": q(self.linked_counterfactual_price),
            "linked_counterfactual_price_se": q(
                self.linked_counterfactual_price_se
            ),
            "price_gradient": [q(value) for value in self.price_gradient],
            "price_residual": q(self.price_residual),
            "standardized_residual": q(self.standardized_residual),
        }


@dataclass(frozen=True)
class OptionSeriesResult:
    option_contract_id: str
    underlying_id: str
    option_type: str
    strike: float
    expiry: str
    linked_diffusion_underlying_id: str
    rows: tuple[Stage2RowResult, ...]
    validation_residual_sse: float
    series_status: str
    bsm_inversion_method_id: str = BSM_INVERSION_METHOD_ID
    bsm_inversion_iteration_count: int = 80
    linked_diffusion_validation_id: str = LINKED_DIFFUSION_VALIDATION_ID
    output_precision: int = 10

    def to_dict(self) -> dict[str, Any]:
        q = lambda value: _quantize(value, self.output_precision)
        ordered = tuple(
            sorted(
                self.rows,
                key=lambda item: (
                    item.observation.valuation_date,
                    item.observation.row_id,
                ),
            )
        )
        market_rows = tuple(
            item for item in ordered if item.market_implied_volatility is not None
        )
        return {
            "option_contract_id": self.option_contract_id,
            "underlying_id": self.underlying_id,
            "valuation_row_ids": [item.observation.row_id for item in ordered],
            "option_type": self.option_type,
            "strike": q(self.strike),
            "expiry": self.expiry,
            "pricing_family": "BSM",
            "bsm_inversion_method_id": self.bsm_inversion_method_id,
            "bsm_inversion_iteration_count": self.bsm_inversion_iteration_count,
            "linked_diffusion_validation_id": self.linked_diffusion_validation_id,
            "linked_diffusion_underlying_id": self.linked_diffusion_underlying_id,
            "observed_prices_by_row_id": {
                item.observation.row_id: q(item.observation.observed_price)
                for item in ordered
            },
            "market_implied_volatility_by_row_id": {
                item.observation.row_id: q(item.market_implied_volatility)
                for item in market_rows
                if item.market_implied_volatility is not None
            },
            "market_iv_status_by_row_id": {
                item.observation.row_id: item.market_iv_status for item in ordered
            },
            "market_d1_by_row_id": {
                item.observation.row_id: q(item.market_d1)
                for item in market_rows
                if item.market_d1 is not None
            },
            "market_d2_by_row_id": {
                item.observation.row_id: q(item.market_d2)
                for item in market_rows
                if item.market_d2 is not None
            },
            "linked_integrated_variance_by_row_id": {
                item.observation.row_id: q(item.linked_integrated_variance)
                for item in ordered
            },
            "linked_effective_volatility_by_row_id": {
                item.observation.row_id: q(item.linked_effective_volatility)
                for item in ordered
            },
            "linked_d1_by_row_id": {
                item.observation.row_id: q(item.linked_d1) for item in ordered
            },
            "linked_d2_by_row_id": {
                item.observation.row_id: q(item.linked_d2) for item in ordered
            },
            "linked_counterfactual_prices_by_row_id": {
                item.observation.row_id: q(item.linked_counterfactual_price)
                for item in ordered
            },
            "linked_counterfactual_price_se_by_row_id": {
                item.observation.row_id: q(item.linked_counterfactual_price_se)
                for item in ordered
            },
            "price_residuals_by_row_id": {
                item.observation.row_id: q(item.price_residual) for item in ordered
            },
            "standardized_residuals_by_row_id": {
                item.observation.row_id: q(item.standardized_residual)
                for item in ordered
            },
            "validation_residual_sse": q(self.validation_residual_sse),
            "series_status": self.series_status,
        }


@dataclass(frozen=True)
class MutationDiagnosis:
    mutation_group_id: str
    affected_rows: tuple[Stage2RowResult, ...]
    localisation_score: float
    proposed_mutation_family: str
    inferred_direction: str
    estimated_mutation_magnitude: float
    output_precision: int = 10

    def to_dict(self) -> dict[str, Any]:
        q = lambda value: _quantize(value, self.output_precision)
        rows = tuple(sorted(self.affected_rows, key=lambda item: item.observation.row_id))
        return {
            "mutation_group_id": self.mutation_group_id,
            "affected_row_ids": [item.observation.row_id for item in rows],
            "affected_dates": sorted(
                {item.observation.valuation_date for item in rows}
            ),
            "observed_quotes": {
                item.observation.row_id: q(item.observation.observed_price)
                for item in rows
            },
            "linked_clean_counterfactual_quotes": {
                item.observation.row_id: q(item.linked_counterfactual_price)
                for item in rows
            },
            "residuals": {
                item.observation.row_id: q(item.price_residual) for item in rows
            },
            "localisation_score": q(self.localisation_score),
            "proposed_mutation_family": self.proposed_mutation_family,
            "inferred_direction": self.inferred_direction,
            "estimated_mutation_magnitude": q(self.estimated_mutation_magnitude),
        }


def bsm_price_from_integrated_variance(
    *,
    option_type: str,
    spot: float,
    strike: float,
    integrated_rate: float,
    integrated_dividend: float,
    integrated_variance: float,
) -> tuple[float, float, float]:
    """Return the analytic European BSM price and its derived d1/d2 values."""

    if spot <= 0.0 or strike <= 0.0 or integrated_variance <= 0.0:
        raise ValueError("BSM requires positive spot, strike and integrated variance")
    if not all(
        math.isfinite(value)
        for value in (
            spot,
            strike,
            integrated_rate,
            integrated_dividend,
            integrated_variance,
        )
    ):
        raise ValueError("BSM inputs must be finite")
    root_variance = math.sqrt(integrated_variance)
    d1 = (
        math.log(spot / strike)
        + integrated_rate
        - integrated_dividend
        + 0.5 * integrated_variance
    ) / root_variance
    d2 = d1 - root_variance
    discounted_spot = spot * math.exp(-integrated_dividend)
    discounted_strike = strike * math.exp(-integrated_rate)
    if option_type == "call":
        price = discounted_spot * normal_cdf(d1) - discounted_strike * normal_cdf(d2)
    elif option_type == "put":
        price = discounted_strike * normal_cdf(-d2) - discounted_spot * normal_cdf(-d1)
    else:
        raise ValueError("option_type must be call or put")
    return price, d1, d2


def bsm_price(
    *,
    option_type: str,
    spot: float,
    strike: float,
    remaining_years: float,
    integrated_rate: float,
    integrated_dividend: float,
    volatility: float,
) -> float:
    """Analytic BSM price used by the Solver-side fixed bisection wrapper."""

    if remaining_years <= 0.0 or volatility <= 0.0:
        raise ValueError("BSM maturity and volatility must be positive")
    price, _, _ = bsm_price_from_integrated_variance(
        option_type=option_type,
        spot=spot,
        strike=strike,
        integrated_rate=integrated_rate,
        integrated_dividend=integrated_dividend,
        integrated_variance=volatility * volatility * remaining_years,
    )
    return price


def discounted_bsm_price_bounds(
    *,
    option_type: str,
    spot: float,
    strike: float,
    integrated_rate: float,
    integrated_dividend: float,
) -> tuple[float, float]:
    """Return the continuous-carry European BSM lower and upper price bounds."""

    discounted_spot = spot * math.exp(-integrated_dividend)
    discounted_strike = strike * math.exp(-integrated_rate)
    if option_type == "call":
        return max(0.0, discounted_spot - discounted_strike), discounted_spot
    if option_type == "put":
        return max(0.0, discounted_strike - discounted_spot), discounted_strike
    raise ValueError("option_type must be call or put")


def invert_bsm_implied_volatility(
    *,
    option_type: str,
    spot: float,
    strike: float,
    remaining_years: float,
    integrated_rate: float,
    integrated_dividend: float,
    observed_price: float,
    contract: BSMInversionContract | None = None,
) -> BSMInversionResult:
    """Apply the frozen 80-step binary64 bisection to one visible midpoint."""

    inversion = contract or BSMInversionContract()
    values = (
        spot,
        strike,
        remaining_years,
        integrated_rate,
        integrated_dividend,
        observed_price,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("BSM inversion inputs must be finite")
    if spot <= 0.0 or strike <= 0.0 or remaining_years <= 0.0:
        raise ValueError("BSM inversion requires positive spot, strike and maturity")
    lower_bound, upper_bound = discounted_bsm_price_bounds(
        option_type=option_type,
        spot=spot,
        strike=strike,
        integrated_rate=integrated_rate,
        integrated_dividend=integrated_dividend,
    )
    if observed_price < lower_bound or observed_price > upper_bound:
        return BSMInversionResult(
            status=MARKET_IV_INVALID_BRACKET,
            implied_volatility=None,
            d1=None,
            d2=None,
            iterations=0,
            method_id=inversion.method_id,
        )
    low = inversion.lower_volatility
    high = inversion.upper_volatility
    price_low = bsm_price(
        option_type=option_type,
        spot=spot,
        strike=strike,
        remaining_years=remaining_years,
        integrated_rate=integrated_rate,
        integrated_dividend=integrated_dividend,
        volatility=low,
    )
    price_high = bsm_price(
        option_type=option_type,
        spot=spot,
        strike=strike,
        remaining_years=remaining_years,
        integrated_rate=integrated_rate,
        integrated_dividend=integrated_dividend,
        volatility=high,
    )
    if observed_price < price_low or observed_price > price_high:
        return BSMInversionResult(
            status=MARKET_IV_INVALID_BRACKET,
            implied_volatility=None,
            d1=None,
            d2=None,
            iterations=0,
            method_id=inversion.method_id,
        )
    for _ in range(inversion.iterations):
        mid = (low + high) / 2.0
        price_mid = bsm_price(
            option_type=option_type,
            spot=spot,
            strike=strike,
            remaining_years=remaining_years,
            integrated_rate=integrated_rate,
            integrated_dividend=integrated_dividend,
            volatility=mid,
        )
        if price_mid < observed_price:
            low = mid
        else:
            high = mid
    root = (low + high) / 2.0
    _, d1, d2 = bsm_price_from_integrated_variance(
        option_type=option_type,
        spot=spot,
        strike=strike,
        integrated_rate=integrated_rate,
        integrated_dividend=integrated_dividend,
        integrated_variance=root * root * remaining_years,
    )
    if not all(math.isfinite(value) for value in (root, d1, d2)):
        raise ValueError("canonical BSM inversion produced a non-finite result")
    return BSMInversionResult(
        status=MARKET_IV_CONVERGED,
        implied_volatility=root,
        d1=d1,
        d2=d2,
        iterations=inversion.iterations,
        method_id=inversion.method_id,
    )


def _quadratic(vector: Sequence[float], matrix: Sequence[Sequence[float]]) -> float:
    return sum(
        vector[i] * matrix[i][j] * vector[j]
        for i in range(len(vector))
        for j in range(len(vector))
    )


def _matvec(
    matrix: Sequence[Sequence[float]], vector: Sequence[float]
) -> tuple[float, ...]:
    return tuple(sum(a * b for a, b in zip(row, vector)) for row in matrix)


def evaluate_stage2_row(
    observation: OptionObservation,
    physical_contract: PhysicalFittingContract,
    underlying_fit: UnderlyingFit,
    inversion_contract: BSMInversionContract,
    validation_contract: LinkedDiffusionValidationContract,
) -> Stage2RowResult:
    """Invert the visible midpoint and independently build the linked counterfactual."""

    if observation.underlying_id != underlying_fit.underlying_id:
        raise ValueError("option and linked Stage-1 result underlying do not match")
    market = invert_bsm_implied_volatility(
        option_type=observation.option_type,
        spot=observation.spot,
        strike=observation.strike,
        remaining_years=observation.remaining_years,
        integrated_rate=observation.integrated_rate,
        integrated_dividend=observation.integrated_dividend_or_carry,
        observed_price=observation.observed_price,
        contract=inversion_contract,
    )
    origin = date.fromisoformat(physical_contract.time_origin)
    valuation_time = (
        date.fromisoformat(observation.valuation_date) - origin
    ).days / 365.0
    expiry_time = (date.fromisoformat(observation.expiry) - origin).days / 365.0
    _, q_matrix = integrate_hat_basis(
        physical_contract.node_times,
        valuation_time,
        expiry_time,
    )
    beta = underlying_fit.fitted_diffusion_node_values
    integrated_variance = _quadratic(beta, q_matrix)
    tau = observation.remaining_years
    if integrated_variance <= 0.0 or tau <= 0.0:
        raise ValueError("linked integrated variance and maturity must be positive")
    effective_volatility = math.sqrt(integrated_variance / tau)
    linked_price, linked_d1, linked_d2 = bsm_price_from_integrated_variance(
        option_type=observation.option_type,
        spot=observation.spot,
        strike=observation.strike,
        integrated_rate=observation.integrated_rate,
        integrated_dividend=observation.integrated_dividend_or_carry,
        integrated_variance=integrated_variance,
    )
    vega = (
        observation.spot
        * math.exp(-observation.integrated_dividend_or_carry)
        * normal_pdf(linked_d1)
        * math.sqrt(tau)
    )
    q_beta = _matvec(q_matrix, beta)
    gradient = tuple(
        vega * value / (tau * effective_volatility) for value in q_beta
    )
    parameter_variance = _quadratic(
        gradient, underlying_fit.diffusion_covariance_matrix
    )
    if parameter_variance < -1e-12:
        raise ValueError("linked counterfactual delta-method variance is negative")
    linked_price_se = math.sqrt(max(0.0, parameter_variance))
    residual = observation.observed_price - linked_price
    residual_scale = math.sqrt(
        validation_contract.quote_noise_scale
        * validation_contract.quote_noise_scale
        + linked_price_se * linked_price_se
    )
    return Stage2RowResult(
        observation=observation,
        market_iv_status=market.status,
        market_implied_volatility=market.implied_volatility,
        market_d1=market.d1,
        market_d2=market.d2,
        linked_integrated_variance=integrated_variance,
        linked_effective_volatility=effective_volatility,
        linked_d1=linked_d1,
        linked_d2=linked_d2,
        linked_counterfactual_price=linked_price,
        linked_counterfactual_price_se=linked_price_se,
        price_gradient=gradient,
        price_residual=residual,
        standardized_residual=residual / residual_scale,
    )


def evaluate_option_series(
    observations: Iterable[OptionObservation],
    physical_contracts: Mapping[str, PhysicalFittingContract],
    underlying_fits: Mapping[str, UnderlyingFit],
    inversion_contract: BSMInversionContract,
    validation_contract: LinkedDiffusionValidationContract,
) -> tuple[OptionSeriesResult, ...]:
    """Evaluate quote-specific market IV and the shared linked validation by series."""

    if inversion_contract.output_precision != validation_contract.output_precision:
        raise ValueError("Stage-2 inversion and validation precision must match")
    groups: dict[
        tuple[str, str, str, float, str, str, float],
        list[OptionObservation],
    ] = {}
    for observation in observations:
        key = (
            observation.underlying_id,
            observation.option_contract_id,
            observation.option_type,
            observation.strike,
            observation.expiry,
            observation.settlement_style,
            observation.contract_multiplier,
        )
        groups.setdefault(key, []).append(observation)
    results: list[OptionSeriesResult] = []
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda item: (item.valuation_date, item.row_id))
        if len(rows) < validation_contract.minimum_series_observations:
            continue
        underlying_id, contract_id, option_type, strike, expiry, _, _ = key
        try:
            physical = physical_contracts[underlying_id]
            underlying_result = underlying_fits[underlying_id]
        except KeyError as error:
            raise ValueError(
                "eligible option series lacks its linked Stage-1 result"
            ) from error
        evaluated = tuple(
            evaluate_stage2_row(
                row,
                physical,
                underlying_result,
                inversion_contract,
                validation_contract,
            )
            for row in rows
        )
        residual_sse = validation_contract.row_weight * sum(
            item.standardized_residual * item.standardized_residual
            for item in evaluated
        )
        results.append(
            OptionSeriesResult(
                option_contract_id=contract_id,
                underlying_id=underlying_id,
                option_type=option_type,
                strike=strike,
                expiry=expiry,
                linked_diffusion_underlying_id=underlying_id,
                rows=evaluated,
                validation_residual_sse=residual_sse,
                series_status="COMPLETE",
                bsm_inversion_method_id=inversion_contract.method_id,
                bsm_inversion_iteration_count=inversion_contract.iterations,
                linked_diffusion_validation_id=(
                    validation_contract.validation_contract_id
                ),
                output_precision=validation_contract.output_precision,
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda item: (
                item.underlying_id,
                item.option_contract_id,
                item.option_type,
                item.strike,
                item.expiry,
            ),
        )
    )


def stage2_rows_by_id(
    series_results: Iterable[OptionSeriesResult],
) -> dict[str, Stage2RowResult]:
    result: dict[str, Stage2RowResult] = {}
    for series in series_results:
        for row in series.rows:
            if row.observation.row_id in result:
                raise ValueError("duplicate Stage-2 valuation row id")
            result[row.observation.row_id] = row
    return result


def localize_mutations(
    series_results: Iterable[OptionSeriesResult],
    validation_contract: LinkedDiffusionValidationContract,
) -> tuple[MutationDiagnosis, ...]:
    """Group linked price-residual outliers by their economic quote point."""

    flagged = [
        row
        for series in series_results
        for row in series.rows
        if abs(row.standardized_residual) >= validation_contract.residual_threshold
    ]
    groups: dict[tuple[str, str, str, float, float], list[Stage2RowResult]] = {}
    for row in flagged:
        observation = row.observation
        key = (
            observation.underlying_id,
            observation.valuation_date,
            observation.expiry,
            observation.strike,
            observation.contract_multiplier,
        )
        groups.setdefault(key, []).append(row)
    result: list[MutationDiagnosis] = []
    for key in sorted(groups):
        rows = tuple(sorted(groups[key], key=lambda item: item.observation.row_id))
        row_ids = tuple(item.observation.row_id for item in rows)
        identity_payload = json.dumps(row_ids, separators=(",", ":"))
        group_id = (
            "f2a-v5-localisation-"
            + sha256(identity_payload.encode("utf-8")).hexdigest()[:20]
        )
        signs = {1 if item.price_residual > 0.0 else -1 for item in rows}
        if len(signs) == 1:
            direction = "up" if next(iter(signs)) > 0 else "down"
        else:
            direction = "mixed"
        option_types = {item.observation.option_type for item in rows}
        proposed = (
            "U"
            if option_types == {"call", "put"} and len(rows) == 2
            else "UNCLASSIFIED_MODEL_RESIDUAL"
        )
        result.append(
            MutationDiagnosis(
                mutation_group_id=group_id,
                affected_rows=rows,
                localisation_score=max(
                    abs(item.standardized_residual) for item in rows
                ),
                proposed_mutation_family=proposed,
                inferred_direction=direction,
                estimated_mutation_magnitude=(
                    sum(item.price_residual for item in rows) / len(rows)
                ),
                output_precision=validation_contract.output_precision,
            )
        )
    return tuple(sorted(result, key=lambda item: item.mutation_group_id))


__all__ = [
    "BSM_FORMULA_ID",
    "BSM_INVERSION_METHOD_ID",
    "BSMInversionContract",
    "BSMInversionResult",
    "LINKED_DIFFUSION_VALIDATION_ID",
    "LOCALISATION_ID",
    "LinkedDiffusionValidationContract",
    "MARKET_IV_CONVERGED",
    "MARKET_IV_INVALID_BRACKET",
    "MutationDiagnosis",
    "OptionObservation",
    "OptionSeriesResult",
    "PRICE_UNCERTAINTY_ID",
    "Stage2RowResult",
    "bsm_price",
    "bsm_price_from_integrated_variance",
    "discounted_bsm_price_bounds",
    "evaluate_option_series",
    "evaluate_stage2_row",
    "invert_bsm_implied_volatility",
    "localize_mutations",
    "normal_cdf",
    "normal_pdf",
    "stable_row_id",
    "stage2_rows_by_id",
]
