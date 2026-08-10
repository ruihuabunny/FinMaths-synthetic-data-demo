from __future__ import annotations

from dataclasses import fields, replace
import math

import pytest

from synthetic_derivatives.solver import f2a_v5_stage2 as solver_stage2
from synthetic_derivatives.verifier import f2a_stage2 as verifier_stage2
from synthetic_derivatives.verifier.f2a_stage1 import (
    PhysicalFittingContract,
    UnderlyingFit,
)
from synthetic_derivatives.verifier.f2a_stage2 import (
    BSMInversionContract,
    LinkedDiffusionValidationContract,
    OptionObservation,
    OptionSeriesResult,
    Stage2RowResult,
    bsm_price,
    discounted_bsm_price_bounds,
    evaluate_stage2_row,
    invert_bsm_implied_volatility,
)


def _price(option_type: str, volatility: float = 0.2375) -> float:
    return bsm_price(
        option_type=option_type,
        spot=102.0,
        strike=100.0,
        remaining_years=0.75,
        integrated_rate=0.0225,
        integrated_dividend=0.0075,
        volatility=volatility,
    )


def _invert(option_type: str, price: float):
    return invert_bsm_implied_volatility(
        option_type=option_type,
        spot=102.0,
        strike=100.0,
        remaining_years=0.75,
        integrated_rate=0.0225,
        integrated_dividend=0.0075,
        observed_price=price,
    )


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_fixed_bisection_round_trip_is_canonical_for_call_and_put(
    option_type: str,
) -> None:
    result = _invert(option_type, _price(option_type))
    assert result.status == "converged"
    assert result.iterations == 80
    assert result.method_id == "bsm-bisection-float64-80-v1"
    assert result.implied_volatility == pytest.approx(0.2375, abs=1e-14)
    assert round(result.implied_volatility, 10) == 0.2375


def test_coherent_call_and_put_prices_recover_the_same_market_iv() -> None:
    call = _invert("call", _price("call"))
    put = _invert("put", _price("put"))
    assert call.implied_volatility == pytest.approx(put.implied_volatility, abs=1e-14)
    assert call.d1 == pytest.approx(put.d1, abs=1e-14)
    assert call.d2 == pytest.approx(put.d2, abs=1e-14)


def test_invalid_discounted_or_finite_bracket_returns_no_root() -> None:
    lower, upper = discounted_bsm_price_bounds(
        option_type="call",
        spot=102.0,
        strike=100.0,
        integrated_rate=0.0225,
        integrated_dividend=0.0075,
    )
    for observed in (lower - 0.01, upper + 0.01):
        result = _invert("call", observed)
        assert result.status == "invalid_bracket"
        assert result.implied_volatility is None
        assert result.d1 is None and result.d2 is None
        assert result.iterations == 0

    narrow = BSMInversionContract(lower_volatility=0.10, upper_volatility=0.11)
    result = invert_bsm_implied_volatility(
        option_type="call",
        spot=102.0,
        strike=100.0,
        remaining_years=0.75,
        integrated_rate=0.0225,
        integrated_dividend=0.0075,
        observed_price=_price("call"),
        contract=narrow,
    )
    assert result.status == "invalid_bracket"
    assert result.implied_volatility is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"method_id": "wrong"}, "method contract"),
        ({"iterations": 79}, "exactly 80"),
        ({"lower_volatility": 5.0, "upper_volatility": 1e-6}, "bracket"),
        ({"early_stop": True}, "forbids early stop"),
        ({"fallback_method": "newton"}, "forbids early stop"),
    ],
)
def test_inversion_contract_rejects_method_bracket_iteration_or_fallback_changes(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(BSMInversionContract(), **change)


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
        diffusion_information_matrix=tuple(
            tuple(0.0 for _ in range(3)) for _ in range(3)
        ),
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


def test_market_and_linked_d1_d2_are_derived_from_their_separate_volatilities() -> None:
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
        bid=4.9,
        ask=5.1,
        contract_multiplier=100.0,
        integrated_rate=0.01,
        integrated_dividend_or_carry=0.003,
    )
    result = evaluate_stage2_row(
        observation,
        PhysicalFittingContract(
            time_origin="2026-01-01",
            node_offsets_calendar_days=(0.0, 30.0, 60.0),
        ),
        _fit(),
        BSMInversionContract(),
        LinkedDiffusionValidationContract(),
    )
    assert result.market_implied_volatility is not None
    market_variance = (
        result.market_implied_volatility
        * result.market_implied_volatility
        * observation.remaining_years
    )
    expected_market_d1 = (
        math.log(observation.spot / observation.strike)
        + observation.integrated_rate
        - observation.integrated_dividend_or_carry
        + 0.5 * market_variance
    ) / math.sqrt(market_variance)
    expected_linked_d1 = (
        math.log(observation.spot / observation.strike)
        + observation.integrated_rate
        - observation.integrated_dividend_or_carry
        + 0.5 * result.linked_integrated_variance
    ) / math.sqrt(result.linked_integrated_variance)
    assert result.market_d1 == pytest.approx(expected_market_d1)
    assert result.market_d2 == pytest.approx(
        expected_market_d1 - math.sqrt(market_variance)
    )
    assert result.linked_d1 == pytest.approx(expected_linked_d1)
    assert result.linked_d2 == pytest.approx(
        expected_linked_d1 - math.sqrt(result.linked_integrated_variance)
    )


def test_stage2_contract_has_no_free_d1_d2_or_discounted_term_coefficients() -> None:
    names = {
        field.name for cls in (Stage2RowResult, OptionSeriesResult) for field in fields(cls)
    }
    assert not {
        "d1_coefficient",
        "d2_coefficient",
        "discounted_spot_coefficient",
        "discounted_strike_coefficient",
    } & names


def test_half_even_canonicalization_occurs_after_internal_binary64_work() -> None:
    observation = OptionObservation(
        row_id="row",
        option_contract_id="contract",
        option_id="option",
        underlying_id="U",
        valuation_date="2026-01-01",
        expiry="2026-02-01",
        option_type="call",
        strike=100.0,
        spot=100.0,
        bid=1.0,
        ask=1.0,
        contract_multiplier=100.0,
        integrated_rate=0.0,
        integrated_dividend_or_carry=0.0,
    )
    row = Stage2RowResult(
        observation=observation,
        market_iv_status="converged",
        market_implied_volatility=0.2,
        market_d1=0.1,
        market_d2=-0.1,
        linked_integrated_variance=0.04,
        linked_effective_volatility=0.2,
        linked_d1=0.1,
        linked_d2=-0.1,
        linked_counterfactual_price=1.23456789005,
        linked_counterfactual_price_se=0.01,
        price_gradient=(0.1, 0.2, 0.3),
        price_residual=1.23456789015,
        standardized_residual=0.0,
    )
    series = OptionSeriesResult(
        option_contract_id="contract",
        underlying_id="U",
        option_type="call",
        strike=100.0,
        expiry="2026-02-01",
        linked_diffusion_underlying_id="U",
        rows=(row,),
        validation_residual_sse=0.0,
        series_status="COMPLETE",
    ).to_dict()
    assert series["linked_counterfactual_prices_by_row_id"]["row"] == 1.23456789
    assert series["price_residuals_by_row_id"]["row"] == 1.2345678902


def test_solver_and_verifier_inversion_wrappers_match_exactly() -> None:
    arguments = dict(
        option_type="put",
        spot=98.0,
        strike=103.0,
        remaining_years=0.4,
        integrated_rate=0.012,
        integrated_dividend=0.004,
        observed_price=solver_stage2.bsm_price(
            option_type="put",
            spot=98.0,
            strike=103.0,
            remaining_years=0.4,
            integrated_rate=0.012,
            integrated_dividend=0.004,
            volatility=0.31,
        ),
    )
    solver_result = solver_stage2.invert_bsm_implied_volatility(**arguments)
    verifier_result = verifier_stage2.invert_bsm_implied_volatility(**arguments)
    assert solver_result.status == verifier_result.status
    assert solver_result.implied_volatility == verifier_result.implied_volatility
    assert solver_result.d1 == verifier_result.d1
    assert solver_result.d2 == verifier_result.d2
    assert solver_result.iterations == verifier_result.iterations == 80
