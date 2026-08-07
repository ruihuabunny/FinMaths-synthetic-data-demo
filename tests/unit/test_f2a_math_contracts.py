from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from math import exp

import pytest

from synthetic_derivatives.verifier.f2a_contract import (
    CandidateCertificate,
    ExpiryInputs,
    OptionQuote,
    candidate_is_arbitrage,
    discounted_price_bound_surpluses,
    executable_put_call_parity_surpluses,
    gcd_normalized_convexity_positions,
    long_share_count,
    nonuniform_convexity_surplus,
    option_ask_amount,
    option_bid_amount,
    scan_single_expiry_families,
    shift_call_put_pair,
    shift_single_option_quote,
    short_share_count,
    strike_monotonicity_surplus,
    terminal_spot_ask,
    terminal_spot_ask_initial_shares,
    terminal_spot_bid,
    terminal_spot_bid_initial_shares,
    terminal_spot_outflow,
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


def test_complete_noncalendar_scanner_has_exact_spot_cross_sectional_invariant() -> None:
    quotes = _three_strike_chain()
    inputs = {"T": ExpiryInputs(0.98, 0.01)}
    before = scan_single_expiry_families(
        quotes=quotes,
        spot=100.0,
        expiry_inputs=inputs,
        fee_per_contract_per_side=0.5,
        proportional_cost=0.0005,
    )
    for mutated_spot in (0.01, 50.0, 100.0, 1_000_000.0):
        after = scan_single_expiry_families(
            quotes=quotes,
            spot=mutated_spot,
            expiry_inputs=inputs,
            fee_per_contract_per_side=0.5,
            proportional_cost=0.0005,
        )
        assert after.cross_sectional_surpluses == before.cross_sectional_surpluses


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


def _three_strike_chain() -> tuple[OptionQuote, ...]:
    rows = (
        ("c90", "call", "90", 11.8, 12.0),
        ("p90", "put", "90", 1.8, 2.0),
        ("c100", "call", "100", 5.8, 6.0),
        ("p100", "put", "100", 4.8, 5.0),
        ("c120", "call", "120", 0.8, 1.0),
        ("p120", "put", "120", 19.8, 20.0),
    )
    return tuple(
        OptionQuote(
            option_id=option_id,
            expiry="T",
            call_put=call_put,
            strike=Decimal(strike),
            bid=bid,
            ask=ask,
            multiplier=100.0,
        )
        for option_id, call_put, strike, bid, ask in rows
    )


def test_candidate_predicate_does_not_replace_g_with_total_wealth() -> None:
    discount_factor = 1.0
    surplus = 10.0
    terminal_certificate_payoff = -5.0
    total_wealth = surplus / discount_factor + terminal_certificate_payoff

    assert total_wealth >= 0.0
    assert candidate_is_arbitrage(
        CandidateCertificate(
            initial_surplus=surplus,
            terminal_payoff_nonnegative_almost_surely=False,
            terminal_payoff_strictly_positive_with_positive_probability=False,
        )
    ) is False


@pytest.mark.parametrize(
    ("surplus", "strict_gain", "expected"),
    [(-1.0, False, False), (0.0, False, False), (1.0, False, True)],
)
def test_identically_zero_parity_payoff_uses_open_setup_boundary(
    surplus: float,
    strict_gain: bool,
    expected: bool,
) -> None:
    assert candidate_is_arbitrage(
        CandidateCertificate(surplus, True, strict_gain)
    ) is expected


@pytest.mark.parametrize(
    ("surplus", "expected"),
    [(-1.0, False), (0.0, True), (1.0, True)],
)
def test_nonconstant_nonnegative_payoff_uses_closed_setup_boundary(
    surplus: float,
    expected: bool,
) -> None:
    assert candidate_is_arbitrage(
        CandidateCertificate(surplus, True, True)
    ) is expected


def test_terminal_spot_share_schedule_replays_arbitrary_yield_partition() -> None:
    proportional_cost = 0.0005
    partitions = (0.003, 0.007, 0.011, 0.009)
    total_yield = sum(partitions)

    long_shares = terminal_spot_ask_initial_shares(
        total_yield, proportional_cost
    )
    long_states = [long_shares]
    for interval_yield in partitions:
        next_shares = long_share_count(
            long_states[-1], interval_yield, proportional_cost
        )
        # At every instant dividend cash q*N*S*dt exactly buys dN shares
        # at S*(1+kappa); integration gives this interval ledger identity.
        financed_purchase_notional = (1.0 + proportional_cost) * (
            next_shares - long_states[-1]
        )
        integrated_dividend_notional = (
            (1.0 + proportional_cost)
            * long_states[-1]
            * (exp(interval_yield / (1.0 + proportional_cost)) - 1.0)
        )
        assert financed_purchase_notional == pytest.approx(
            integrated_dividend_notional
        )
        long_states.append(next_shares)
    assert long_states[-1] == pytest.approx(
        long_share_count(long_shares, total_yield, proportional_cost)
    )
    assert long_states[-1] * (1.0 - proportional_cost) == pytest.approx(1.0)

    short_shares = terminal_spot_bid_initial_shares(
        total_yield, proportional_cost
    )
    short_states = [short_shares]
    for interval_yield in partitions:
        next_shares = short_share_count(
            short_states[-1], interval_yield, proportional_cost
        )
        short_sale_proceeds_notional = (1.0 - proportional_cost) * (
            next_shares - short_states[-1]
        )
        financed_dividend_notional = (
            (1.0 - proportional_cost)
            * short_states[-1]
            * (exp(interval_yield / (1.0 - proportional_cost)) - 1.0)
        )
        assert short_sale_proceeds_notional == pytest.approx(
            financed_dividend_notional
        )
        short_states.append(next_shares)
    assert short_states[-1] == pytest.approx(
        short_share_count(short_shares, total_yield, proportional_cost)
    )
    assert short_states[-1] * (1.0 + proportional_cost) == pytest.approx(1.0)


def _affine_payoff(
    *,
    calls: tuple[tuple[Decimal, Decimal], ...] = (),
    puts: tuple[tuple[Decimal, Decimal], ...] = (),
    spot_position: Decimal = Decimal(0),
    cash: Decimal = Decimal(0),
    state: Decimal,
) -> Decimal:
    return (
        sum(weight * max(state - strike, Decimal(0)) for strike, weight in calls)
        + sum(weight * max(strike - state, Decimal(0)) for strike, weight in puts)
        + spot_position * state
        + cash
    )


def _assert_piecewise_affine_nonnegative(
    *,
    calls: tuple[tuple[Decimal, Decimal], ...] = (),
    puts: tuple[tuple[Decimal, Decimal], ...] = (),
    spot_position: Decimal = Decimal(0),
    cash: Decimal = Decimal(0),
) -> None:
    knots = sorted({strike for strike, _ in calls + puts})
    boundaries = [Decimal(0), *knots]
    for boundary in boundaries:
        assert _affine_payoff(
            calls=calls,
            puts=puts,
            spot_position=spot_position,
            cash=cash,
            state=boundary,
        ) >= 0

    for lower, upper in pairwise(boundaries):
        midpoint = (lower + upper) / 2
        lower_value = _affine_payoff(
            calls=calls,
            puts=puts,
            spot_position=spot_position,
            cash=cash,
            state=lower,
        )
        midpoint_value = _affine_payoff(
            calls=calls,
            puts=puts,
            spot_position=spot_position,
            cash=cash,
            state=midpoint,
        )
        upper_value = _affine_payoff(
            calls=calls,
            puts=puts,
            spot_position=spot_position,
            cash=cash,
            state=upper,
        )
        assert midpoint_value * 2 == lower_value + upper_value
        assert min(lower_value, upper_value) >= 0

    tail_start = boundaries[-1]
    tail_value = _affine_payoff(
        calls=calls,
        puts=puts,
        spot_position=spot_position,
        cash=cash,
        state=tail_start,
    )
    next_value = _affine_payoff(
        calls=calls,
        puts=puts,
        spot_position=spot_position,
        cash=cash,
        state=tail_start + 1,
    )
    assert tail_value >= 0
    assert next_value - tail_value >= 0


def test_single_expiry_payoffs_have_exhaustive_piecewise_affine_certificates() -> None:
    strike = Decimal(100)
    # Four discounted-bound terminal payoffs and both zero parity directions.
    _assert_piecewise_affine_nonnegative(
        calls=((strike, Decimal(-1)),), spot_position=Decimal(1)
    )
    _assert_piecewise_affine_nonnegative(
        calls=((strike, Decimal(1)),),
        spot_position=Decimal(-1),
        cash=strike,
    )
    _assert_piecewise_affine_nonnegative(
        puts=((strike, Decimal(-1)),), cash=strike
    )
    _assert_piecewise_affine_nonnegative(
        puts=((strike, Decimal(1)),),
        spot_position=Decimal(1),
        cash=-strike,
    )
    _assert_piecewise_affine_nonnegative(
        calls=((strike, Decimal(1)),),
        puts=((strike, Decimal(-1)),),
        spot_position=Decimal(-1),
        cash=strike,
    )
    _assert_piecewise_affine_nonnegative(
        calls=((strike, Decimal(-1)),),
        puts=((strike, Decimal(1)),),
        spot_position=Decimal(1),
        cash=-strike,
    )

    lower, middle, upper = Decimal(90), Decimal(110), Decimal(120)
    _assert_piecewise_affine_nonnegative(
        calls=((lower, Decimal(1)), (middle, Decimal(-1)))
    )
    _assert_piecewise_affine_nonnegative(
        puts=((lower, Decimal(-1)), (middle, Decimal(1)))
    )
    a, b, c = gcd_normalized_convexity_positions(lower, middle, upper)
    assert (a, b, c) == (1, 3, 2)
    _assert_piecewise_affine_nonnegative(
        calls=(
            (lower, Decimal(a)),
            (middle, Decimal(-b)),
            (upper, Decimal(c)),
        )
    )
    _assert_piecewise_affine_nonnegative(
        puts=(
            (lower, Decimal(a)),
            (middle, Decimal(-b)),
            (upper, Decimal(c)),
        )
    )


def test_convexity_positions_are_derived_and_scalar_duplicates_rejected() -> None:
    assert gcd_normalized_convexity_positions(
        Decimal("90.00"), Decimal("102.50"), Decimal("120.00")
    ) == (7, 12, 5)
    with pytest.raises(ValueError, match="gcd-normalized"):
        nonuniform_convexity_surplus(
            lower_ask_amount=4.0,
            middle_bid_amount=3.0,
            upper_ask_amount=1.0,
            lower_position=2,
            middle_position=6,
            upper_position=4,
        )


def test_single_quote_and_grouped_pair_have_declared_surplus_responses() -> None:
    quotes = _three_strike_chain()
    inputs = {"T": ExpiryInputs(0.98, 0.01)}
    kwargs = {
        "spot": 100.0,
        "expiry_inputs": inputs,
        "fee_per_contract_per_side": 0.5,
        "proportional_cost": 0.0005,
    }
    baseline = scan_single_expiry_families(quotes=quotes, **kwargs)

    single = shift_single_option_quote(quotes, "c100", 1.0)
    single_scan = scan_single_expiry_families(quotes=single, **kwargs)
    # Each strike contributes the two parity directions in order.
    assert single_scan.cross_asset_zero_payoff_surpluses[2] == pytest.approx(
        baseline.cross_asset_zero_payoff_surpluses[2] + 100.0
    )
    assert single_scan.cross_asset_zero_payoff_surpluses[3] == pytest.approx(
        baseline.cross_asset_zero_payoff_surpluses[3] - 100.0
    )

    grouped = shift_call_put_pair(
        quotes, expiry="T", strike=Decimal(100), delta=1.0
    )
    grouped_scan = scan_single_expiry_families(quotes=grouped, **kwargs)
    assert grouped_scan.cross_asset_zero_payoff_surpluses[2:4] == pytest.approx(
        baseline.cross_asset_zero_payoff_surpluses[2:4]
    )
    assert grouped_scan.cross_asset_nonconstant_surpluses[4:8] == pytest.approx(
        tuple(
            value + slope
            for value, slope in zip(
                baseline.cross_asset_nonconstant_surpluses[4:8],
                (100.0, -100.0, 100.0, -100.0),
            )
        )
    )
    changed_ids = {
        after.option_id
        for before, after in zip(quotes, grouped)
        if before != after
    }
    assert changed_ids == {"c100", "p100"}


def test_spot_surplus_slopes_match_terminal_spot_primitive() -> None:
    quotes = _three_strike_chain()
    integrated_yield = 0.01
    proportional_cost = 0.0005
    inputs = {"T": ExpiryInputs(0.98, integrated_yield)}
    kwargs = {
        "quotes": quotes,
        "expiry_inputs": inputs,
        "fee_per_contract_per_side": 0.5,
        "proportional_cost": proportional_cost,
    }
    before = scan_single_expiry_families(spot=100.0, **kwargs)
    after = scan_single_expiry_families(spot=101.0, **kwargs)
    ask_slope = terminal_spot_ask(1.0, integrated_yield, proportional_cost)
    bid_slope = terminal_spot_bid(1.0, integrated_yield, proportional_cost)

    assert after.cross_asset_nonconstant_surpluses[:4] == pytest.approx(
        tuple(
            value + slope
            for value, slope in zip(
                before.cross_asset_nonconstant_surpluses[:4],
                (-100.0 * ask_slope, 100.0 * bid_slope, 0.0, -100.0 * ask_slope),
            )
        )
    )
    assert after.cross_asset_zero_payoff_surpluses[:2] == pytest.approx(
        (
            before.cross_asset_zero_payoff_surpluses[0] - 100.0 * ask_slope,
            before.cross_asset_zero_payoff_surpluses[1] + 100.0 * bid_slope,
        )
    )

    exposure = -50.0
    before_calendar_surplus = -terminal_spot_outflow(
        exposure, 100.0, integrated_yield, proportional_cost
    )
    after_calendar_surplus = -terminal_spot_outflow(
        exposure, 101.0, integrated_yield, proportional_cost
    )
    phi_slope = exposure * bid_slope
    assert after_calendar_surplus - before_calendar_surplus == pytest.approx(
        -phi_slope
    )
