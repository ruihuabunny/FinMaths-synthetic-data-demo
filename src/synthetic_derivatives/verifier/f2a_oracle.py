"""Trusted executable F2A v4 oracle.

The oracle classifies only the frozen finite catalogue.  Its output is not a
claim about full-market dynamic arbitrage or NFLVR.  Public market values are
cast to binary64 once, candidates are enumerated deterministically, and the
candidate-specific setup boundary is compared without a numerical tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import combinations
import json
from math import exp, isfinite
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.verifier.f2a_contract import (
    ExpiryInputs,
    OptionQuote,
    discounted_price_bound_surpluses,
    executable_put_call_parity_surpluses,
    gcd_normalized_convexity_positions,
    nonuniform_convexity_surplus,
    option_ask_amount,
    option_bid_amount,
    strike_monotonicity_surplus,
    terminal_spot_ask,
    terminal_spot_bid,
    terminal_spot_outflow,
)


CALENDAR_FAMILY_ID = "transaction-cost-aware-two-expiry-call-stock-flip-v1"
CALENDAR_EXPOSURE_BUFFER_RATIO = 1e-8
CANONICAL_FAMILY_ORDER = ("cross-sectional", "cross-asset", "calendar")


@dataclass(frozen=True)
class PublicMarketSlice:
    snapshot_id: str
    snapshot_revision: int
    valuation_date: str
    underlying_id: str
    spot: float
    currency: str
    quotes: tuple[OptionQuote, ...]
    expiry_inputs: Mapping[str, ExpiryInputs]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_revision": self.snapshot_revision,
            "valuation_date": self.valuation_date,
            "underlying_id": self.underlying_id,
            "spot": self.spot,
            "currency": self.currency,
            "expiry_inputs": {
                expiry: {
                    "discount_factor": item.discount_factor,
                    "integrated_dividend_yield": item.integrated_dividend_yield,
                }
                for expiry, item in sorted(self.expiry_inputs.items())
            },
            "quotes": [
                {
                    "option_id": quote.option_id,
                    "expiry": quote.expiry,
                    "call_put": quote.call_put,
                    "strike": float(quote.strike),
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "contract_multiplier": quote.multiplier,
                    "underlying_id": quote.underlying_id,
                    "valuation_date": quote.valuation_date,
                    "currency": quote.currency,
                    "exercise_style": quote.exercise_style,
                    "settlement_type": quote.settlement_type,
                }
                for quote in self.quotes
            ],
        }


@dataclass(frozen=True)
class PublicChild:
    task_id: str
    snapshot_id: str
    snapshot_revision: int
    status: str
    slices: tuple[PublicMarketSlice, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "snapshot_id": self.snapshot_id,
            "snapshot_revision": self.snapshot_revision,
            "status": self.status,
            "slices": [market_slice.to_dict() for market_slice in self.slices],
        }


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    family: str
    template_id: str
    initial_surplus_usd: float
    setup_boundary_kind: str
    terminal_nonnegative: bool
    strict_gain_open_set: bool
    is_arbitrage: bool
    details: Mapping[str, Any]


@dataclass(frozen=True)
class OracleResult:
    candidates: tuple[CandidateEvidence, ...]

    @property
    def family_bits(self) -> tuple[bool, bool, bool]:
        return tuple(
            any(
                candidate.family == family and candidate.is_arbitrage
                for candidate in self.candidates
            )
            for family in CANONICAL_FAMILY_ORDER
        )  # type: ignore[return-value]

    @property
    def realized_signature(self) -> str:
        return "".join("1" if bit else "0" for bit in self.family_bits)

    @property
    def arbitrage_type(self) -> tuple[str, ...]:
        return tuple(
            family
            for family, active in zip(CANONICAL_FAMILY_ORDER, self.family_bits)
            if active
        )

    @property
    def arbitrage_opportunity(self) -> bool:
        return any(self.family_bits)

    @property
    def calendar_candidate_count(self) -> int:
        return sum(candidate.family == "calendar" for candidate in self.candidates)

    def orm_answer(self) -> dict[str, Any]:
        return {
            "arbitrage_opportunity": self.arbitrage_opportunity,
            "arbitrage_type": list(self.arbitrage_type),
        }


def _canonical_candidate_order(item: CandidateEvidence) -> tuple[Any, ...]:
    family_rank = {
        family: index for index, family in enumerate(CANONICAL_FAMILY_ORDER)
    }
    if item.family == "calendar":
        return (
            family_rank[item.family],
            item.details["valuation_date"],
            item.details["underlying_id"],
            item.details["T1"],
            item.details["T2"],
            item.details["strike"],
            item.details["contract_multiplier"],
            item.details["early_option_id"],
            item.details["late_option_id"],
        )
    return (family_rank[item.family], item.candidate_id)


def _finite(name: str, value: float) -> float:
    cast = float(value)
    if not isfinite(cast):
        raise ValueError(f"{name} must be finite")
    return cast


def market_slice_from_dict(data: Mapping[str, Any]) -> PublicMarketSlice:
    """Cast one public JSON slice into the immutable canonical input shape."""

    spot = _finite("spot", data["spot"])
    if spot <= 0.0:
        raise ValueError("spot must be strictly positive")
    valuation_date = str(data["valuation_date"])
    date.fromisoformat(valuation_date)
    underlying_id = str(data["underlying_id"])
    currency = str(data.get("currency", "USD"))

    expiry_inputs: dict[str, ExpiryInputs] = {}
    for expiry, raw in data["expiry_inputs"].items():
        date.fromisoformat(expiry)
        discount = _finite("discount_factor", raw["discount_factor"])
        dividend = _finite(
            "integrated_dividend_yield", raw["integrated_dividend_yield"]
        )
        if discount <= 0.0:
            raise ValueError("discount factor must be strictly positive")
        expiry_inputs[str(expiry)] = ExpiryInputs(discount, dividend)

    quotes: list[OptionQuote] = []
    option_ids: set[str] = set()
    for raw in data["quotes"]:
        strike = Decimal(str(raw["strike"]))
        bid = _finite("option bid", raw["bid"])
        ask = _finite("option ask", raw["ask"])
        multiplier = _finite(
            "contract multiplier", raw.get("contract_multiplier", raw.get("multiplier"))
        )
        if strike <= 0 or multiplier <= 0.0:
            raise ValueError("strike and contract multiplier must be positive")
        if bid < 0.0 or ask < bid:
            raise ValueError("option quotes require 0 <= bid <= ask")
        expiry = str(raw["expiry"])
        if expiry not in expiry_inputs:
            raise ValueError("every quote expiry requires public curve inputs")
        option_id = str(raw["option_id"])
        if not option_id or option_id in option_ids:
            raise ValueError("option_id must be nonempty and unique in a slice")
        option_ids.add(option_id)
        quote = OptionQuote(
            option_id=option_id,
            expiry=expiry,
            call_put=str(raw["call_put"]),
            strike=strike,
            bid=bid,
            ask=ask,
            multiplier=multiplier,
            underlying_id=str(raw.get("underlying_id", underlying_id)),
            valuation_date=str(raw.get("valuation_date", valuation_date)),
            currency=str(raw.get("currency", currency)),
            exercise_style=str(raw.get("exercise_style", "european")),
            settlement_type=str(raw.get("settlement_type", "cash")),
        )
        if quote.call_put not in {"call", "put"}:
            raise ValueError("call_put must be call or put")
        if quote.underlying_id != underlying_id:
            raise ValueError("quote underlying does not match the public slice")
        if quote.valuation_date != valuation_date:
            raise ValueError("quote valuation date does not match the public slice")
        if quote.currency != currency:
            raise ValueError("quote currency does not match the public slice")
        if date.fromisoformat(expiry) <= date.fromisoformat(valuation_date):
            raise ValueError("live option expiry must be after valuation date")
        quotes.append(quote)

    quotes.sort(
        key=lambda item: (
            item.expiry,
            item.strike,
            0 if item.call_put == "call" else 1,
            item.option_id,
        )
    )
    return PublicMarketSlice(
        snapshot_id=str(data["snapshot_id"]),
        snapshot_revision=int(data["snapshot_revision"]),
        valuation_date=valuation_date,
        underlying_id=underlying_id,
        spot=spot,
        currency=currency,
        quotes=tuple(quotes),
        expiry_inputs=expiry_inputs,
    )


def public_child_from_dict(data: Mapping[str, Any]) -> PublicChild:
    slices = tuple(market_slice_from_dict(item) for item in data["slices"])
    if not slices:
        raise ValueError("public child must contain at least one market slice")
    snapshot_id = str(data["snapshot_id"])
    revision = int(data["snapshot_revision"])
    if any(
        market_slice.snapshot_id != snapshot_id
        or market_slice.snapshot_revision != revision
        for market_slice in slices
    ):
        raise ValueError("slice snapshot identity must match the public child")
    slice_keys = {
        (market_slice.valuation_date, market_slice.underlying_id)
        for market_slice in slices
    }
    if len(slice_keys) != len(slices):
        raise ValueError("public child slice keys must be unique")
    return PublicChild(
        task_id=str(data["task_id"]),
        snapshot_id=snapshot_id,
        snapshot_revision=revision,
        status=str(data["status"]),
        slices=slices,
    )


def load_public_child(path: str | Path) -> PublicChild:
    """Load a frozen solver-visible JSON child without private lineage."""

    child_path = Path(path)
    if child_path.suffix.casefold() != ".json":
        raise ValueError("the v4 reference adapter accepts a JSON public child")
    return public_child_from_dict(json.loads(child_path.read_text(encoding="utf-8")))


def calendar_beta_12(
    integrated_dividend_yield_12: float,
    proportional_cost: float,
) -> float:
    return terminal_spot_bid(1.0, integrated_dividend_yield_12, proportional_cost)


def calendar_delta_0(
    multiplier: float,
    beta_12: float,
    exposure_buffer_ratio: float = CALENDAR_EXPOSURE_BUFFER_RATIO,
) -> float:
    return multiplier * (1.0 - beta_12 + exposure_buffer_ratio)


def calendar_delta_1(spot_at_t1: float, strike: float, multiplier: float) -> float:
    if spot_at_t1 < strike:
        return 0.0
    return -multiplier


def calendar_candidate_is_arbitrage(
    initial_surplus: float,
    *,
    terminal_nonnegative: bool = True,
    strict_gain_open_set: bool = True,
) -> bool:
    """Apply the stock-flip family's exact closed setup boundary."""

    if not isfinite(initial_surplus):
        raise ValueError("calendar surplus must be finite")
    if not terminal_nonnegative:
        return False
    return initial_surplus > 0.0 or (
        initial_surplus == 0.0 and strict_gain_open_set
    )


def calendar_t1_cash_unsimplified(
    *,
    spot_at_t1: float,
    strike: float,
    multiplier: float,
    delta_0: float,
    integrated_dividend_yield_12: float,
    proportional_cost: float,
) -> float:
    delta_1 = calendar_delta_1(spot_at_t1, strike, multiplier)
    early_call_payoff = -multiplier * max(spot_at_t1 - strike, 0.0)
    return (
        early_call_payoff
        + delta_0 * spot_at_t1
        - terminal_spot_outflow(
            delta_1,
            spot_at_t1,
            integrated_dividend_yield_12,
            proportional_cost,
        )
    )


def calendar_t1_cash_piecewise(
    *,
    spot_at_t1: float,
    strike: float,
    multiplier: float,
    beta_12: float,
    exposure_buffer_ratio: float = CALENDAR_EXPOSURE_BUFFER_RATIO,
) -> float:
    if spot_at_t1 < strike:
        return (
            multiplier
            * (1.0 - beta_12 + exposure_buffer_ratio)
            * spot_at_t1
        )
    return multiplier * strike + multiplier * exposure_buffer_ratio * spot_at_t1


def calendar_terminal_cash_unsimplified(
    *,
    spot_at_t1: float,
    spot_at_t2: float,
    strike: float,
    multiplier: float,
    delta_0: float,
    funding_factor_12: float,
    integrated_dividend_yield_12: float,
    proportional_cost: float,
) -> float:
    cash_t1 = calendar_t1_cash_unsimplified(
        spot_at_t1=spot_at_t1,
        strike=strike,
        multiplier=multiplier,
        delta_0=delta_0,
        integrated_dividend_yield_12=integrated_dividend_yield_12,
        proportional_cost=proportional_cost,
    )
    late_call_payoff = multiplier * max(spot_at_t2 - strike, 0.0)
    delta_1 = calendar_delta_1(spot_at_t1, strike, multiplier)
    return funding_factor_12 * cash_t1 + late_call_payoff + delta_1 * spot_at_t2


def calendar_terminal_cash_piecewise(
    *,
    spot_at_t1: float,
    spot_at_t2: float,
    strike: float,
    multiplier: float,
    beta_12: float,
    funding_factor_12: float,
    exposure_buffer_ratio: float = CALENDAR_EXPOSURE_BUFFER_RATIO,
) -> float:
    if spot_at_t1 < strike:
        return (
            funding_factor_12
            * multiplier
            * (1.0 - beta_12 + exposure_buffer_ratio)
            * spot_at_t1
            + multiplier * max(spot_at_t2 - strike, 0.0)
        )
    if spot_at_t2 < strike:
        return (
            funding_factor_12
            * (multiplier * strike + multiplier * exposure_buffer_ratio * spot_at_t1)
            - multiplier * spot_at_t2
        )
    return (
        multiplier * strike * (funding_factor_12 - 1.0)
        + funding_factor_12
        * multiplier
        * exposure_buffer_ratio
        * spot_at_t1
    )


def _candidate(
    *,
    candidate_id: str,
    family: str,
    template_id: str,
    surplus: float,
    boundary: str,
    terminal_nonnegative: bool,
    strict_gain: bool,
    details: Mapping[str, Any],
) -> CandidateEvidence:
    if not isfinite(surplus):
        raise ValueError("candidate surplus must be finite")
    active = terminal_nonnegative and (
        surplus > 0.0 or (surplus == 0.0 and strict_gain)
    )
    return CandidateEvidence(
        candidate_id=candidate_id,
        family=family,
        template_id=template_id,
        initial_surplus_usd=surplus,
        setup_boundary_kind=boundary,
        terminal_nonnegative=terminal_nonnegative,
        strict_gain_open_set=strict_gain,
        is_arbitrage=active,
        details=details,
    )


def _single_expiry_candidates(
    market: PublicMarketSlice,
    *,
    fee: float,
    proportional_cost: float,
) -> list[CandidateEvidence]:
    buckets: dict[tuple[str, float], dict[Decimal, dict[str, OptionQuote]]] = {}
    for quote in market.quotes:
        pair = buckets.setdefault((quote.expiry, quote.multiplier), {}).setdefault(
            quote.strike, {}
        )
        if quote.call_put in pair:
            raise ValueError("duplicate option quote in canonical bucket")
        pair[quote.call_put] = quote

    result: list[CandidateEvidence] = []
    for expiry, multiplier in sorted(buckets):
        context = market.expiry_inputs[expiry]
        pairs = buckets[(expiry, multiplier)]
        strikes = sorted(pairs)
        if any(set(pairs[strike]) != {"call", "put"} for strike in strikes):
            raise ValueError("each strike bucket requires exactly one call and put")
        for strike in strikes:
            call = pairs[strike]["call"]
            put = pairs[strike]["put"]
            call_ask = option_ask_amount(call.ask, multiplier, fee)
            call_bid = option_bid_amount(call.bid, multiplier, fee)
            put_ask = option_ask_amount(put.ask, multiplier, fee)
            put_bid = option_bid_amount(put.bid, multiplier, fee)
            spot_ask = multiplier * terminal_spot_ask(
                market.spot,
                context.integrated_dividend_yield,
                proportional_cost,
            )
            spot_bid = multiplier * terminal_spot_bid(
                market.spot,
                context.integrated_dividend_yield,
                proportional_cost,
            )
            bond = multiplier * float(strike) * context.discount_factor
            bound_names = ("call-upper", "call-lower", "put-upper", "put-lower")
            bound_values = discounted_price_bound_surpluses(
                call_ask_amount=call_ask,
                call_bid_amount=call_bid,
                put_ask_amount=put_ask,
                put_bid_amount=put_bid,
                terminal_spot_ask_amount=spot_ask,
                terminal_spot_bid_amount=spot_bid,
                strike_bond_amount=bond,
            )
            for name, surplus in zip(bound_names, bound_values):
                result.append(
                    _candidate(
                        candidate_id=(
                            f"cross-asset-bound|{market.underlying_id}|"
                            f"{market.valuation_date}|{expiry}|{strike}|{multiplier:g}|{name}"
                        ),
                        family="cross-asset",
                        template_id="discounted_price_bounds",
                        surplus=surplus,
                        boundary="closed",
                        terminal_nonnegative=True,
                        strict_gain=True,
                        details={"option_ids": [call.option_id, put.option_id]},
                    )
                )
            parity_values = executable_put_call_parity_surpluses(
                call_ask_amount=call_ask,
                call_bid_amount=call_bid,
                put_ask_amount=put_ask,
                put_bid_amount=put_bid,
                terminal_spot_ask_amount=spot_ask,
                terminal_spot_bid_amount=spot_bid,
                strike_bond_amount=bond,
            )
            for direction, surplus in zip(("long-call", "short-call"), parity_values):
                result.append(
                    _candidate(
                        candidate_id=(
                            f"cross-asset-parity|{market.underlying_id}|"
                            f"{market.valuation_date}|{expiry}|{strike}|{multiplier:g}|{direction}"
                        ),
                        family="cross-asset",
                        template_id="executable_put_call_parity",
                        surplus=surplus,
                        boundary="open",
                        terminal_nonnegative=True,
                        strict_gain=False,
                        details={"option_ids": [call.option_id, put.option_id]},
                    )
                )

        for lower_strike, upper_strike in combinations(strikes, 2):
            lower = pairs[lower_strike]
            upper = pairs[upper_strike]
            monotonic = (
                (
                    "call",
                    strike_monotonicity_surplus(
                        long_lower_or_higher_ask_amount=option_ask_amount(
                            lower["call"].ask, multiplier, fee
                        ),
                        short_higher_or_lower_bid_amount=option_bid_amount(
                            upper["call"].bid, multiplier, fee
                        ),
                    ),
                    (lower["call"].option_id, upper["call"].option_id),
                ),
                (
                    "put",
                    strike_monotonicity_surplus(
                        long_lower_or_higher_ask_amount=option_ask_amount(
                            upper["put"].ask, multiplier, fee
                        ),
                        short_higher_or_lower_bid_amount=option_bid_amount(
                            lower["put"].bid, multiplier, fee
                        ),
                    ),
                    (lower["put"].option_id, upper["put"].option_id),
                ),
            )
            for call_put, surplus, option_ids in monotonic:
                result.append(
                    _candidate(
                        candidate_id=(
                            f"cross-sectional-monotonicity|{market.underlying_id}|"
                            f"{market.valuation_date}|{expiry}|{call_put}|"
                            f"{lower_strike}|{upper_strike}|{multiplier:g}"
                        ),
                        family="cross-sectional",
                        template_id="strike_monotonicity",
                        surplus=surplus,
                        boundary="closed",
                        terminal_nonnegative=True,
                        strict_gain=True,
                        details={"option_ids": list(option_ids)},
                    )
                )

        for lower_strike, middle_strike, upper_strike in combinations(strikes, 3):
            positions = gcd_normalized_convexity_positions(
                lower_strike, middle_strike, upper_strike
            )
            for call_put in ("call", "put"):
                lower = pairs[lower_strike][call_put]
                middle = pairs[middle_strike][call_put]
                upper = pairs[upper_strike][call_put]
                surplus = nonuniform_convexity_surplus(
                    lower_ask_amount=option_ask_amount(lower.ask, multiplier, fee),
                    middle_bid_amount=option_bid_amount(middle.bid, multiplier, fee),
                    upper_ask_amount=option_ask_amount(upper.ask, multiplier, fee),
                    lower_position=positions[0],
                    middle_position=positions[1],
                    upper_position=positions[2],
                )
                result.append(
                    _candidate(
                        candidate_id=(
                            f"cross-sectional-convexity|{market.underlying_id}|"
                            f"{market.valuation_date}|{expiry}|{call_put}|{lower_strike}|"
                            f"{middle_strike}|{upper_strike}|{multiplier:g}"
                        ),
                        family="cross-sectional",
                        template_id="nonuniform_strike_convexity",
                        surplus=surplus,
                        boundary="closed",
                        terminal_nonnegative=True,
                        strict_gain=True,
                        details={
                            "option_ids": [
                                lower.option_id,
                                middle.option_id,
                                upper.option_id,
                            ],
                            "position_magnitudes": list(positions),
                        },
                    )
                )
    return result


def _calendar_candidates(
    market: PublicMarketSlice,
    *,
    fee: float,
    proportional_cost: float,
    exposure_buffer_ratio: float,
) -> list[CandidateEvidence]:
    calls: dict[tuple[str, Decimal, float], OptionQuote] = {}
    for quote in market.quotes:
        if quote.call_put != "call":
            continue
        if quote.exercise_style != "european" or quote.settlement_type != "cash":
            continue
        key = (quote.expiry, quote.strike, quote.multiplier)
        if key in calls:
            raise ValueError("duplicate call for calendar key")
        calls[key] = quote

    result: list[CandidateEvidence] = []
    expiries = sorted({key[0] for key in calls})
    for early_expiry, late_expiry in combinations(expiries, 2):
        early_context = market.expiry_inputs[early_expiry]
        late_context = market.expiry_inputs[late_expiry]
        funding_factor = (
            early_context.discount_factor / late_context.discount_factor
        )
        if funding_factor < 1.0:
            continue
        dividend_12 = (
            late_context.integrated_dividend_yield
            - early_context.integrated_dividend_yield
        )
        beta = calendar_beta_12(dividend_12, proportional_cost)
        if not (0.0 < beta <= 1.0):
            continue
        early_keys = sorted(
            (strike, multiplier)
            for expiry, strike, multiplier in calls
            if expiry == early_expiry
        )
        late_keys = {
            (strike, multiplier)
            for expiry, strike, multiplier in calls
            if expiry == late_expiry
        }
        for strike, multiplier in early_keys:
            if (strike, multiplier) not in late_keys:
                continue
            early = calls[(early_expiry, strike, multiplier)]
            late = calls[(late_expiry, strike, multiplier)]
            if (
                early.currency != late.currency
                or early.underlying_id != late.underlying_id
                or early.exercise_style != late.exercise_style
                or early.settlement_type != late.settlement_type
            ):
                continue
            delta_0 = calendar_delta_0(multiplier, beta, exposure_buffer_ratio)
            early_bid_amount = option_bid_amount(early.bid, multiplier, fee)
            late_ask_amount = option_ask_amount(late.ask, multiplier, fee)
            first_segment_outflow = terminal_spot_outflow(
                delta_0,
                market.spot,
                early_context.integrated_dividend_yield,
                proportional_cost,
            )
            surplus = early_bid_amount - late_ask_amount - first_segment_outflow
            strike_float = float(strike)
            # Replay the unsimplified T1 ledger in the frozen operation order.
            # The paid eta buffer makes the resulting binary64 coefficient
            # strictly positive without treating it as a comparison tolerance.
            low_x_slope = funding_factor * delta_0
            high_t1_x_coefficient = delta_0 - multiplier + multiplier * beta
            high_x_slope = funding_factor * high_t1_x_coefficient
            boundary_slack = (
                funding_factor
                * (
                    multiplier * strike_float
                    + high_t1_x_coefficient * strike_float
                )
                - multiplier * strike_float
            )
            terminal = {
                "beta_positive": beta > 0.0,
                "beta_at_most_one": beta <= 1.0,
                "funding_factor_at_least_one": funding_factor >= 1.0,
                "left_boundary_nonnegative": low_x_slope * strike_float >= 0.0,
                "actual_boundary_nonnegative": boundary_slack >= 0.0,
                "low_x_cell_nonnegative": low_x_slope >= 0.0,
                "high_x_low_y_cell_nonnegative": boundary_slack >= 0.0,
                "high_x_high_y_cell_nonnegative": boundary_slack >= 0.0,
                "strict_gain_open_set": boundary_slack > 0.0,
                "unsimplified_ledger_replayed": (
                    delta_0
                    == calendar_delta_0(
                        multiplier, beta, exposure_buffer_ratio
                    )
                    and high_t1_x_coefficient > 0.0
                ),
                "minimum_boundary_slack_usd": boundary_slack,
                "minimum_unbounded_ray_slope": min(low_x_slope, high_x_slope, 0.0),
            }
            terminal_nonnegative = all(
                bool(terminal[name])
                for name in (
                    "beta_positive",
                    "beta_at_most_one",
                    "funding_factor_at_least_one",
                    "left_boundary_nonnegative",
                    "actual_boundary_nonnegative",
                    "low_x_cell_nonnegative",
                    "high_x_low_y_cell_nonnegative",
                    "high_x_high_y_cell_nonnegative",
                    "unsimplified_ledger_replayed",
                )
            )
            strict_gain = bool(terminal["strict_gain_open_set"])
            candidate_id = (
                f"calendar-call-stock-flip-v1|{market.underlying_id}|"
                f"{market.valuation_date}|{early_expiry}|{late_expiry}|"
                f"{strike}|{multiplier:g}"
            )
            result.append(
                _candidate(
                    candidate_id=candidate_id,
                    family="calendar",
                    template_id=CALENDAR_FAMILY_ID,
                    surplus=surplus,
                    boundary="closed",
                    terminal_nonnegative=terminal_nonnegative,
                    strict_gain=strict_gain,
                    details={
                        "underlying_id": market.underlying_id,
                        "valuation_date": market.valuation_date,
                        "T1": early_expiry,
                        "T2": late_expiry,
                        "strike": strike_float,
                        "contract_multiplier": multiplier,
                        "early_option_id": early.option_id,
                        "late_option_id": late.option_id,
                        "early_position": -1,
                        "late_position": 1,
                        "early_bid_amount_after_fee": early_bid_amount,
                        "late_ask_amount_after_fee": late_ask_amount,
                        "first_segment_underlying_outflow": first_segment_outflow,
                        "beta_12": beta,
                        "funding_factor_12": funding_factor,
                        "exposure_buffer_ratio": exposure_buffer_ratio,
                        "delta_0": delta_0,
                        "delta_1_low": 0.0,
                        "delta_1_high": -multiplier,
                        "terminal_certificate": terminal,
                    },
                )
            )
    return result


def scan_market_slice(
    market: PublicMarketSlice,
    *,
    fee_per_contract_per_side: float = 0.50,
    proportional_cost: float = 0.0005,
    exposure_buffer_ratio: float = CALENDAR_EXPOSURE_BUFFER_RATIO,
) -> OracleResult:
    """Run the complete X/U/T catalogue in canonical family order."""

    fee = _finite("option fee", fee_per_contract_per_side)
    cost = _finite("underlying proportional cost", proportional_cost)
    eta = _finite("calendar exposure buffer ratio", exposure_buffer_ratio)
    if fee < 0.0 or not (0.0 <= cost < 1.0) or eta <= 0.0:
        raise ValueError("invalid public execution profile")
    spot = _finite("spot", market.spot)
    if spot <= 0.0:
        raise ValueError("spot must be strictly positive")
    for expiry, context in market.expiry_inputs.items():
        date.fromisoformat(expiry)
        discount = _finite("discount factor", context.discount_factor)
        _finite(
            "integrated dividend yield", context.integrated_dividend_yield
        )
        if discount <= 0.0:
            raise ValueError("discount factor must be strictly positive")
    for quote in market.quotes:
        bid = _finite("option bid", quote.bid)
        ask = _finite("option ask", quote.ask)
        multiplier = _finite("contract multiplier", quote.multiplier)
        if not quote.strike.is_finite():
            raise ValueError("strike must be finite")
        if bid < 0.0 or ask < bid:
            raise ValueError("option quotes require 0 <= bid <= ask")
        if quote.strike <= 0 or multiplier <= 0.0:
            raise ValueError("strike and multiplier must be positive")
        if quote.expiry not in market.expiry_inputs:
            raise ValueError("quote expiry requires public curve inputs")
    candidates = _single_expiry_candidates(market, fee=fee, proportional_cost=cost)
    candidates.extend(
        _calendar_candidates(
            market,
            fee=fee,
            proportional_cost=cost,
            exposure_buffer_ratio=eta,
        )
    )
    candidates.sort(key=_canonical_candidate_order)
    return OracleResult(tuple(candidates))


def scan_public_child(child: PublicChild) -> OracleResult:
    candidates: list[CandidateEvidence] = []
    for market_slice in sorted(
        child.slices,
        key=lambda item: (item.valuation_date, item.underlying_id),
    ):
        candidates.extend(scan_market_slice(market_slice).candidates)
    candidates.sort(key=_canonical_candidate_order)
    return OracleResult(tuple(candidates))


def continuous_curve_inputs(
    valuation_date: str,
    expiries: tuple[str, ...],
    *,
    risk_free_rate: float,
    dividend_yield: float,
) -> dict[str, ExpiryInputs]:
    """Construct the declared Actual/365 flat continuous curve inputs."""

    start = date.fromisoformat(valuation_date)
    result: dict[str, ExpiryInputs] = {}
    for expiry in expiries:
        years = (date.fromisoformat(expiry) - start).days / 365.0
        if years <= 0.0:
            raise ValueError("expiry must follow valuation date")
        result[expiry] = ExpiryInputs(
            discount_factor=exp(-risk_free_rate * years),
            integrated_dividend_yield=dividend_yield * years,
        )
    return result
