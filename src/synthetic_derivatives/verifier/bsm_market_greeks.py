"""Independent QuantLib verifier for market-implied unit BSM Greeks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
from typing import Any

from synthetic_derivatives.packaging.contracts import oracle_config
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
from synthetic_derivatives.verifier.bsm_greeks import trusted_quantlib_values


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


def _quantlib_price(
    task_input: NormalizedBSMImpliedVolatilityInput, sigma: float
) -> float:
    return trusted_quantlib_values(
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


def trusted_market_implied_root(task_input: BSMMarketGreeksInput) -> float:
    """Repeat the exact 80-step schedule with QuantLib price evaluations."""

    normalized = normalize_bsm_iv_input(task_input.as_iv_input())
    lower_bound, upper_bound = _price_bounds(normalized)
    if not lower_bound <= normalized.observed_price < upper_bound:
        raise ValueError("published market-Greeks row is outside BSM price bounds")
    low = DEFAULT_BSM_IV_CONTRACT.lower_volatility
    high = DEFAULT_BSM_IV_CONTRACT.upper_volatility
    if not _quantlib_price(normalized, low) <= normalized.observed_price <= (
        _quantlib_price(normalized, high)
    ):
        raise ValueError("published market-Greeks row has no root in the bracket")
    for _ in range(DEFAULT_BSM_IV_CONTRACT.iterations):
        midpoint = (low + high) / 2.0
        if _quantlib_price(normalized, midpoint) < normalized.observed_price:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def trusted_market_greeks_submission(
    task_inputs: Iterable[BSMMarketGreeksInput],
    method_config: Mapping[str, Any] | None = None,
) -> MarketGreeksSubmission:
    """Recompute the complete canonical answer from public rows only."""

    if method_config is not None and dict(method_config) != oracle_config():
        raise ValueError("trusted verifier received a different oracle config")
    rows = tuple(sorted(task_inputs, key=lambda item: item.canonical_order_key))
    if not rows or len({row.task_id for row in rows}) != 1:
        raise ValueError("trusted verifier requires one non-empty task")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, len(rows) + 1))
    if tuple(row.row_id for row in rows) != expected_ids:
        raise ValueError("trusted verifier input order is invalid")
    results = []
    for row in rows:
        sigma = trusted_market_implied_root(row)
        values = trusted_quantlib_values(row.as_greeks_input(sigma))
        results.append(canonical_market_greeks_row(row, sigma, values))
    return MarketGreeksSubmission(task_id=rows[0].task_id, rows=tuple(results))


def verify_market_greeks_submission(
    task_inputs: Iterable[BSMMarketGreeksInput],
    submission: Mapping[str, Any],
    method_config: Mapping[str, Any] | None = None,
) -> None:
    """Apply exact schema, row-order, identity, and value equality."""

    expected = trusted_market_greeks_submission(task_inputs, method_config)
    try:
        actual = MarketGreeksSubmission.from_mapping(submission)
    except (TypeError, ValueError) as error:
        raise ValueError("submission violates the market-Greeks schema") from error
    if actual.to_dict() != expected.to_dict():
        raise ValueError("market-Greeks canonical submission mismatch")


__all__ = [
    "trusted_market_greeks_submission",
    "trusted_market_implied_root",
    "verify_market_greeks_submission",
]
