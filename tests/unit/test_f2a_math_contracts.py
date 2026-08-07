from __future__ import annotations

from math import exp

import pytest

from synthetic_derivatives.verifier.f2a_contract import (
    CandidateCertificate,
    candidate_is_arbitrage,
    discounted_price_bound_surpluses,
    executable_put_call_parity_surpluses,
    long_share_count,
    nonuniform_convexity_surplus,
    option_ask_amount,
    option_bid_amount,
    short_share_count,
    strike_monotonicity_surplus,
    terminal_spot_ask,
    terminal_spot_ask_initial_shares,
    terminal_spot_bid,
    terminal_spot_bid_initial_shares,
)


@pytest.mark.parametrize(
    ("surplus", "nonnegative", "strict_gain", "expected"),
    [
        (0.0, True, False, False),
        (0.0, True, True, True),
        (-1.0, True, True, False),
        (1.0, True, False, True),
        (1.0, False, True, False),
    ],
)
def test_candidate_predicate_has_candidate_specific_zero_boundary(
    surplus: float,
    nonnegative: bool,
    strict_gain: bool,
    expected: bool,
) -> None:
    certificate = CandidateCertificate(surplus, nonnegative, strict_gain)
    assert candidate_is_arbitrage(certificate) is expected


def test_terminal_spot_primitives_replay_each_cashflow() -> None:
    spot = 100.0
    integrated_dividend_yield = 0.03
    proportional_cost = 0.0005

    long_initial = terminal_spot_ask_initial_shares(
        integrated_dividend_yield,
        proportional_cost,
    )
    long_terminal = long_share_count(
        long_initial,
        integrated_dividend_yield,
        proportional_cost,
    )
    assert long_terminal == pytest.approx(1.0 / (1.0 - proportional_cost))
    assert long_terminal * spot * (1.0 - proportional_cost) == pytest.approx(spot)
    assert terminal_spot_ask(
        spot,
        integrated_dividend_yield,
        proportional_cost,
    ) == pytest.approx(long_initial * spot * (1.0 + proportional_cost))

    short_initial = terminal_spot_bid_initial_shares(
        integrated_dividend_yield,
        proportional_cost,
    )
    short_terminal = short_share_count(
        short_initial,
        integrated_dividend_yield,
        proportional_cost,
    )
    assert short_terminal == pytest.approx(1.0 / (1.0 + proportional_cost))
    assert short_terminal * spot * (1.0 + proportional_cost) == pytest.approx(spot)
    assert terminal_spot_bid(
        spot,
        integrated_dividend_yield,
        proportional_cost,
    ) == pytest.approx(short_initial * spot * (1.0 - proportional_cost))

    half_long = long_share_count(
        long_initial,
        integrated_dividend_yield / 2.0,
        proportional_cost,
    )
    assert half_long * exp(
        (integrated_dividend_yield / 2.0) / (1.0 + proportional_cost)
    ) == pytest.approx(long_terminal)


def test_single_expiry_formulas_charge_directional_sides_and_each_fee() -> None:
    multiplier = 100.0
    fee = 0.50
    call_ask = option_ask_amount(5.10, multiplier, fee)
    call_bid = option_bid_amount(4.90, multiplier, fee)
    put_ask = option_ask_amount(3.10, multiplier, fee)
    put_bid = option_bid_amount(2.90, multiplier, fee)
    spot_ask = 10_100.0
    spot_bid = 9_900.0
    bond = 10_000.0

    assert discounted_price_bound_surpluses(
        call_ask_amount=call_ask,
        call_bid_amount=call_bid,
        put_ask_amount=put_ask,
        put_bid_amount=put_bid,
        terminal_spot_ask_amount=spot_ask,
        terminal_spot_bid_amount=spot_bid,
        strike_bond_amount=bond,
    ) == (
        call_bid - spot_ask,
        spot_bid - bond - call_ask,
        put_bid - bond,
        bond - put_ask - spot_ask,
    )
    assert executable_put_call_parity_surpluses(
        call_ask_amount=call_ask,
        call_bid_amount=call_bid,
        put_ask_amount=put_ask,
        put_bid_amount=put_bid,
        terminal_spot_ask_amount=spot_ask,
        terminal_spot_bid_amount=spot_bid,
        strike_bond_amount=bond,
    ) == (
        call_bid - put_ask - spot_ask + bond,
        spot_bid - bond - call_ask + put_bid,
    )


def test_cross_sectional_formulas_do_not_consume_spot() -> None:
    option_amounts = {
        "lower_ask": 401.0,
        "middle_bid": 298.0,
        "upper_ask": 101.0,
        "higher_bid": 199.0,
    }

    def scan_cross_sectional(_spot_close: float) -> tuple[float, float]:
        monotonicity = strike_monotonicity_surplus(
            long_lower_or_higher_ask_amount=option_amounts["lower_ask"],
            short_higher_or_lower_bid_amount=option_amounts["higher_bid"],
        )
        convexity = nonuniform_convexity_surplus(
            lower_ask_amount=option_amounts["lower_ask"],
            middle_bid_amount=option_amounts["middle_bid"],
            upper_ask_amount=option_amounts["upper_ask"],
            lower_position=1,
            middle_position=2,
            upper_position=1,
        )
        return monotonicity, convexity

    before = scan_cross_sectional(100.0)
    for mutated_spot in (0.01, 50.0, 100.0, 1_000_000.0):
        assert scan_cross_sectional(mutated_spot) == before


def test_nonuniform_convexity_uses_gcd_normalized_integer_positions() -> None:
    # Strike gaps 10, 30, 20 reduce from 10:30:20 to 1:3:2.
    assert nonuniform_convexity_surplus(
        lower_ask_amount=4.0,
        middle_bid_amount=3.0,
        upper_ask_amount=1.0,
        lower_position=1,
        middle_position=3,
        upper_position=2,
    ) == 3.0


@pytest.mark.parametrize("terminal_spot", [1.0, 50.0, 100.0, 150.0, 300.0])
def test_single_expiry_candidate_payoffs_are_statewise_nonnegative(
    terminal_spot: float,
) -> None:
    strike = 100.0
    call = max(terminal_spot - strike, 0.0)
    put = max(strike - terminal_spot, 0.0)

    bound_payoffs = (
        -call + terminal_spot,
        call - terminal_spot + strike,
        -put + strike,
        put + terminal_spot - strike,
    )
    assert bound_payoffs == (
        min(terminal_spot, strike),
        max(strike - terminal_spot, 0.0),
        min(terminal_spot, strike),
        max(terminal_spot - strike, 0.0),
    )
    assert all(payoff >= 0.0 for payoff in bound_payoffs)

    assert call - put - terminal_spot + strike == 0.0
    assert terminal_spot - strike - call + put == 0.0

    lower_strike = 90.0
    middle_strike = 110.0
    upper_strike = 120.0
    call_payoffs = (
        max(terminal_spot - lower_strike, 0.0),
        max(terminal_spot - middle_strike, 0.0),
        max(terminal_spot - upper_strike, 0.0),
    )
    put_payoffs = (
        max(lower_strike - terminal_spot, 0.0),
        max(middle_strike - terminal_spot, 0.0),
        max(upper_strike - terminal_spot, 0.0),
    )
    assert call_payoffs[0] - call_payoffs[1] >= 0.0
    assert put_payoffs[1] - put_payoffs[0] >= 0.0

    # Gaps 10:30:20 reduce to positions +1:-3:+2.
    assert call_payoffs[0] - 3.0 * call_payoffs[1] + 2.0 * call_payoffs[2] >= 0.0
    assert put_payoffs[0] - 3.0 * put_payoffs[1] + 2.0 * put_payoffs[2] >= 0.0
