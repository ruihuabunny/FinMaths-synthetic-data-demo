"""Independent Solver post-cost fitted-counterfactual X/U/T scanner.

This module deliberately does not issue executable-arbitrage certificates.  It
compares public executable quotes with the linked BSM counterfactual, charges the
declared hurdles, and reports a model-based relative-value signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from itertools import combinations
import math
from typing import Any, Iterable, Mapping, Sequence

from synthetic_derivatives.solver.f2a_v5_types import (
    PublicMarketSlice,
    gcd_normalized_convexity_positions,
    terminal_spot_bid,
    terminal_spot_outflow,
)
from synthetic_derivatives.solver.f2a_v5_stage1 import UnderlyingFit
from synthetic_derivatives.solver.f2a_v5_stage2 import CounterfactualRow, stable_row_id


MODEL_SIGNAL_VARIANT_ID = "bsm_model_reconstruction_xut_signal_f2a_v5"
MODEL_SIGNAL_CATALOGUE_ID = "bsm-f2a-model-signal-candidate-catalogue-v1"
CANONICAL_MODEL_FAMILY_ORDER = ("X", "U", "T")


def _quantize(value: float, precision: int) -> float:
    if not math.isfinite(value):
        raise ValueError("canonical model-signal output must be finite")
    return float(
        Decimal(str(value)).quantize(
            Decimal(1).scaleb(-precision),
            rounding=ROUND_HALF_EVEN,
        )
    )


def _quadratic(vector: Sequence[float], matrix: Sequence[Sequence[float]]) -> float:
    return sum(
        vector[i] * matrix[i][j] * vector[j]
        for i in range(len(vector))
        for j in range(len(vector))
    )


@dataclass(frozen=True)
class ModelSignalContract:
    option_fee_per_contract_per_side: float = 0.50
    option_fee_units: str = "USD_per_contract_per_side"
    underlying_transaction_cost: float = 0.0005
    underlying_transaction_cost_units: str = "fraction_of_spot_notional"
    edge_units: str = "USD_per_candidate_notional"
    candidate_catalogue_id: str = MODEL_SIGNAL_CATALOGUE_ID
    directional_candidate_rule: str = "both_directions_have_stable_ids"
    strict_inequality_rule: str = "net_signal_edge_gt_zero"
    activation_checkpoint: str = "after_half_even_output_quantization"
    observed_quote_execution_rule: str = "midpoint_plus_exact_half_spread_hurdle"
    fitted_counterfactual_rule: str = "linked_stage1_bsm"
    contract_multiplier_rule: str = "every_option_leg"
    funding_rule: str = "public_discount_inputs"
    dividend_or_carry_rule: str = "public_integrated_carry_inputs"
    net_edge_rule: str = "gross_minus_spread_fees_underlying_and_funding_hurdles"
    uncertainty_propagation_rule: str = "full_shared_diffusion_delta_method"
    canonical_order: str = "valuation_date_underlying_family_candidate"
    output_precision: int = 10

    def __post_init__(self) -> None:
        if not math.isfinite(self.option_fee_per_contract_per_side) or self.option_fee_per_contract_per_side < 0.0:
            raise ValueError("option fee must be finite and nonnegative")
        if not math.isfinite(self.underlying_transaction_cost) or not 0.0 <= self.underlying_transaction_cost < 1.0:
            raise ValueError("underlying transaction cost must lie in [0, 1)")
        if (
            self.option_fee_units != "USD_per_contract_per_side"
            or self.underlying_transaction_cost_units != "fraction_of_spot_notional"
            or self.edge_units != "USD_per_candidate_notional"
        ):
            raise ValueError("unknown v5 model-signal units")
        if self.candidate_catalogue_id != MODEL_SIGNAL_CATALOGUE_ID:
            raise ValueError("unknown v5 model-signal catalogue")
        expected = (
            (self.directional_candidate_rule, "both_directions_have_stable_ids"),
            (self.observed_quote_execution_rule, "midpoint_plus_exact_half_spread_hurdle"),
            (self.fitted_counterfactual_rule, "linked_stage1_bsm"),
            (self.contract_multiplier_rule, "every_option_leg"),
            (self.funding_rule, "public_discount_inputs"),
            (self.dividend_or_carry_rule, "public_integrated_carry_inputs"),
            (self.net_edge_rule, "gross_minus_spread_fees_underlying_and_funding_hurdles"),
            (self.uncertainty_propagation_rule, "full_shared_diffusion_delta_method"),
            (self.canonical_order, "valuation_date_underlying_family_candidate"),
        )
        if any(actual != frozen for actual, frozen in expected):
            raise ValueError("unknown v5 model-signal economic contract")
        if self.strict_inequality_rule != "net_signal_edge_gt_zero":
            raise ValueError("the v5 signal boundary is strictly positive")
        if self.activation_checkpoint != "after_half_even_output_quantization":
            raise ValueError("unknown model-signal activation checkpoint")
        if self.output_precision < 0:
            raise ValueError("model-signal output precision must be nonnegative")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ModelSignalContract":
        return cls(
            option_fee_per_contract_per_side=float(raw.get("option_fee", 0.50)),
            option_fee_units=str(
                raw.get("option_fee_units", "USD_per_contract_per_side")
            ),
            underlying_transaction_cost=float(raw.get("underlying_transaction_cost", 0.0005)),
            underlying_transaction_cost_units=str(
                raw.get(
                    "underlying_transaction_cost_units",
                    "fraction_of_spot_notional",
                )
            ),
            edge_units=str(raw.get("edge_units", "USD_per_candidate_notional")),
            candidate_catalogue_id=str(raw.get("candidate_enumeration_version", MODEL_SIGNAL_CATALOGUE_ID)),
            directional_candidate_rule=str(
                raw.get("directional_candidate_rule", "both_directions_have_stable_ids")
            ),
            strict_inequality_rule=str(raw.get("strict_inequality_rule", "net_signal_edge_gt_zero")),
            activation_checkpoint=str(
                raw.get(
                    "activation_checkpoint",
                    "after_half_even_output_quantization",
                )
            ),
            observed_quote_execution_rule=str(raw.get("observed_quote_execution_rule", "midpoint_plus_exact_half_spread_hurdle")),
            fitted_counterfactual_rule=str(raw.get("fitted_counterfactual_rule", "linked_stage1_bsm")),
            contract_multiplier_rule=str(
                raw.get("contract_multiplier_rule", "every_option_leg")
            ),
            funding_rule=str(raw.get("funding_rule", "public_discount_inputs")),
            dividend_or_carry_rule=str(
                raw.get("dividend_or_carry_rule", "public_integrated_carry_inputs")
            ),
            net_edge_rule=str(
                raw.get(
                    "net_edge_rule",
                    "gross_minus_spread_fees_underlying_and_funding_hurdles",
                )
            ),
            uncertainty_propagation_rule=str(raw.get("uncertainty_propagation_rule", "full_shared_diffusion_delta_method")),
            canonical_order=str(
                raw.get("canonical_order", "valuation_date_underlying_family_candidate")
            ),
            output_precision=int(raw.get("output_precision", 10)),
        )


@dataclass(frozen=True)
class ModelSignalCandidate:
    family: str
    candidate_id: str
    rows: tuple[CounterfactualRow, ...]
    positions: tuple[float, ...]
    direction: str
    observed_executable_value: float
    fitted_counterfactual_value: float
    gross_model_edge: float
    option_spread_cost: float
    option_fees: float
    underlying_costs: float
    funding_and_dividend: float
    net_signal_edge: float
    parameter_standard_error: float
    standardized_margin: float
    output_precision: int = 10

    @property
    def active(self) -> bool:
        return _quantize(self.net_signal_edge, self.output_precision) > 0.0

    def to_dict(self) -> dict[str, Any]:
        q = lambda value: _quantize(value, self.output_precision)
        legs = [
            {
                "row_id": row.observation.row_id,
                "option_id": row.observation.option_id,
                "position": q(position),
                "option_type": row.observation.option_type,
                "strike": q(row.observation.strike),
                "expiry": row.observation.expiry,
            }
            for row, position in zip(self.rows, self.positions)
        ]
        return {
            "family": self.family,
            "candidate_id": self.candidate_id,
            "row_ids": [row.observation.row_id for row in self.rows],
            "legs": legs,
            "direction": self.direction,
            "observed_executable_value": q(self.observed_executable_value),
            "fitted_counterfactual_value": q(self.fitted_counterfactual_value),
            "gross_model_edge": q(self.gross_model_edge),
            "option_spread_cost": q(self.option_spread_cost),
            "option_fees": q(self.option_fees),
            "underlying_costs": q(self.underlying_costs),
            "funding_and_dividend": q(self.funding_and_dividend),
            "net_signal_edge": q(self.net_signal_edge),
            "parameter_standard_error": q(self.parameter_standard_error),
            "standardized_margin": q(self.standardized_margin),
            "canonical_active": self.active,
        }


@dataclass(frozen=True)
class ModelSignalSlice:
    underlying_id: str
    valuation_date: str
    candidates: tuple[ModelSignalCandidate, ...]

    @property
    def family_bits(self) -> tuple[bool, bool, bool]:
        return tuple(
            any(item.family == family and item.active for item in self.candidates)
            for family in CANONICAL_MODEL_FAMILY_ORDER
        )  # type: ignore[return-value]

    @property
    def signature(self) -> str:
        return "".join("1" if bit else "0" for bit in self.family_bits)

    @property
    def active_candidates(self) -> tuple[ModelSignalCandidate, ...]:
        return tuple(item for item in self.candidates if item.active)

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying_id": self.underlying_id,
            "valuation_date": self.valuation_date,
            "signature": self.signature,
            "active_signals": [item.to_dict() for item in self.active_candidates],
        }


@dataclass(frozen=True)
class DatabaseSignalResult:
    slices: tuple[ModelSignalSlice, ...]

    @property
    def active_slices(self) -> tuple[ModelSignalSlice, ...]:
        return tuple(item for item in self.slices if item.signature != "000")

    def to_dict(self) -> dict[str, Any]:
        family_counts = {
            family: sum(result.family_bits[index] for result in self.slices)
            for index, family in enumerate(CANONICAL_MODEL_FAMILY_ORDER)
        }
        return {
            "slices": [item.to_dict() for item in self.active_slices],
            "full_scan_summary": {
                "scanned_slice_count": len(self.slices),
                "active_slice_count": len(self.active_slices),
                "scanned_candidate_count": sum(len(item.candidates) for item in self.slices),
                "active_candidate_count": sum(len(item.active_candidates) for item in self.slices),
                "family_active_slice_counts": family_counts,
                "complete_full_database_scan": True,
            },
        }


def _candidate(
    *,
    family: str,
    candidate_id: str,
    weighted_rows: Sequence[tuple[CounterfactualRow, float]],
    direction: int,
    underlying_fit: UnderlyingFit,
    contract: ModelSignalContract,
    underlying_costs: float = 0.0,
    funding_and_dividend: float = 0.0,
) -> ModelSignalCandidate:
    if family not in CANONICAL_MODEL_FAMILY_ORDER or direction not in {-1, 1}:
        raise ValueError("invalid model-signal family or direction")
    rows = tuple(item[0] for item in weighted_rows)
    weights = tuple(float(item[1]) for item in weighted_rows)
    if not rows or any(row.observation.underlying_id != underlying_fit.underlying_id for row in rows):
        raise ValueError("candidate rows must share the linked underlying fit")
    observed_base = sum(
        weight * row.observation.contract_multiplier * row.observation.observed_price
        for row, weight in weighted_rows
    )
    counterfactual_base = sum(
        weight * row.observation.contract_multiplier * row.fitted_counterfactual_price
        for row, weight in weighted_rows
    )
    observed_functional = direction * observed_base
    counterfactual_functional = direction * counterfactual_base
    gross_edge = observed_functional - counterfactual_functional
    spread_cost = sum(
        abs(weight)
        * row.observation.contract_multiplier
        * (row.observation.ask - row.observation.bid)
        / 2.0
        for row, weight in weighted_rows
    )
    fees = sum(abs(weight) for weight in weights) * contract.option_fee_per_contract_per_side
    hurdle = spread_cost + fees + underlying_costs + funding_and_dividend
    net_edge = gross_edge - hurdle
    gradient_size = len(rows[0].price_gradient)
    edge_gradient = tuple(
        -direction
        * sum(
            weight * row.observation.contract_multiplier * row.price_gradient[index]
            for row, weight in weighted_rows
        )
        for index in range(gradient_size)
    )
    parameter_variance = _quadratic(
        edge_gradient,
        underlying_fit.diffusion_covariance_matrix,
    )
    if parameter_variance < -1e-10:
        raise ValueError("candidate parameter variance is negative")
    parameter_se = math.sqrt(max(0.0, parameter_variance))
    scale_floor = 10.0 ** (-contract.output_precision)
    standardized_margin = net_edge / max(parameter_se, scale_floor)
    # A positive direction means the public relationship is rich to its fitted
    # value; the convergence trade therefore takes the negative relationship.
    positions = tuple(-direction * weight for weight in weights)
    return ModelSignalCandidate(
        family=family,
        candidate_id=candidate_id,
        rows=rows,
        positions=positions,
        direction="positive" if direction > 0 else "negative",
        observed_executable_value=observed_functional - hurdle,
        fitted_counterfactual_value=counterfactual_functional,
        gross_model_edge=gross_edge,
        option_spread_cost=spread_cost,
        option_fees=fees,
        underlying_costs=underlying_costs,
        funding_and_dividend=funding_and_dividend,
        net_signal_edge=net_edge,
        parameter_standard_error=parameter_se,
        standardized_margin=standardized_margin,
        output_precision=contract.output_precision,
    )


def _row(
    lookup: Mapping[str, CounterfactualRow],
    market: PublicMarketSlice,
    option_id: str,
) -> CounterfactualRow | None:
    return lookup.get(stable_row_id(market.underlying_id, market.valuation_date, option_id))


def _canonical_candidate_key(candidate: ModelSignalCandidate) -> tuple[int, str]:
    return CANONICAL_MODEL_FAMILY_ORDER.index(candidate.family), candidate.candidate_id


def scan_model_signal_slice(
    market: PublicMarketSlice,
    counterfactual_by_row_id: Mapping[str, CounterfactualRow],
    underlying_fit: UnderlyingFit,
    contract: ModelSignalContract | None = None,
) -> ModelSignalSlice:
    """Enumerate stable directional X/U/T candidates for one public slice."""

    signal_contract = contract or ModelSignalContract()
    if market.underlying_id != underlying_fit.underlying_id:
        raise ValueError("market slice and Stage-1 fit underlying do not match")
    buckets: dict[tuple[str, float], dict[Decimal, dict[str, CounterfactualRow]]] = {}
    for quote in market.quotes:
        row = _row(counterfactual_by_row_id, market, quote.option_id)
        if row is None:
            continue
        buckets.setdefault((quote.expiry, quote.multiplier), {}).setdefault(
            quote.strike, {}
        )[quote.call_put] = row
    candidates: list[ModelSignalCandidate] = []

    # X: strike monotonicity and nonuniform convexity residual relationships.
    for expiry, multiplier in sorted(buckets):
        pairs = buckets[(expiry, multiplier)]
        strikes = sorted(pairs)
        for lower, upper in combinations(strikes, 2):
            for option_type, weights in (
                ("call", ((pairs[lower].get("call"), 1.0), (pairs[upper].get("call"), -1.0))),
                ("put", ((pairs[lower].get("put"), -1.0), (pairs[upper].get("put"), 1.0))),
            ):
                if any(row is None for row, _ in weights):
                    continue
                typed_weights = tuple((row, weight) for row, weight in weights if row is not None)
                base = (
                    f"X|strike-monotonicity|{market.underlying_id}|{market.valuation_date}|"
                    f"{expiry}|{option_type}|{lower}|{upper}|{multiplier:g}"
                )
                for direction in (-1, 1):
                    candidates.append(
                        _candidate(
                            family="X",
                            candidate_id=f"{base}|direction:{direction:+d}",
                            weighted_rows=typed_weights,
                            direction=direction,
                            underlying_fit=underlying_fit,
                            contract=signal_contract,
                        )
                    )
        for lower, middle, upper in combinations(strikes, 3):
            magnitudes = gcd_normalized_convexity_positions(lower, middle, upper)
            for option_type in ("call", "put"):
                rows = (
                    pairs[lower].get(option_type),
                    pairs[middle].get(option_type),
                    pairs[upper].get(option_type),
                )
                if any(row is None for row in rows):
                    continue
                weighted = tuple(
                    (row, weight)
                    for row, weight in zip(
                        rows,
                        (-float(magnitudes[0]), float(magnitudes[1]), -float(magnitudes[2])),
                    )
                    if row is not None
                )
                base = (
                    f"X|strike-convexity|{market.underlying_id}|{market.valuation_date}|"
                    f"{expiry}|{option_type}|{lower}|{middle}|{upper}|{multiplier:g}"
                )
                for direction in (-1, 1):
                    candidates.append(
                        _candidate(
                            family="X",
                            candidate_id=f"{base}|direction:{direction:+d}",
                            weighted_rows=weighted,
                            direction=direction,
                            underlying_fit=underlying_fit,
                            contract=signal_contract,
                        )
                    )

        # U: each vanilla quote is compared with the same-underlying BSM value,
        # and put/call parity residuals are included as separate relationships.
        context = market.expiry_inputs[expiry]
        for strike in strikes:
            pair = pairs[strike]
            stock_cost = (
                multiplier
                * market.spot
                * signal_contract.underlying_transaction_cost
            )
            for option_type in ("call", "put"):
                row = pair.get(option_type)
                if row is None:
                    continue
                base = (
                    f"U|vanilla-underlying-cash-consistency|{market.underlying_id}|"
                    f"{market.valuation_date}|{expiry}|{option_type}|{strike}|{multiplier:g}"
                )
                for direction in (-1, 1):
                    candidates.append(
                        _candidate(
                            family="U",
                            candidate_id=f"{base}|direction:{direction:+d}",
                            weighted_rows=((row, 1.0),),
                            direction=direction,
                            underlying_fit=underlying_fit,
                            contract=signal_contract,
                            underlying_costs=stock_cost,
                        )
                    )
            if set(pair) == {"call", "put"}:
                parity_cost = abs(
                    terminal_spot_outflow(
                        multiplier,
                        market.spot,
                        context.integrated_dividend_yield,
                        signal_contract.underlying_transaction_cost,
                    )
                    - multiplier
                    * market.spot
                    * math.exp(-context.integrated_dividend_yield)
                )
                weighted = ((pair["call"], 1.0), (pair["put"], -1.0))
                base = (
                    f"U|put-call-fitted-consistency|{market.underlying_id}|"
                    f"{market.valuation_date}|{expiry}|{strike}|{multiplier:g}"
                )
                for direction in (-1, 1):
                    candidates.append(
                        _candidate(
                            family="U",
                            candidate_id=f"{base}|direction:{direction:+d}",
                            weighted_rows=weighted,
                            direction=direction,
                            underlying_fit=underlying_fit,
                            contract=signal_contract,
                            underlying_costs=parity_cost,
                        )
                    )

    # T: all same-strike two-expiry call relationships, in both frozen directions.
    calls: dict[tuple[str, Decimal, float], CounterfactualRow] = {}
    for (expiry, multiplier), pairs in buckets.items():
        for strike, pair in pairs.items():
            if "call" in pair:
                calls[(expiry, strike, multiplier)] = pair["call"]
    expiries = sorted({key[0] for key in calls})
    for early, late in combinations(expiries, 2):
        early_context = market.expiry_inputs[early]
        late_context = market.expiry_inputs[late]
        dividend_12 = (
            late_context.integrated_dividend_yield
            - early_context.integrated_dividend_yield
        )
        beta_12 = terminal_spot_bid(
            1.0,
            dividend_12,
            signal_contract.underlying_transaction_cost,
        )
        early_keys = {
            (strike, multiplier)
            for expiry, strike, multiplier in calls
            if expiry == early
        }
        late_keys = {
            (strike, multiplier)
            for expiry, strike, multiplier in calls
            if expiry == late
        }
        for strike, multiplier in sorted(early_keys & late_keys):
            early_row = calls[(early, strike, multiplier)]
            late_row = calls[(late, strike, multiplier)]
            delta_0 = multiplier * (1.0 - beta_12 + 1e-8)
            frictionless_outflow = delta_0 * market.spot * math.exp(
                -early_context.integrated_dividend_yield
            )
            executable_outflow = terminal_spot_outflow(
                delta_0,
                market.spot,
                early_context.integrated_dividend_yield,
                signal_contract.underlying_transaction_cost,
            )
            stock_cost = abs(executable_outflow - frictionless_outflow)
            funding_factor = early_context.discount_factor / late_context.discount_factor
            weighted = ((early_row, 1.0), (late_row, -1.0))
            base = (
                f"T|two-expiry-call-stock-flip|{market.underlying_id}|"
                f"{market.valuation_date}|{early}|{late}|{strike}|{multiplier:g}"
            )
            for direction in (-1, 1):
                candidates.append(
                    _candidate(
                        family="T",
                        candidate_id=f"{base}|direction:{direction:+d}",
                        weighted_rows=weighted,
                        direction=direction,
                        underlying_fit=underlying_fit,
                        contract=signal_contract,
                        underlying_costs=stock_cost,
                        # Funding and carry are already inside both BSM values and
                        # the stock-flip hurdle.  This field records no extra fee.
                        funding_and_dividend=0.0 * funding_factor,
                    )
                )

    candidates.sort(key=_canonical_candidate_key)
    ids = [item.candidate_id for item in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate directional model-signal candidate id")
    return ModelSignalSlice(
        underlying_id=market.underlying_id,
        valuation_date=market.valuation_date,
        candidates=tuple(candidates),
    )


def scan_model_signals(
    market_slices: Iterable[PublicMarketSlice],
    counterfactual_by_row_id: Mapping[str, CounterfactualRow],
    underlying_fits: Mapping[str, UnderlyingFit],
    contract: ModelSignalContract | None = None,
) -> DatabaseSignalResult:
    results: list[ModelSignalSlice] = []
    seen: set[tuple[str, str]] = set()
    for market in sorted(
        market_slices,
        key=lambda item: (item.valuation_date, item.underlying_id),
    ):
        key = (market.valuation_date, market.underlying_id)
        if key in seen:
            raise ValueError("duplicate market slice in full model-signal scan")
        seen.add(key)
        try:
            fit = underlying_fits[market.underlying_id]
        except KeyError as error:
            raise ValueError("market slice lacks its Stage-1 fit") from error
        results.append(
            scan_model_signal_slice(
                market,
                counterfactual_by_row_id,
                fit,
                contract,
            )
        )
    return DatabaseSignalResult(tuple(results))


__all__ = [
    "CANONICAL_MODEL_FAMILY_ORDER",
    "DatabaseSignalResult",
    "MODEL_SIGNAL_CATALOGUE_ID",
    "MODEL_SIGNAL_VARIANT_ID",
    "ModelSignalCandidate",
    "ModelSignalContract",
    "ModelSignalSlice",
    "scan_model_signal_slice",
    "scan_model_signals",
]
