"""Standard-library visible-midpoint IV and unit-Greeks composition."""

from __future__ import annotations

from collections.abc import Iterable
import math

from synthetic_derivatives.solver.analytic_and_implied_greeks_iv.bsm import (
    bsm_analytic_values,
)
from synthetic_derivatives.tasks.bsm_greeks import BSMGreeksInput
from synthetic_derivatives.tasks.bsm_implied_volatility import (
    DEFAULT_BSM_IV_CONTRACT,
    NormalizedBSMImpliedVolatilityInput,
    normalize_bsm_iv_input,
)
from synthetic_derivatives.tasks.bsm_market_greeks import (
    BSMMarketGreeksInput,
    MarketGreeksSubmission,
    canonical_market_greeks_row,
)


def _price_bounds(
    task_input: NormalizedBSMImpliedVolatilityInput,
) -> tuple[float, float]:
    tau = task_input.time_to_expiry_actual365
    discounted_spot = task_input.spot * math.exp(-task_input.dividend_yield * tau)
    discounted_strike = task_input.strike * math.exp(
        -task_input.risk_free_rate * tau
    )
    if task_input.call_put == "call":
        return max(discounted_spot - discounted_strike, 0.0), discounted_spot
    return max(discounted_strike - discounted_spot, 0.0), discounted_strike


def _price(
    task_input: NormalizedBSMImpliedVolatilityInput, sigma: float
) -> float:
    return bsm_analytic_values(
        BSMGreeksInput(
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
            sigma=sigma,
        )
    ).price


def solve_market_implied_root(task_input: BSMMarketGreeksInput) -> float:
    """Return the unrounded binary64 root after exactly 80 midpoint updates."""

    normalized = normalize_bsm_iv_input(task_input.as_iv_input())
    lower_bound, upper_bound = _price_bounds(normalized)
    if not lower_bound <= normalized.observed_price < upper_bound:
        raise ValueError("published market-Greeks row is outside BSM price bounds")
    low = DEFAULT_BSM_IV_CONTRACT.lower_volatility
    high = DEFAULT_BSM_IV_CONTRACT.upper_volatility
    if not _price(normalized, low) <= normalized.observed_price <= _price(
        normalized, high
    ):
        raise ValueError("published market-Greeks row has no root in the bracket")
    for _ in range(DEFAULT_BSM_IV_CONTRACT.iterations):
        midpoint = (low + high) / 2.0
        if _price(normalized, midpoint) < normalized.observed_price:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def solve_market_greeks_submission(
    task_inputs: Iterable[BSMMarketGreeksInput],
) -> MarketGreeksSubmission:
    """Solve a complete task in the immutable public row order."""

    rows = tuple(sorted(task_inputs, key=lambda item: item.canonical_order_key))
    if not rows:
        raise ValueError("market-Greeks task must contain rows")
    if len({row.task_id for row in rows}) != 1:
        raise ValueError("market-Greeks inputs must share one task ID")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, len(rows) + 1))
    if tuple(row.row_id for row in rows) != expected_ids:
        raise ValueError("market-Greeks row IDs do not encode canonical order")
    results = []
    for row in rows:
        sigma = solve_market_implied_root(row)
        values = bsm_analytic_values(row.as_greeks_input(sigma))
        results.append(canonical_market_greeks_row(row, sigma, values))
    return MarketGreeksSubmission(task_id=rows[0].task_id, rows=tuple(results))


__all__ = ["solve_market_greeks_submission", "solve_market_implied_root"]
