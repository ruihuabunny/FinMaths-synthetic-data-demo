from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from math import exp, nextafter
from pathlib import Path

import pytest

from synthetic_derivatives.authoring.f2a_child_materializer import (
    load_frozen_parent_fixture,
)
from synthetic_derivatives.verifier.f2a_contract import (
    ExpiryInputs,
    OptionQuote,
    scan_single_expiry_families,
    terminal_spot_ask,
    terminal_spot_bid,
    terminal_spot_outflow,
)
from synthetic_derivatives.verifier.f2a_oracle import (
    CALENDAR_EXPOSURE_BUFFER_RATIO,
    PublicMarketSlice,
    calendar_beta_12,
    calendar_candidate_is_arbitrage,
    calendar_delta_0,
    calendar_delta_1,
    calendar_t1_cash_piecewise,
    calendar_t1_cash_unsimplified,
    calendar_terminal_cash_piecewise,
    calendar_terminal_cash_unsimplified,
    scan_market_slice,
)


def _parent(repository_root: Path):
    return load_frozen_parent_fixture(
        repository_root / "tests/fixtures/f2a/v4_parent.json",
        repository_root / "tests/fixtures/f2a/v4_parent.manifest.json",
    )


def _calendar_candidate(market, *, strike=100.0, t1="2026-09-02", t2="2026-10-02"):
    result = scan_market_slice(market)
    return next(
        item
        for item in result.candidates
        if item.family == "calendar"
        and item.details["strike"] == strike
        and item.details["T1"] == t1
        and item.details["T2"] == t2
    )


def test_calendar_complete_chain_enumerates_exactly_42_candidates(
    repository_root: Path,
) -> None:
    market = _parent(repository_root).slices[0]
    result = scan_market_slice(market)
    assert result.realized_signature == "000"
    assert result.calendar_candidate_count == 42
    calendar = [item for item in result.candidates if item.family == "calendar"]
    ids = [item.candidate_id for item in calendar]
    order = [
        (item.details["T1"], item.details["T2"], item.details["strike"])
        for item in calendar
    ]
    assert order == sorted(order)
    assert len(ids) == len(set(ids))


def test_beta_is_terminal_bid_ratio_independent_of_spot() -> None:
    integrated_yield = 0.01 * 30 / 365
    cost = 0.0005
    beta = calendar_beta_12(integrated_yield, cost)
    for spot_at_t1 in (0.5, 1.0, 2.0, 128.0):
        assert terminal_spot_bid(spot_at_t1, integrated_yield, cost) / spot_at_t1 == beta
    assert 0.0 < beta <= 1.0


def test_delta_0_is_real_valued_and_charged_through_first_segment_ask(
    repository_root: Path,
) -> None:
    market = _parent(repository_root).slices[0]
    candidate = _calendar_candidate(market)
    details = candidate.details
    expected_delta = calendar_delta_0(
        details["contract_multiplier"],
        details["beta_12"],
        CALENDAR_EXPOSURE_BUFFER_RATIO,
    )
    assert details["delta_0"] == expected_delta
    assert expected_delta != round(expected_delta)
    expected_outflow = terminal_spot_outflow(
        expected_delta,
        market.spot,
        market.expiry_inputs[details["T1"]].integrated_dividend_yield,
        0.0005,
    )
    assert details["first_segment_underlying_outflow"] == expected_outflow
    assert expected_outflow == expected_delta * terminal_spot_ask(
        market.spot,
        market.expiry_inputs[details["T1"]].integrated_dividend_yield,
        0.0005,
    )


def test_stock_flip_boundary_uses_high_branch_at_strike() -> None:
    assert calendar_delta_1(nextafter(100.0, 0.0), 100.0, 100.0) == 0.0
    assert calendar_delta_1(100.0, 100.0, 100.0) == -100.0
    assert calendar_delta_1(101.0, 100.0, 100.0) == -100.0


@pytest.mark.parametrize("spot_at_t1", [25.0, nextafter(100.0, 0.0), 100.0, 175.0])
def test_unsimplified_t1_ledger_matches_piecewise_form(
    repository_root: Path,
    spot_at_t1: float,
) -> None:
    details = _calendar_candidate(_parent(repository_root).slices[0]).details
    unsimplified = calendar_t1_cash_unsimplified(
        spot_at_t1=spot_at_t1,
        strike=details["strike"],
        multiplier=details["contract_multiplier"],
        delta_0=details["delta_0"],
        integrated_dividend_yield_12=(
            _parent(repository_root)
            .slices[0]
            .expiry_inputs[details["T2"]]
            .integrated_dividend_yield
            - _parent(repository_root)
            .slices[0]
            .expiry_inputs[details["T1"]]
            .integrated_dividend_yield
        ),
        proportional_cost=0.0005,
    )
    simplified = calendar_t1_cash_piecewise(
        spot_at_t1=spot_at_t1,
        strike=details["strike"],
        multiplier=details["contract_multiplier"],
        beta_12=details["beta_12"],
    )
    assert unsimplified == pytest.approx(simplified, rel=1e-13, abs=1e-11)


@pytest.mark.parametrize(
    ("spot_at_t1", "spot_at_t2"),
    [
        (50.0, 50.0),
        (50.0, 100.0),
        (50.0, 200.0),
        (100.0, 50.0),
        (100.0, 100.0),
        (100.0, 200.0),
        (200.0, 50.0),
        (200.0, 100.0),
        (200.0, 200.0),
    ],
)
def test_unsimplified_terminal_ledger_matches_piecewise_certificate(
    repository_root: Path,
    spot_at_t1: float,
    spot_at_t2: float,
) -> None:
    market = _parent(repository_root).slices[0]
    details = _calendar_candidate(market).details
    dividend_12 = (
        market.expiry_inputs[details["T2"]].integrated_dividend_yield
        - market.expiry_inputs[details["T1"]].integrated_dividend_yield
    )
    unsimplified = calendar_terminal_cash_unsimplified(
        spot_at_t1=spot_at_t1,
        spot_at_t2=spot_at_t2,
        strike=details["strike"],
        multiplier=details["contract_multiplier"],
        delta_0=details["delta_0"],
        funding_factor_12=details["funding_factor_12"],
        integrated_dividend_yield_12=dividend_12,
        proportional_cost=0.0005,
    )
    simplified = calendar_terminal_cash_piecewise(
        spot_at_t1=spot_at_t1,
        spot_at_t2=spot_at_t2,
        strike=details["strike"],
        multiplier=details["contract_multiplier"],
        beta_12=details["beta_12"],
        funding_factor_12=details["funding_factor_12"],
    )
    assert unsimplified == pytest.approx(simplified, rel=1e-12, abs=1e-9)
    assert simplified >= 0.0


def test_calendar_boundary_y_k_rays_and_open_strict_gain_are_certified(
    repository_root: Path,
) -> None:
    details = _calendar_candidate(_parent(repository_root).slices[0]).details
    terminal = details["terminal_certificate"]
    assert terminal["left_boundary_nonnegative"] is True
    assert terminal["actual_boundary_nonnegative"] is True
    assert terminal["minimum_boundary_slack_usd"] > 0.0
    assert terminal["minimum_unbounded_ray_slope"] >= 0.0
    assert terminal["strict_gain_open_set"] is True
    left = calendar_terminal_cash_piecewise(
        spot_at_t1=nextafter(details["strike"], 0.0),
        spot_at_t2=details["strike"],
        strike=details["strike"],
        multiplier=details["contract_multiplier"],
        beta_12=details["beta_12"],
        funding_factor_12=details["funding_factor_12"],
    )
    actual = calendar_terminal_cash_piecewise(
        spot_at_t1=details["strike"],
        spot_at_t2=details["strike"],
        strike=details["strike"],
        multiplier=details["contract_multiplier"],
        beta_12=details["beta_12"],
        funding_factor_12=details["funding_factor_12"],
    )
    assert left >= 0.0
    assert actual > 0.0


def test_calendar_closed_boundary_accepts_zero_and_rejects_negative_surplus() -> None:
    assert calendar_candidate_is_arbitrage(0.0) is True
    assert calendar_candidate_is_arbitrage(-1e-300) is False
    assert calendar_candidate_is_arbitrage(1.0, strict_gain_open_set=False) is True
    assert calendar_candidate_is_arbitrage(0.0, strict_gain_open_set=False) is False
    assert calendar_candidate_is_arbitrage(1.0, terminal_nonnegative=False) is False
    with pytest.raises(ValueError, match="finite"):
        calendar_candidate_is_arbitrage(float("nan"))


def test_initial_surplus_charges_both_fees_and_underlying_cost_once(
    repository_root: Path,
) -> None:
    market = _parent(repository_root).slices[0]
    candidate = _calendar_candidate(market)
    details = candidate.details
    early = next(q for q in market.quotes if q.option_id == details["early_option_id"])
    late = next(q for q in market.quotes if q.option_id == details["late_option_id"])
    assert details["early_bid_amount_after_fee"] == early.multiplier * early.bid - 0.50
    assert details["late_ask_amount_after_fee"] == late.multiplier * late.ask + 0.50
    assert candidate.initial_surplus_usd == (
        details["early_bid_amount_after_fee"]
        - details["late_ask_amount_after_fee"]
        - details["first_segment_underlying_outflow"]
    )
    no_fees = candidate.initial_surplus_usd + 1.0
    double_charged_cost = (
        candidate.initial_surplus_usd - 0.0005 * details["first_segment_underlying_outflow"]
    )
    assert no_fees != candidate.initial_surplus_usd
    assert double_charged_cost != candidate.initial_surplus_usd


def test_raw_maturity_ordering_without_bridge_surplus_is_not_calendar_arbitrage(
    repository_root: Path,
) -> None:
    market = _parent(repository_root).slices[0]
    baseline = _calendar_candidate(market, strike=100.0)
    early_id = baseline.details["early_option_id"]
    late_id = baseline.details["late_option_id"]
    late = next(item for item in market.quotes if item.option_id == late_id)
    mutated_quotes = tuple(
        replace(item, bid=late.ask + 0.01, ask=late.ask + 0.02)
        if item.option_id == early_id
        else item
        for item in market.quotes
    )
    mutated = replace(market, quotes=mutated_quotes)
    candidate = _calendar_candidate(mutated, strike=100.0)
    early = next(item for item in mutated.quotes if item.option_id == early_id)
    assert early.bid > late.ask
    assert candidate.initial_surplus_usd < 0.0
    assert candidate.is_arbitrage is False


def test_small_model_quote_mismatch_does_not_create_executable_certificate(
    repository_root: Path,
) -> None:
    market = _parent(repository_root).slices[0]
    target_id = market.quotes[0].option_id
    mutated = replace(
        market,
        quotes=tuple(
            replace(item, bid=item.bid + 0.01, ask=item.ask + 0.01)
            if item.option_id == target_id
            else item
            for item in market.quotes
        ),
    )
    assert scan_market_slice(mutated).realized_signature == "000"


def test_calendar_skips_mismatched_multiplier_and_funding_factor_below_one(
    repository_root: Path,
) -> None:
    market = _parent(repository_root).slices[0]
    selected = _calendar_candidate(market)
    late_id = selected.details["late_option_id"]
    late = next(item for item in market.quotes if item.option_id == late_id)
    mismatch = replace(
        market,
        quotes=tuple(
            replace(item, multiplier=50.0)
            if item.expiry == late.expiry and item.strike == late.strike
            else item
            for item in market.quotes
        ),
    )
    # The changed October contract participates in three expiry pairs.
    assert scan_market_slice(mismatch).calendar_candidate_count == 39

    negative_rate_contexts = {
        expiry: ExpiryInputs(
            discount_factor=exp(0.03 * index),
            integrated_dividend_yield=context.integrated_dividend_yield,
        )
        for index, (expiry, context) in enumerate(sorted(market.expiry_inputs.items()))
    }
    negative_rate = replace(market, expiry_inputs=negative_rate_contexts)
    assert scan_market_slice(negative_rate).calendar_candidate_count == 0


def test_nonfinite_quote_is_rejected(repository_root: Path) -> None:
    market = _parent(repository_root).slices[0]
    bad_quote = replace(market.quotes[0], bid=float("inf"))
    with pytest.raises(ValueError, match="finite"):
        scan_market_slice(replace(market, quotes=(bad_quote, *market.quotes[1:])))


def test_frictionless_dividend_discount_is_not_terminal_spot_ask() -> None:
    spot = 100.0
    integrated_yield = 0.01
    executable = terminal_spot_ask(spot, integrated_yield, 0.0005)
    frictionless = spot * exp(-integrated_yield)
    assert executable != frictionless


def test_cross_strike_candidates_are_bucketed_by_common_multiplier() -> None:
    quotes = tuple(
        OptionQuote(
            option_id=f"{call_put}-{strike}-{multiplier:g}",
            expiry="2026-09-02",
            call_put=call_put,
            strike=Decimal(str(strike)),
            bid=bid,
            ask=ask,
            multiplier=multiplier,
        )
        for strike, multiplier, call_mid, put_mid in (
            (90, 100.0, 12.0, 2.0),
            (100, 100.0, 6.0, 5.0),
            (110, 50.0, 2.0, 11.0),
        )
        for call_put, bid, ask in (
            ("call", call_mid - 0.1, call_mid + 0.1),
            ("put", put_mid - 0.1, put_mid + 0.1),
        )
    )
    result = scan_single_expiry_families(
        quotes=quotes,
        spot=100.0,
        expiry_inputs={"2026-09-02": ExpiryInputs(0.99, 0.001)},
        fee_per_contract_per_side=0.50,
        proportional_cost=0.0005,
    )
    # Only the two M=100 strikes form a monotonicity pair; the lone M=50
    # strike is never combined with them and no three-strike butterfly exists.
    assert len(result.cross_sectional_surpluses) == 2
