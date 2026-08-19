"""Standalone v3 reference solver using only the public trusted SQL tool."""

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
        "market_implied_volatility": _canonical(sigma),
        "unit_delta": _canonical(delta),
        "unit_gamma": _canonical(gamma),
        "unit_vega_1volpt": _canonical(vega),
        "unit_theta_1calendar_day": _canonical(theta),
        "unit_rho_1pct": _canonical(rho),
    }


def _records(response):
    if response["truncated"]:
        raise ValueError("trusted SQL response was truncated")
    columns = response["columns"]
    rows = response["rows"]
    if response["row_count"] != len(rows):
        raise ValueError("trusted SQL row_count is inconsistent")
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _task_contract(task_id):
    contracts = (
        (
            "bsm-market-iv-v2-",
            "market_implied_volatility",
            "bsm-market-implied-iv-submission-v2.0.0",
            "bsm-mid-iv-bisection80-v1",
            True,
        ),
        (
            "bsm-market-delta-v2-",
            "unit_delta",
            "bsm-market-implied-delta-submission-v2.0.0",
            "bsm-mid-iv-bisection80-analytic-delta-v1",
            False,
        ),
        (
            "bsm-market-gamma-v2-",
            "unit_gamma",
            "bsm-market-implied-gamma-submission-v2.0.0",
            "bsm-mid-iv-bisection80-analytic-gamma-v1",
            False,
        ),
        (
            "bsm-market-vega-1volpt-v2-",
            "unit_vega_1volpt",
            "bsm-market-implied-vega-1volpt-submission-v2.0.0",
            "bsm-mid-iv-bisection80-analytic-vega-1volpt-v1",
            False,
        ),
        (
            "bsm-market-theta-1calendar-day-v2-",
            "unit_theta_1calendar_day",
            "bsm-market-implied-theta-1calendar-day-submission-v2.0.0",
            "bsm-mid-iv-bisection80-analytic-theta-1calendar-day-v1",
            False,
        ),
        (
            "bsm-market-rho-1pct-v2-",
            "unit_rho_1pct",
            "bsm-market-implied-rho-1pct-submission-v2.0.0",
            "bsm-mid-iv-bisection80-analytic-rho-1pct-v1",
            False,
        ),
    )
    for contract in contracts:
        if task_id.startswith(contract[0]):
            return contract[1:]
    raise ValueError("public task ID does not identify a supported metric")


def solve(tools):
    relations = _records(
        tools.query_public_duckdb_v3(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "ORDER BY table_schema, table_name"
        )
    )
    relation_names = {
        (row["table_schema"], row["table_name"]) for row in relations
    }
    required_relations = {
        ("metadata", "public_task"),
        ("solver_visible", "underlying_market_inputs"),
        ("solver_visible", "option_quote_inputs"),
    }
    if relation_names != required_relations:
        raise ValueError("public relation discovery differs from the task contract")

    columns = _records(
        tools.query_public_duckdb_v3(
            "SELECT table_schema, table_name, column_name, data_type, "
            "ordinal_position FROM information_schema.columns "
            "WHERE table_schema IN ('metadata', 'solver_visible') "
            "ORDER BY table_schema, table_name, ordinal_position"
        )
    )
    if not columns:
        raise ValueError("public column discovery returned no rows")

    rows = _records(
        tools.query_public_duckdb_v3(
            "SELECT o.task_id, o.row_id, o.call_put, o.strike, o.expiry, "
            "o.time_to_expiry_actual365, o.bid, o.ask, u.spot, "
            "u.risk_free_rate, u.dividend_yield "
            "FROM solver_visible.option_quote_inputs AS o "
            "JOIN solver_visible.underlying_market_inputs AS u "
            "USING (task_id, snapshot_id, valuation_date, underlying_id) "
            "ORDER BY o.row_id"
        )
    )
    if not rows:
        raise ValueError("public option query returned no rows")
    task_ids = {row["task_id"] for row in rows}
    if len(task_ids) != 1:
        raise ValueError("public option rows identify multiple tasks")
    task_id = next(iter(task_ids))
    output_field, schema_version, method_id, needs_iv_status = _task_contract(
        task_id
    )
    result_rows = []
    for row in rows:
        solved = _solve_row(row)
        result = {"row_id": row["row_id"], output_field: solved[output_field]}
        if needs_iv_status:
            result["iv_status"] = "CONVERGED_FIXED_ITERATIONS"
        result_rows.append(result)
    payload = {
        "task_id": task_id,
        "submission_schema_version": schema_version,
        "method_id": method_id,
        "status": "completed",
        "rows": result_rows,
    }
    tools.submit_greeks_submission_v3(payload)
    return payload


__all__ = ["solve"]
