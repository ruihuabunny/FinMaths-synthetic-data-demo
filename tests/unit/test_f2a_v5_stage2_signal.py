from __future__ import annotations

from dataclasses import replace
import math

import pytest

from synthetic_derivatives.verifier.f2a_model_signal import (
    ModelSignalContract,
    scan_model_signal_slice,
)
from synthetic_derivatives.verifier.f2a_oracle import market_slice_from_dict
from synthetic_derivatives.verifier.f2a_stage1 import (
    PhysicalFittingContract,
    UnderlyingFit,
)
from synthetic_derivatives.verifier.f2a_stage2 import (
    BSMInversionContract,
    LinkedDiffusionValidationContract,
    OptionObservation,
    bsm_price,
    bsm_price_from_integrated_variance,
    evaluate_stage2_row,
    stable_row_id,
)


def _fit() -> UnderlyingFit:
    covariance = (
        (0.0004, 0.0002, 0.0),
        (0.0002, 0.0005, 0.0001),
        (0.0, 0.0001, 0.0006),
    )
    return UnderlyingFit(
        underlying_id="U",
        node_offsets_calendar_days=(0.0, 30.0, 60.0),
        fitted_drift_node_values=(0.05, 0.05, 0.05),
        fitted_diffusion_node_values=(0.20, 0.24, 0.22),
        observed_hessian=tuple(tuple(0.0 for _ in range(6)) for _ in range(6)),
        diffusion_information_matrix=tuple(tuple(0.0 for _ in range(3)) for _ in range(3)),
        diffusion_covariance_matrix=covariance,
        diffusion_rse_by_node=(0.1, 0.1, 0.1),
        information_effective_sample_size_by_node=(50.0, 50.0, 50.0),
        objective_value=0.0,
        usable_return_count=64,
        hessian_condition_number=2.0,
        drift_active_bound_node_indices=(),
        solver_status="CONVERGED",
        solver_evaluations=100,
    )


def test_bsm_call_put_terms_obey_discounted_parity() -> None:
    call, d1, d2 = bsm_price_from_integrated_variance(
        option_type="call",
        spot=100.0,
        strike=105.0,
        integrated_rate=0.03,
        integrated_dividend=0.01,
        integrated_variance=0.04,
    )
    put, put_d1, put_d2 = bsm_price_from_integrated_variance(
        option_type="put",
        spot=100.0,
        strike=105.0,
        integrated_rate=0.03,
        integrated_dividend=0.01,
        integrated_variance=0.04,
    )
    assert (put_d1, put_d2) == (d1, d2)
    assert call - put == pytest.approx(
        100.0 * math.exp(-0.01) - 105.0 * math.exp(-0.03)
    )


def test_linked_price_gradient_matches_finite_difference() -> None:
    physical = PhysicalFittingContract(
        time_origin="2026-01-01",
        node_offsets_calendar_days=(0.0, 30.0, 60.0),
    )
    observation = OptionObservation(
        row_id="row",
        option_contract_id="contract",
        option_id="option",
        underlying_id="U",
        valuation_date="2026-01-15",
        expiry="2026-03-15",
        option_type="call",
        strike=100.0,
        spot=101.0,
        bid=5.0,
        ask=5.2,
        contract_multiplier=100.0,
        integrated_rate=0.01,
        integrated_dividend_or_carry=0.003,
    )
    fit = _fit()
    row = evaluate_stage2_row(
        observation,
        physical,
        fit,
        BSMInversionContract(),
        LinkedDiffusionValidationContract(),
    )
    assert row.standardized_residual == pytest.approx(
        row.price_residual / math.hypot(0.05, row.linked_counterfactual_price_se)
    )
    epsilon = 1e-6
    for index, gradient in enumerate(row.price_gradient):
        up = list(fit.fitted_diffusion_node_values)
        down = list(fit.fitted_diffusion_node_values)
        up[index] += epsilon
        down[index] -= epsilon
        up_price = evaluate_stage2_row(
            observation,
            physical,
            replace(fit, fitted_diffusion_node_values=tuple(up)),
            BSMInversionContract(),
            LinkedDiffusionValidationContract(),
        ).linked_counterfactual_price
        down_price = evaluate_stage2_row(
            observation,
            physical,
            replace(fit, fitted_diffusion_node_values=tuple(down)),
            BSMInversionContract(),
            LinkedDiffusionValidationContract(),
        ).linked_counterfactual_price
        assert gradient == pytest.approx((up_price - down_price) / (2 * epsilon), rel=1e-6)


def test_model_signal_requires_post_cost_candidate_edge() -> None:
    market = market_slice_from_dict(
        {
            "snapshot_id": "S",
            "snapshot_revision": 1,
            "valuation_date": "2026-01-15",
            "underlying_id": "U",
            "spot": 100.0,
            "currency": "USD",
            "expiry_inputs": {
                "2026-02-15": {
                    "discount_factor": math.exp(-0.01),
                    "integrated_dividend_yield": 0.003,
                }
            },
            "quotes": [
                {
                    "option_id": f"{kind}-{strike}",
                    "expiry": "2026-02-15",
                    "call_put": kind,
                    "strike": strike,
                    "bid": 4.9,
                    "ask": 5.1,
                    "contract_multiplier": 100.0,
                    "underlying_id": "U",
                    "valuation_date": "2026-01-15",
                }
                for strike in (95.0, 100.0, 105.0)
                for kind in ("call", "put")
            ],
        }
    )
    fit = _fit()
    rows = {}
    for quote in market.quotes:
        row_id = stable_row_id("U", market.valuation_date, quote.option_id)
        observation = OptionObservation(
            row_id=row_id,
            option_contract_id=quote.option_id,
            option_id=quote.option_id,
            underlying_id="U",
            valuation_date=market.valuation_date,
            expiry=quote.expiry,
            option_type=quote.call_put,
            strike=float(quote.strike),
            spot=market.spot,
            bid=quote.bid,
            ask=quote.ask,
            contract_multiplier=quote.multiplier,
            integrated_rate=-math.log(market.expiry_inputs[quote.expiry].discount_factor),
            integrated_dividend_or_carry=market.expiry_inputs[quote.expiry].integrated_dividend_yield,
        )
        base = evaluate_stage2_row(
            observation,
            PhysicalFittingContract(
                time_origin="2026-01-01",
                node_offsets_calendar_days=(0.0, 30.0, 60.0),
            ),
            fit,
            BSMInversionContract(),
            LinkedDiffusionValidationContract(),
        )
        # Zero model residual: spreads and fees alone can never activate a bit.
        midpoint = base.linked_counterfactual_price
        consistent_observation = replace(
            observation,
            bid=max(0.0, midpoint - 0.1),
            ask=midpoint + 0.1,
        )
        rows[row_id] = replace(
            base,
            observation=consistent_observation,
            price_residual=0.0,
            standardized_residual=0.0,
        )
    clean = scan_model_signal_slice(market, rows, fit, ModelSignalContract())
    assert clean.signature == "000"
    excluded = {
        row_id: replace(
            row,
            market_iv_status="invalid_bracket",
            market_implied_volatility=None,
            market_d1=None,
            market_d2=None,
        )
        for row_id, row in rows.items()
    }
    excluded_result = scan_model_signal_slice(
        market, excluded, fit, ModelSignalContract()
    )
    assert excluded_result.signature == "000"
    assert excluded_result.candidates == ()
    mutated_id = stable_row_id("U", market.valuation_date, "call-100.0")
    mutated = dict(rows)
    target = mutated[mutated_id]
    shifted_observation = replace(
        target.observation,
        bid=target.observation.bid + 2.0,
        ask=target.observation.ask + 2.0,
    )
    mutated[mutated_id] = replace(
        target,
        observation=shifted_observation,
        price_residual=2.0,
        standardized_residual=40.0,
    )
    result = scan_model_signal_slice(market, mutated, fit, ModelSignalContract())
    assert result.signature != "000"
    assert all(item.net_signal_edge > 0.0 for item in result.active_candidates)
    assert all(item.option_fees > 0.0 for item in result.active_candidates)


def test_market_iv_repricing_never_replaces_the_linked_counterfactual() -> None:
    physical = PhysicalFittingContract(
        time_origin="2026-01-01",
        node_offsets_calendar_days=(0.0, 30.0, 60.0),
    )
    common = dict(
        row_id="row",
        option_contract_id="contract",
        option_id="option",
        underlying_id="U",
        valuation_date="2026-01-15",
        expiry="2026-03-15",
        option_type="call",
        strike=100.0,
        spot=100.0,
        contract_multiplier=100.0,
        integrated_rate=0.01,
        integrated_dividend_or_carry=0.003,
    )
    low_quote = OptionObservation(**common, bid=3.9, ask=4.1)
    high_quote = OptionObservation(**common, bid=5.9, ask=6.1)
    low = evaluate_stage2_row(
        low_quote,
        physical,
        _fit(),
        BSMInversionContract(),
        LinkedDiffusionValidationContract(),
    )
    high = evaluate_stage2_row(
        high_quote,
        physical,
        _fit(),
        BSMInversionContract(),
        LinkedDiffusionValidationContract(),
    )
    assert low.market_implied_volatility != high.market_implied_volatility
    assert low.linked_counterfactual_price == high.linked_counterfactual_price
    assert low.linked_integrated_variance == high.linked_integrated_variance
    assert high.market_implied_volatility is not None
    market_reprice = bsm_price(
        option_type="call",
        spot=high_quote.spot,
        strike=high_quote.strike,
        remaining_years=high_quote.remaining_years,
        integrated_rate=high_quote.integrated_rate,
        integrated_dividend=high_quote.integrated_dividend_or_carry,
        volatility=high.market_implied_volatility,
    )
    assert market_reprice == pytest.approx(high_quote.observed_price)
    assert high.price_residual == pytest.approx(
        high_quote.observed_price - high.linked_counterfactual_price
    )
    assert high.price_residual != pytest.approx(0.0)
