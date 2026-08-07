"""Runtime-independent cashflow primitives shared by F2A contract versions.

The immutable v1--v3 identities use a subset of these formulas.  The executable
v4 runtime also uses the terminal-spot segment primitives, while keeping its
family-bit decision independent in authoring, Solver, and trusted-verifier code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import combinations
from math import exp
from math import gcd


@dataclass(frozen=True)
class CandidateCertificate:
    initial_surplus: float
    terminal_payoff_nonnegative_almost_surely: bool
    terminal_payoff_strictly_positive_with_positive_probability: bool


@dataclass(frozen=True)
class OptionQuote:
    """One executable quote used by the runtime-independent contract scanner."""

    option_id: str
    expiry: str
    call_put: str
    strike: Decimal
    bid: float
    ask: float
    multiplier: float
    underlying_id: str = ""
    valuation_date: str = ""
    currency: str = "USD"
    exercise_style: str = "european"
    settlement_type: str = "cash"


@dataclass(frozen=True)
class ExpiryInputs:
    discount_factor: float
    integrated_dividend_yield: float


@dataclass(frozen=True)
class SingleExpiryScan:
    cross_sectional_surpluses: tuple[float, ...]
    cross_asset_nonconstant_surpluses: tuple[float, ...]
    cross_asset_zero_payoff_surpluses: tuple[float, ...]

    @property
    def cross_sectional(self) -> bool:
        return any(value >= 0.0 for value in self.cross_sectional_surpluses)

    @property
    def cross_asset(self) -> bool:
        return any(
            value >= 0.0 for value in self.cross_asset_nonconstant_surpluses
        ) or any(value > 0.0 for value in self.cross_asset_zero_payoff_surpluses)


def candidate_is_arbitrage(certificate: CandidateCertificate) -> bool:
    """Apply the exact candidate-specific predicate without a tolerance."""

    if not certificate.terminal_payoff_nonnegative_almost_surely:
        return False
    if certificate.initial_surplus > 0.0:
        return True
    return (
        certificate.initial_surplus == 0.0
        and certificate.terminal_payoff_strictly_positive_with_positive_probability
    )


def terminal_spot_ask_initial_shares(
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    return exp(-integrated_dividend_yield / (1.0 + proportional_cost)) / (
        1.0 - proportional_cost
    )


def terminal_spot_bid_initial_shares(
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    return exp(-integrated_dividend_yield / (1.0 - proportional_cost)) / (
        1.0 + proportional_cost
    )


def terminal_spot_ask(
    spot: float,
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    shares = terminal_spot_ask_initial_shares(
        integrated_dividend_yield,
        proportional_cost,
    )
    return spot * (1.0 + proportional_cost) * shares


def terminal_spot_bid(
    spot: float,
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    shares = terminal_spot_bid_initial_shares(
        integrated_dividend_yield,
        proportional_cost,
    )
    return spot * (1.0 - proportional_cost) * shares


def terminal_spot_outflow(
    exposure: float,
    spot: float,
    integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    """Initial cash outflow for a signed terminal spot exposure."""

    if exposure >= 0.0:
        return exposure * terminal_spot_ask(
            spot,
            integrated_dividend_yield,
            proportional_cost,
        )
    return exposure * terminal_spot_bid(
        spot,
        integrated_dividend_yield,
        proportional_cost,
    )


def long_share_count(
    initial_shares: float,
    elapsed_integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    """Shares after reinvesting dividends through the executable ask."""

    return initial_shares * exp(
        elapsed_integrated_dividend_yield / (1.0 + proportional_cost)
    )


def short_share_count(
    initial_shares: float,
    elapsed_integrated_dividend_yield: float,
    proportional_cost: float,
) -> float:
    """Short shares after financing dividends with executable short sales."""

    return initial_shares * exp(
        elapsed_integrated_dividend_yield / (1.0 - proportional_cost)
    )


def option_ask_amount(
    ask: float,
    multiplier: float,
    fee_per_contract_per_side: float,
) -> float:
    return multiplier * ask + fee_per_contract_per_side


def option_bid_amount(
    bid: float,
    multiplier: float,
    fee_per_contract_per_side: float,
) -> float:
    return multiplier * bid - fee_per_contract_per_side


def discounted_price_bound_surpluses(
    *,
    call_ask_amount: float,
    call_bid_amount: float,
    put_ask_amount: float,
    put_bid_amount: float,
    terminal_spot_ask_amount: float,
    terminal_spot_bid_amount: float,
    strike_bond_amount: float,
) -> tuple[float, float, float, float]:
    return (
        call_bid_amount - terminal_spot_ask_amount,
        terminal_spot_bid_amount - strike_bond_amount - call_ask_amount,
        put_bid_amount - strike_bond_amount,
        strike_bond_amount - put_ask_amount - terminal_spot_ask_amount,
    )


def executable_put_call_parity_surpluses(
    *,
    call_ask_amount: float,
    call_bid_amount: float,
    put_ask_amount: float,
    put_bid_amount: float,
    terminal_spot_ask_amount: float,
    terminal_spot_bid_amount: float,
    strike_bond_amount: float,
) -> tuple[float, float]:
    return (
        call_bid_amount
        - put_ask_amount
        - terminal_spot_ask_amount
        + strike_bond_amount,
        terminal_spot_bid_amount
        - strike_bond_amount
        - call_ask_amount
        + put_bid_amount,
    )


def strike_monotonicity_surplus(
    *,
    long_lower_or_higher_ask_amount: float,
    short_higher_or_lower_bid_amount: float,
) -> float:
    return short_higher_or_lower_bid_amount - long_lower_or_higher_ask_amount


def nonuniform_convexity_surplus(
    *,
    lower_ask_amount: float,
    middle_bid_amount: float,
    upper_ask_amount: float,
    lower_position: int,
    middle_position: int,
    upper_position: int,
) -> float:
    positions = (lower_position, middle_position, upper_position)
    if any(position <= 0 for position in positions):
        raise ValueError("convexity position magnitudes must be positive")
    if gcd(gcd(lower_position, middle_position), upper_position) != 1:
        raise ValueError("convexity positions must be gcd-normalized")
    return (
        middle_position * middle_bid_amount
        - lower_position * lower_ask_amount
        - upper_position * upper_ask_amount
    )


def gcd_normalized_convexity_positions(
    lower_strike: Decimal,
    middle_strike: Decimal,
    upper_strike: Decimal,
) -> tuple[int, int, int]:
    """Return the minimal integer magnitudes for a nonuniform butterfly."""

    if not lower_strike < middle_strike < upper_strike:
        raise ValueError("strikes must be strictly increasing")
    gaps = (
        upper_strike - middle_strike,
        upper_strike - lower_strike,
        middle_strike - lower_strike,
    )
    scale = max(-gap.as_tuple().exponent for gap in gaps)
    integers = tuple(int(gap * (Decimal(10) ** scale)) for gap in gaps)
    divisor = gcd(gcd(integers[0], integers[1]), integers[2])
    return tuple(value // divisor for value in integers)  # type: ignore[return-value]


def shift_single_option_quote(
    quotes: tuple[OptionQuote, ...],
    option_id: str,
    delta: float,
) -> tuple[OptionQuote, ...]:
    """Shift one logical quote point while preserving its half-spreads."""

    matches = [quote for quote in quotes if quote.option_id == option_id]
    if len(matches) != 1:
        raise ValueError("single-option target must match exactly one quote")
    return tuple(
        replace(quote, bid=quote.bid + delta, ask=quote.ask + delta)
        if quote.option_id == option_id
        else quote
        for quote in quotes
    )


def shift_call_put_pair(
    quotes: tuple[OptionQuote, ...],
    *,
    expiry: str,
    strike: Decimal,
    delta: float,
) -> tuple[OptionQuote, ...]:
    """Apply the atomic equal shift to one same-expiry, same-strike pair."""

    targets = [
        quote
        for quote in quotes
        if quote.expiry == expiry and quote.strike == strike
    ]
    if len(targets) != 2 or {quote.call_put for quote in targets} != {"call", "put"}:
        raise ValueError("grouped target must be exactly one call/put pair")
    if targets[0].multiplier != targets[1].multiplier:
        raise ValueError("grouped call/put multipliers must match")
    return tuple(
        replace(quote, bid=quote.bid + delta, ask=quote.ask + delta)
        if quote.expiry == expiry and quote.strike == strike
        else quote
        for quote in quotes
    )


def scan_single_expiry_families(
    *,
    quotes: tuple[OptionQuote, ...],
    spot: float,
    expiry_inputs: dict[str, ExpiryInputs],
    fee_per_contract_per_side: float,
    proportional_cost: float,
) -> SingleExpiryScan:
    """Enumerate the single-expiry catalogue with multiplier-safe buckets."""

    by_expiry_multiplier: dict[
        tuple[str, float], dict[Decimal, dict[str, OptionQuote]]
    ] = {}
    for quote in quotes:
        if quote.call_put not in {"call", "put"}:
            raise ValueError("call_put must be call or put")
        pair = by_expiry_multiplier.setdefault(
            (quote.expiry, quote.multiplier), {}
        ).setdefault(quote.strike, {})
        if quote.call_put in pair:
            raise ValueError(
                "duplicate quote for expiry/multiplier/strike/call_put"
            )
        pair[quote.call_put] = quote

    cross_sectional: list[float] = []
    cross_asset_nonconstant: list[float] = []
    cross_asset_zero: list[float] = []

    for expiry, bucket_multiplier in sorted(by_expiry_multiplier):
        context = expiry_inputs[expiry]
        strike_pairs = by_expiry_multiplier[(expiry, bucket_multiplier)]
        strikes = sorted(strike_pairs)
        if any(set(strike_pairs[strike]) != {"call", "put"} for strike in strikes):
            raise ValueError("every strike must contain one call and one put")

        for strike in strikes:
            call = strike_pairs[strike]["call"]
            put = strike_pairs[strike]["put"]
            if (
                call.multiplier != put.multiplier
                or call.multiplier != bucket_multiplier
            ):
                raise ValueError("call/put multipliers must match")
            multiplier = call.multiplier
            call_ask = option_ask_amount(
                call.ask, multiplier, fee_per_contract_per_side
            )
            call_bid = option_bid_amount(
                call.bid, multiplier, fee_per_contract_per_side
            )
            put_ask = option_ask_amount(
                put.ask, multiplier, fee_per_contract_per_side
            )
            put_bid = option_bid_amount(
                put.bid, multiplier, fee_per_contract_per_side
            )
            spot_ask = multiplier * terminal_spot_ask(
                spot,
                context.integrated_dividend_yield,
                proportional_cost,
            )
            spot_bid = multiplier * terminal_spot_bid(
                spot,
                context.integrated_dividend_yield,
                proportional_cost,
            )
            bond = multiplier * float(strike) * context.discount_factor
            cross_asset_nonconstant.extend(
                discounted_price_bound_surpluses(
                    call_ask_amount=call_ask,
                    call_bid_amount=call_bid,
                    put_ask_amount=put_ask,
                    put_bid_amount=put_bid,
                    terminal_spot_ask_amount=spot_ask,
                    terminal_spot_bid_amount=spot_bid,
                    strike_bond_amount=bond,
                )
            )
            cross_asset_zero.extend(
                executable_put_call_parity_surpluses(
                    call_ask_amount=call_ask,
                    call_bid_amount=call_bid,
                    put_ask_amount=put_ask,
                    put_bid_amount=put_bid,
                    terminal_spot_ask_amount=spot_ask,
                    terminal_spot_bid_amount=spot_bid,
                    strike_bond_amount=bond,
                )
            )

        for lower_strike, upper_strike in combinations(strikes, 2):
            lower = strike_pairs[lower_strike]
            upper = strike_pairs[upper_strike]
            cross_sectional.append(
                strike_monotonicity_surplus(
                    long_lower_or_higher_ask_amount=option_ask_amount(
                        lower["call"].ask,
                        lower["call"].multiplier,
                        fee_per_contract_per_side,
                    ),
                    short_higher_or_lower_bid_amount=option_bid_amount(
                        upper["call"].bid,
                        upper["call"].multiplier,
                        fee_per_contract_per_side,
                    ),
                )
            )
            cross_sectional.append(
                strike_monotonicity_surplus(
                    long_lower_or_higher_ask_amount=option_ask_amount(
                        upper["put"].ask,
                        upper["put"].multiplier,
                        fee_per_contract_per_side,
                    ),
                    short_higher_or_lower_bid_amount=option_bid_amount(
                        lower["put"].bid,
                        lower["put"].multiplier,
                        fee_per_contract_per_side,
                    ),
                )
            )

        for lower_strike, middle_strike, upper_strike in combinations(strikes, 3):
            lower_position, middle_position, upper_position = (
                gcd_normalized_convexity_positions(
                    lower_strike,
                    middle_strike,
                    upper_strike,
                )
            )
            for call_put in ("call", "put"):
                lower = strike_pairs[lower_strike][call_put]
                middle = strike_pairs[middle_strike][call_put]
                upper = strike_pairs[upper_strike][call_put]
                cross_sectional.append(
                    nonuniform_convexity_surplus(
                        lower_ask_amount=option_ask_amount(
                            lower.ask,
                            lower.multiplier,
                            fee_per_contract_per_side,
                        ),
                        middle_bid_amount=option_bid_amount(
                            middle.bid,
                            middle.multiplier,
                            fee_per_contract_per_side,
                        ),
                        upper_ask_amount=option_ask_amount(
                            upper.ask,
                            upper.multiplier,
                            fee_per_contract_per_side,
                        ),
                        lower_position=lower_position,
                        middle_position=middle_position,
                        upper_position=upper_position,
                    )
                )

    return SingleExpiryScan(
        cross_sectional_surpluses=tuple(cross_sectional),
        cross_asset_nonconstant_surpluses=tuple(cross_asset_nonconstant),
        cross_asset_zero_payoff_surpluses=tuple(cross_asset_zero),
    )
