"""Standard-library fixed-iteration BSM implied-volatility solver."""

from __future__ import annotations

import math

from synthetic_derivatives.solver.bsm import bsm_analytic_values
from synthetic_derivatives.tasks.bsm_greeks import BSMGreeksInput
from synthetic_derivatives.tasks.bsm_implied_volatility import (
    BSM_IV_INVALID_INPUT,
    BSM_IV_NO_BRACKET,
    BSM_IV_OK,
    BSM_IV_OUT_OF_BOUNDS,
    BSMImpliedVolatilityContract,
    BSMImpliedVolatilityInput,
    CanonicalBSMImpliedVolatilityResult,
    DEFAULT_BSM_IV_CONTRACT,
    NormalizedBSMImpliedVolatilityInput,
    canonicalize_bsm_iv_result,
    normalize_bsm_iv_input,
)


def _discounted_price_bounds(
    task_input: NormalizedBSMImpliedVolatilityInput,
) -> tuple[float, float]:
    tau = task_input.time_to_expiry_actual365
    discounted_spot = task_input.spot * math.exp(-task_input.dividend_yield * tau)
    discounted_strike = task_input.strike * math.exp(
        -task_input.risk_free_rate * tau
    )
    if task_input.call_put == "call":
        return (
            max(discounted_spot - discounted_strike, 0.0),
            discounted_spot,
        )
    return (
        max(discounted_strike - discounted_spot, 0.0),
        discounted_strike,
    )


def _bsm_price(
    task_input: NormalizedBSMImpliedVolatilityInput,
    volatility: float,
) -> float:
    pricing_input = BSMGreeksInput(
        task_id=task_input.task_id,
        valuation_date=task_input.valuation_date,
        underlying_id=task_input.underlying_id,
        option_id=task_input.option_id,
        call_put=task_input.call_put,
        spot=task_input.spot,
        strike=task_input.strike,
        expiry=task_input.expiry,
        time_to_expiry_actual365=task_input.time_to_expiry_actual365,
        risk_free_rate=task_input.risk_free_rate,
        dividend_yield=task_input.dividend_yield,
        sigma=volatility,
    )
    return bsm_analytic_values(pricing_input).price


def solve_bsm_implied_volatility(
    task_input: BSMImpliedVolatilityInput,
    contract: BSMImpliedVolatilityContract = DEFAULT_BSM_IV_CONTRACT,
) -> CanonicalBSMImpliedVolatilityResult:
    """Solve one canonical price inverse with the frozen 80 updates."""

    try:
        normalized = normalize_bsm_iv_input(task_input)
    except ValueError:
        return canonicalize_bsm_iv_result(
            task_input,
            status=BSM_IV_INVALID_INPUT,
        )

    observed = normalized.observed_price
    observed_decimal = normalized.observed_price_decimal
    try:
        lower_price_bound, upper_price_bound = _discounted_price_bounds(normalized)
    except (OverflowError, ValueError):
        return canonicalize_bsm_iv_result(
            task_input,
            status=BSM_IV_INVALID_INPUT,
        )

    if observed < lower_price_bound or observed >= upper_price_bound:
        return canonicalize_bsm_iv_result(
            task_input,
            status=BSM_IV_OUT_OF_BOUNDS,
            observed_price=observed_decimal,
        )

    low = contract.lower_volatility
    high = contract.upper_volatility
    try:
        price_low = _bsm_price(normalized, low)
        price_high = _bsm_price(normalized, high)
    except (OverflowError, ValueError):
        return canonicalize_bsm_iv_result(
            task_input,
            status=BSM_IV_INVALID_INPUT,
        )
    if (
        not price_low < price_high
        or observed < price_low
        or observed > price_high
    ):
        return canonicalize_bsm_iv_result(
            task_input,
            status=BSM_IV_NO_BRACKET,
            observed_price=observed_decimal,
        )

    for _ in range(contract.iterations):
        midpoint = (low + high) / 2.0
        price_midpoint = _bsm_price(normalized, midpoint)
        if price_midpoint < observed:
            low = midpoint
        else:
            high = midpoint
    implied_volatility = (low + high) / 2.0

    return canonicalize_bsm_iv_result(
        task_input,
        status=BSM_IV_OK,
        observed_price=observed_decimal,
        implied_volatility=implied_volatility,
        iterations=contract.iterations,
    )


__all__ = ["solve_bsm_implied_volatility"]
