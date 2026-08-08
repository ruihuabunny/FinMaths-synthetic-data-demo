"""Independent Solver linked-diffusion BSM counterfactual for F2A v5."""

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


PRICING_COUNTERFACTUAL_ID = "bsm-linked-stage1-diffusion-v1"
BSM_FORMULA_ID = "bsm-integrated-variance-european-v1"
PRICE_UNCERTAINTY_ID = "delta-method-full-diffusion-covariance-v1"
LOCALISATION_ID = "standardized-residual-contract-date-grouping-v1"


def _finite(name: str, value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


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
class PricingCounterfactualContract:
    measure: str = "USD-MONEY-MARKET-Q-v1"
    numeraire_id: str = "USD-MONEY-MARKET-ACCOUNT-v1"
    currency: str = "USD"
    valuation_time_utc: str = "16:00:00"
    measure_change: str = "girsanov_drift_only"
    volatility_measure_mapping: str = "same_deterministic_diffusion_coefficient"
    quote_noise_scale: float = 0.05
    residual_threshold: float = 4.0
    minimum_series_observations: int = 5
    row_weight: float = 1.0
    validation_loss: str = "squared"
    quote_observation_rule: str = "bid_ask_midpoint"
    rate_curve_integration_rule: str = "flat_continuous_scalar_times_actual365"
    dividend_or_carry_integration_rule: str = "flat_continuous_scalar_times_actual365"
    pricing_family: str = "BSM"
    formula_id: str = BSM_FORMULA_ID
    linked_diffusion_id: str = PRICING_COUNTERFACTUAL_ID
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
            raise ValueError("unknown common-Q/numeraire linked-diffusion contract")
        if self.quote_noise_scale <= 0.0 or not math.isfinite(self.quote_noise_scale):
            raise ValueError("quote-noise scale must be finite and positive")
        if self.residual_threshold <= 0.0 or not math.isfinite(self.residual_threshold):
            raise ValueError("residual threshold must be finite and positive")
        if self.minimum_series_observations < 2:
            raise ValueError("eligible option series need at least two observations")
        if self.row_weight <= 0.0 or not math.isfinite(self.row_weight):
            raise ValueError("Stage-2 row weight must be finite and positive")
        if self.validation_loss != "squared":
            raise ValueError("the current v5 contract freezes squared residual loss")
        if self.quote_observation_rule != "bid_ask_midpoint":
            raise ValueError("the current v5 contract observes the bid/ask midpoint")
        if (
            self.rate_curve_integration_rule
            != "flat_continuous_scalar_times_actual365"
            or self.dividend_or_carry_integration_rule
            != "flat_continuous_scalar_times_actual365"
        ):
            raise ValueError("unknown Stage-2 flat-curve integration rule")
        if self.pricing_family != "BSM" or self.formula_id != BSM_FORMULA_ID:
            raise ValueError("unknown Stage-2 pricing formula")
        if self.linked_diffusion_id != PRICING_COUNTERFACTUAL_ID:
            raise ValueError("Stage 2 must link the canonical Stage-1 diffusion")
        if self.uncertainty_method != PRICE_UNCERTAINTY_ID:
            raise ValueError("unknown Stage-2 uncertainty method")
        if self.localisation_id != LOCALISATION_ID:
            raise ValueError("unknown Stage-2 localisation rule")
        if self.residual_standardization_rule != "quote_noise_plus_parameter_variance":
            raise ValueError("unknown Stage-2 residual standardization rule")
        if not 0.0 <= self.clean_max_outlier_fraction <= 1.0:
            raise ValueError("clean residual outlier fraction must lie in [0, 1]")
        if self.output_precision < 0:
            raise ValueError("Stage-2 output precision must be nonnegative")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PricingCounterfactualContract":
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
            quote_noise_scale=float(raw.get("quote_noise_scale", raw.get("quote_noise_normalization", 0.05))),
            residual_threshold=float(raw.get("residual_threshold", 4.0)),
            minimum_series_observations=int(raw.get("minimum_series_observations", 5)),
            row_weight=float(raw.get("row_weight", 1.0)),
            validation_loss=str(raw.get("validation_statistic", "squared")),
            quote_observation_rule=str(raw.get("quote_observation_rule", "bid_ask_midpoint")),
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
            pricing_family=str(raw.get("candidate_model_family", "BSM")),
            formula_id=str(raw.get("formula_id", BSM_FORMULA_ID)),
            linked_diffusion_id=str(raw.get("linked_diffusion_variant_id", PRICING_COUNTERFACTUAL_ID)),
            uncertainty_method=str(raw.get("price_uncertainty_method", PRICE_UNCERTAINTY_ID)),
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
        return (date.fromisoformat(self.expiry) - date.fromisoformat(self.valuation_date)).days / 365.0


@dataclass(frozen=True)
class CounterfactualRow:
    observation: OptionObservation
    integrated_variance: float
    average_volatility: float
    d1: float
    d2: float
    fitted_counterfactual_price: float
    fitted_counterfactual_price_se: float
    price_gradient: tuple[float, ...]
    residual: float
    standardized_residual: float

    def to_dict(self, precision: int = 10) -> dict[str, Any]:
        q = lambda value: _quantize(value, precision)
        return {
            "row_id": self.observation.row_id,
            "integrated_variance": q(self.integrated_variance),
            "average_volatility": q(self.average_volatility),
            "d1": q(self.d1),
            "d2": q(self.d2),
            "observed_price": q(self.observation.observed_price),
            "fitted_counterfactual_price": q(self.fitted_counterfactual_price),
            "fitted_counterfactual_price_se": q(self.fitted_counterfactual_price_se),
            "price_gradient": [q(value) for value in self.price_gradient],
            "residual": q(self.residual),
            "standardized_residual": q(self.standardized_residual),
        }


@dataclass(frozen=True)
class OptionFit:
    option_contract_id: str
    underlying_id: str
    option_type: str
    strike: float
    expiry: str
    linked_diffusion_underlying_id: str
    rows: tuple[CounterfactualRow, ...]
    objective_value: float
    fit_status: str
    output_precision: int = 10

    def to_dict(self) -> dict[str, Any]:
        q = lambda value: _quantize(value, self.output_precision)
        ordered = tuple(sorted(self.rows, key=lambda item: (item.observation.valuation_date, item.observation.row_id)))
        return {
            "option_contract_id": self.option_contract_id,
            "underlying_id": self.underlying_id,
            "valuation_row_ids": [item.observation.row_id for item in ordered],
            "option_type": self.option_type,
            "strike": q(self.strike),
            "expiry": self.expiry,
            "pricing_family": "BSM",
            "linked_diffusion_underlying_id": self.linked_diffusion_underlying_id,
            "integrated_variance_by_row_id": {
                item.observation.row_id: q(item.integrated_variance) for item in ordered
            },
            "fitted_counterfactual_prices_by_row_id": {
                item.observation.row_id: q(item.fitted_counterfactual_price) for item in ordered
            },
            "fitted_counterfactual_price_se_by_row_id": {
                item.observation.row_id: q(item.fitted_counterfactual_price_se) for item in ordered
            },
            "d1_values_by_row_id": {
                item.observation.row_id: q(item.d1) for item in ordered
            },
            "d2_values_by_row_id": {
                item.observation.row_id: q(item.d2) for item in ordered
            },
            "standardized_residuals_by_row_id": {
                item.observation.row_id: q(item.standardized_residual) for item in ordered
            },
            "objective_value": q(self.objective_value),
            "fit_status": self.fit_status,
            "optional_iv_diagnostics": [],
        }


@dataclass(frozen=True)
class MutationDiagnosis:
    mutation_group_id: str
    affected_rows: tuple[CounterfactualRow, ...]
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
            "affected_dates": sorted({item.observation.valuation_date for item in rows}),
            "observed_quotes": {
                item.observation.row_id: q(item.observation.observed_price) for item in rows
            },
            "fitted_clean_counterfactual_quotes": {
                item.observation.row_id: q(item.fitted_counterfactual_price) for item in rows
            },
            "residuals": {
                item.observation.row_id: q(item.residual) for item in rows
            },
            "localisation_score": q(self.localisation_score),
            "proposed_mutation_family": self.proposed_mutation_family,
            "inferred_direction": self.inferred_direction,
            "estimated_mutation_magnitude": q(self.estimated_mutation_magnitude),
        }


def bsm_counterfactual(
    *,
    option_type: str,
    spot: float,
    strike: float,
    integrated_rate: float,
    integrated_dividend: float,
    integrated_variance: float,
) -> tuple[float, float, float]:
    """Return price, d1 and d2 for deterministic integrated BSM inputs."""

    if spot <= 0.0 or strike <= 0.0 or integrated_variance <= 0.0:
        raise ValueError("BSM requires positive spot, strike and integrated variance")
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


def _quadratic(vector: Sequence[float], matrix: Sequence[Sequence[float]]) -> float:
    return sum(
        vector[i] * matrix[i][j] * vector[j]
        for i in range(len(vector))
        for j in range(len(vector))
    )


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> tuple[float, ...]:
    return tuple(sum(a * b for a, b in zip(row, vector)) for row in matrix)


def counterfactual_row(
    observation: OptionObservation,
    physical_contract: PhysicalFittingContract,
    underlying_fit: UnderlyingFit,
    pricing_contract: PricingCounterfactualContract,
) -> CounterfactualRow:
    if observation.underlying_id != underlying_fit.underlying_id:
        raise ValueError("option and linked Stage-1 fit underlying do not match")
    origin = date.fromisoformat(physical_contract.time_origin)
    valuation_time = (date.fromisoformat(observation.valuation_date) - origin).days / 365.0
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
    average_volatility = math.sqrt(integrated_variance / tau)
    price, d1, d2 = bsm_counterfactual(
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
        * normal_pdf(d1)
        * math.sqrt(tau)
    )
    q_beta = _matvec(q_matrix, beta)
    gradient = tuple(
        vega * value / (tau * average_volatility) for value in q_beta
    )
    covariance = underlying_fit.diffusion_covariance_matrix
    variance = _quadratic(gradient, covariance)
    if variance < -1e-12:
        raise ValueError("counterfactual delta-method variance is negative")
    price_se = math.sqrt(max(0.0, variance))
    residual = observation.observed_price - price
    residual_scale = math.sqrt(
        pricing_contract.quote_noise_scale * pricing_contract.quote_noise_scale
        + price_se * price_se
    )
    standardized = residual / residual_scale
    return CounterfactualRow(
        observation=observation,
        integrated_variance=integrated_variance,
        average_volatility=average_volatility,
        d1=d1,
        d2=d2,
        fitted_counterfactual_price=price,
        fitted_counterfactual_price_se=price_se,
        price_gradient=gradient,
        residual=residual,
        standardized_residual=standardized,
    )


def fit_option_series(
    observations: Iterable[OptionObservation],
    physical_contracts: Mapping[str, PhysicalFittingContract],
    underlying_fits: Mapping[str, UnderlyingFit],
    pricing_contract: PricingCounterfactualContract,
) -> tuple[OptionFit, ...]:
    """Build constrained BSM counterfactuals without fitting option volatility."""

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
    results: list[OptionFit] = []
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda item: (item.valuation_date, item.row_id))
        if len(rows) < pricing_contract.minimum_series_observations:
            continue
        underlying_id, contract_id, option_type, strike, expiry, _, _ = key
        try:
            physical = physical_contracts[underlying_id]
            fitted = underlying_fits[underlying_id]
        except KeyError as error:
            raise ValueError("eligible option series lacks its linked Stage-1 fit") from error
        counterfactuals = tuple(
            counterfactual_row(row, physical, fitted, pricing_contract) for row in rows
        )
        objective = pricing_contract.row_weight * sum(
            item.standardized_residual * item.standardized_residual
            for item in counterfactuals
        )
        results.append(
            OptionFit(
                option_contract_id=contract_id,
                underlying_id=underlying_id,
                option_type=option_type,
                strike=strike,
                expiry=expiry,
                linked_diffusion_underlying_id=underlying_id,
                rows=counterfactuals,
                objective_value=objective,
                fit_status="CONVERGED",
                output_precision=pricing_contract.output_precision,
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


def counterfactual_rows_by_id(option_fits: Iterable[OptionFit]) -> dict[str, CounterfactualRow]:
    result: dict[str, CounterfactualRow] = {}
    for fit in option_fits:
        for row in fit.rows:
            if row.observation.row_id in result:
                raise ValueError("duplicate Stage-2 valuation row id")
            result[row.observation.row_id] = row
    return result


def localize_mutations(
    option_fits: Iterable[OptionFit],
    pricing_contract: PricingCounterfactualContract,
) -> tuple[MutationDiagnosis, ...]:
    """Group public-child residual outliers by their economic quote point."""

    flagged = [
        row
        for fit in option_fits
        for row in fit.rows
        if abs(row.standardized_residual) >= pricing_contract.residual_threshold
    ]
    groups: dict[tuple[str, str, str, float, float], list[CounterfactualRow]] = {}
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
        group_id = "f2a-v5-localisation-" + sha256(identity_payload.encode("utf-8")).hexdigest()[:20]
        signs = {1 if item.residual > 0.0 else -1 for item in rows}
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
                localisation_score=max(abs(item.standardized_residual) for item in rows),
                proposed_mutation_family=proposed,
                inferred_direction=direction,
                estimated_mutation_magnitude=sum(item.residual for item in rows) / len(rows),
                output_precision=pricing_contract.output_precision,
            )
        )
    return tuple(sorted(result, key=lambda item: item.mutation_group_id))


__all__ = [
    "BSM_FORMULA_ID",
    "CounterfactualRow",
    "LOCALISATION_ID",
    "MutationDiagnosis",
    "OptionFit",
    "OptionObservation",
    "PRICE_UNCERTAINTY_ID",
    "PRICING_COUNTERFACTUAL_ID",
    "PricingCounterfactualContract",
    "bsm_counterfactual",
    "counterfactual_row",
    "counterfactual_rows_by_id",
    "fit_option_series",
    "localize_mutations",
    "normal_cdf",
    "normal_pdf",
    "stable_row_id",
]
