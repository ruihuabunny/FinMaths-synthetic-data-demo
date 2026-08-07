"""Small, runtime-independent primitives from the blocked F2A v2 contract.

This module does not implement the F2A oracle or the calendar catalogue.  It
only centralizes the exact candidate predicate and already-reviewed
single-expiry cashflow formulas so their zero boundary can be unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp


@dataclass(frozen=True)
class CandidateCertificate:
    initial_surplus: float
    terminal_payoff_nonnegative_almost_surely: bool
    terminal_payoff_strictly_positive_with_positive_probability: bool


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
    return (
        middle_position * middle_bid_amount
        - lower_position * lower_ask_amount
        - upper_position * upper_ask_amount
    )
