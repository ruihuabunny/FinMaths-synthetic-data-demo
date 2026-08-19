"""Restricted-dependency analytic BSM price and spot Greeks.

All arithmetic is Python binary64 through the standard-library ``math``
primitives.  The pricing measure, units, day count, and publication checkpoint
are defined in :mod:`synthetic_derivatives.tasks.bsm_greeks`.
"""

from __future__ import annotations

import math
from typing import Iterable

from synthetic_derivatives.tasks.bsm_greeks import (
    BSMGreeksInput,
    BSMGreeksValues,
    CanonicalBSMGreeksResult,
    canonicalize_bsm_greeks,
)


_INVERSE_SQRT_TWO = 1.0 / math.sqrt(2.0)
_INVERSE_SQRT_TWO_PI = 1.0 / math.sqrt(2.0 * math.pi)


def _normal_cdf(value: float) -> float:
    return 0.5 * math.erfc(-value * _INVERSE_SQRT_TWO)


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) * _INVERSE_SQRT_TWO_PI


def bsm_analytic_values(task_input: BSMGreeksInput) -> BSMGreeksValues:
    """Evaluate one constant-parameter European BSM row.

    Vega is returned per 0.01 absolute volatility, theta per calendar day with
    expiry fixed, and rho per 0.01 absolute continuously compounded rate.
    """

    spot = task_input.spot
    strike = task_input.strike
    tau = task_input.time_to_expiry_actual365
    risk_free_rate = task_input.risk_free_rate
    dividend_yield = task_input.dividend_yield
    sigma = task_input.sigma

    sqrt_tau = math.sqrt(tau)
    variance = sigma * sigma
    root_variance = sigma * sqrt_tau
    carry = risk_free_rate - dividend_yield
    log_moneyness = math.log(spot / strike)
    standardized_drift = (carry + 0.5 * variance) * tau
    d1 = (log_moneyness + standardized_drift) / root_variance
    d2 = d1 - root_variance

    spot_discount = math.exp(-dividend_yield * tau)
    strike_discount = math.exp(-risk_free_rate * tau)
    discounted_spot = spot * spot_discount
    discounted_strike = strike * strike_discount
    density = _normal_pdf(d1)

    gamma = spot_discount * density / (spot * root_variance)
    vega_per_unit = discounted_spot * density * sqrt_tau
    diffusion_theta = -discounted_spot * density * sigma / (2.0 * sqrt_tau)

    if task_input.call_put == "call":
        cdf_d1 = _normal_cdf(d1)
        cdf_d2 = _normal_cdf(d2)
        price = discounted_spot * cdf_d1 - discounted_strike * cdf_d2
        delta = spot_discount * cdf_d1
        theta_per_year = (
            diffusion_theta
            - risk_free_rate * discounted_strike * cdf_d2
            + dividend_yield * discounted_spot * cdf_d1
        )
        rho_per_unit = strike * tau * strike_discount * cdf_d2
    else:
        cdf_minus_d1 = _normal_cdf(-d1)
        cdf_minus_d2 = _normal_cdf(-d2)
        price = (
            discounted_strike * cdf_minus_d2
            - discounted_spot * cdf_minus_d1
        )
        delta = -spot_discount * cdf_minus_d1
        theta_per_year = (
            diffusion_theta
            + risk_free_rate * discounted_strike * cdf_minus_d2
            - dividend_yield * discounted_spot * cdf_minus_d1
        )
        rho_per_unit = -strike * tau * strike_discount * cdf_minus_d2

    return BSMGreeksValues(
        price=float(price),
        delta=float(delta),
        gamma=float(gamma),
        vega=float(0.01 * vega_per_unit),
        theta=float(theta_per_year / 365.0),
        rho=float(0.01 * rho_per_unit),
    )


def solve_bsm_greeks(task_input: BSMGreeksInput) -> CanonicalBSMGreeksResult:
    """Solve and canonicalize one row."""

    return canonicalize_bsm_greeks(task_input, bsm_analytic_values(task_input))


def solve_bsm_greeks_batch(
    task_inputs: Iterable[BSMGreeksInput],
) -> tuple[CanonicalBSMGreeksResult, ...]:
    """Solve unique rows in the contract's canonical order."""

    ordered_inputs = sorted(task_inputs, key=lambda row: row.canonical_order_key)
    row_ids = [(row.task_id, row.option_id) for row in ordered_inputs]
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("task_id/option_id rows must be unique")
    return tuple(solve_bsm_greeks(row) for row in ordered_inputs)


__all__ = [
    "bsm_analytic_values",
    "solve_bsm_greeks",
    "solve_bsm_greeks_batch",
]
