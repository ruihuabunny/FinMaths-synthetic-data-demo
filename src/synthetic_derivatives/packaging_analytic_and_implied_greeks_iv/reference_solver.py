"""Standalone standard-library implementation executed through trusted tools."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
import math


_INV_SQRT_TWO = 1.0 / math.sqrt(2.0)
_INV_SQRT_TWO_PI = 1.0 / math.sqrt(2.0 * math.pi)
_QUANTUM = Decimal("0.00000001")


def _cdf(value):
    return 0.5 * math.erfc(-value * _INV_SQRT_TWO)


def _pdf(value):
    return math.exp(-0.5 * value * value) * _INV_SQRT_TWO_PI


def _canonical(value):
    result = Decimal(str(value)).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    if result == 0:
        result = Decimal("0").quantize(_QUANTUM)
    return format(result, "f")


def _terms(row, sigma):
    spot = float(row["spot"])
    strike = float(row["strike"])
    tau = float(row["time_to_expiry_actual365"])
    rate = float(row["risk_free_rate"])
    dividend = float(row["dividend_yield"])
    sqrt_tau = math.sqrt(tau)
    variance = sigma * sigma
    root_variance = sigma * sqrt_tau
    carry = rate - dividend
    log_moneyness = math.log(spot / strike)
    standardized_drift = (carry + 0.5 * variance) * tau
    d1 = (log_moneyness + standardized_drift) / root_variance
    d2 = d1 - root_variance
    spot_discount = math.exp(-dividend * tau)
    strike_discount = math.exp(-rate * tau)
    discounted_spot = spot * spot_discount
    discounted_strike = strike * strike_discount
    density = _pdf(d1)
    return (
        spot,
        strike,
        tau,
        rate,
        dividend,
        sqrt_tau,
        root_variance,
        d1,
        d2,
        spot_discount,
        strike_discount,
        discounted_spot,
        discounted_strike,
        density,
    )


def _price(row, sigma):
    values = _terms(row, sigma)
    d1 = values[7]
    d2 = values[8]
    discounted_spot = values[11]
    discounted_strike = values[12]
    if row["call_put"] == "call":
        return discounted_spot * _cdf(d1) - discounted_strike * _cdf(d2)
    return discounted_strike * _cdf(-d2) - discounted_spot * _cdf(-d1)


def _solve_row(row):
    observed_decimal = (
        Decimal(str(row["bid"])) + Decimal(str(row["ask"]))
    ) / Decimal(2)
    observed = float(observed_decimal)
    low, high = 0.000001, 5.0
    if not _price(row, low) <= observed <= _price(row, high):
        raise ValueError("published row has no root in the frozen bracket")
    for _ in range(80):
        midpoint = (low + high) / 2.0
        if _price(row, midpoint) < observed:
            low = midpoint
        else:
            high = midpoint
    sigma = (low + high) / 2.0
    values = _terms(row, sigma)
    spot = values[0]
    strike = values[1]
    tau = values[2]
    rate = values[3]
    dividend = values[4]
    sqrt_tau = values[5]
    root_variance = values[6]
    d1 = values[7]
    d2 = values[8]
    spot_discount = values[9]
    strike_discount = values[10]
    discounted_spot = values[11]
    discounted_strike = values[12]
    density = values[13]
    gamma = spot_discount * density / (spot * root_variance)
    vega = 0.01 * discounted_spot * density * sqrt_tau
    diffusion_theta = -discounted_spot * density * sigma / (2.0 * sqrt_tau)
    if row["call_put"] == "call":
        cdf_d1 = _cdf(d1)
        cdf_d2 = _cdf(d2)
        delta = spot_discount * cdf_d1
        theta = (
            diffusion_theta
            - rate * discounted_strike * cdf_d2
            + dividend * discounted_spot * cdf_d1
        ) / 365.0
        rho = 0.01 * strike * tau * strike_discount * cdf_d2
    else:
        cdf_minus_d1 = _cdf(-d1)
        cdf_minus_d2 = _cdf(-d2)
        delta = -spot_discount * cdf_minus_d1
        theta = (
            diffusion_theta
            + rate * discounted_strike * cdf_minus_d2
            - dividend * discounted_spot * cdf_minus_d1
        ) / 365.0
        rho = -0.01 * strike * tau * strike_discount * cdf_minus_d2
    return {
        "row_id": row["row_id"],
        "iv_status": "CONVERGED_FIXED_ITERATIONS",
        "market_implied_volatility": _canonical(sigma),
        "unit_delta": _canonical(delta),
        "unit_gamma": _canonical(gamma),
        "unit_vega_1volpt": _canonical(vega),
        "unit_theta_1calendar_day": _canonical(theta),
        "unit_rho_1pct": _canonical(rho),
    }


def solve(tools):
    underlyings = tools.query_greeks_underlying_market_v2()
    options = tools.query_greeks_option_quotes_v2()
    join_fields = ("task_id", "snapshot_id", "valuation_date", "underlying_id")
    underlying_by_key = {}
    for underlying in underlyings:
        key = tuple(underlying[field] for field in join_fields)
        if key in underlying_by_key:
            raise ValueError("underlying join key is duplicated")
        underlying_by_key[key] = underlying
    rows = []
    for option in options:
        key = tuple(option[field] for field in join_fields)
        if key not in underlying_by_key:
            raise ValueError("option row has no underlying match")
        row = dict(underlying_by_key[key])
        row.update(option)
        rows.append(row)
    expected_ids = [f"row_{index:06d}" for index in range(1, len(rows) + 1)]
    if [row["row_id"] for row in rows] != expected_ids:
        raise ValueError("input rows are not in canonical order")
    payload = {
        "task_id": rows[0]["task_id"],
        "submission_schema_version": (
            "bsm-market-implied-greeks-submission-v2.0.0"
        ),
        "method_id": "bsm-mid-iv-bisection80-analytic-greeks-v1",
        "status": "completed",
        "rows": [_solve_row(row) for row in rows],
    }
    tools.submit_greeks_submission_v2(payload)
    return payload


__all__ = ["solve"]
