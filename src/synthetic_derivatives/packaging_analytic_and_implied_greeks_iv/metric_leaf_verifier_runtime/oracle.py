"""Shared independent QuantLib BSM oracle and canonicalization code."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import math
import re
from typing import Any

import QuantLib as ql

from .models import (
    BSMMarketMetricInput,
    _binary64,
    _quote_decimal,
)
from .profiles import (
    MetricSpec,
    _BISECTION_ITERATIONS,
    _LOWER_VOLATILITY,
    _SUCCESS_IV_STATUS,
    _UPPER_VOLATILITY,
    _spec_from_config,
)


_PINNED_QUANTLIB_VERSION = "1.39"
_OUTPUT_QUANTUM = Decimal("0.00000001")


def _require_pinned_quantlib() -> None:
    if ql.__version__ != _PINNED_QUANTLIB_VERSION:
        raise RuntimeError(
            "trusted package verifier requires QuantLib=="
            f"{_PINNED_QUANTLIB_VERSION}, found {ql.__version__}"
        )


@dataclass(frozen=True)
class _NormalizedInput:
    source: BSMMarketMetricInput
    spot: float
    strike: float
    tau: float
    risk_free_rate: float
    dividend_yield: float
    observed_price: float


def _normalize_input(task_input: BSMMarketMetricInput) -> _NormalizedInput:
    bid = _quote_decimal(task_input.bid, "bid")
    ask = _quote_decimal(task_input.ask, "ask")
    observed_price = float((bid + ask) / Decimal(2))
    if not math.isfinite(observed_price):
        raise ValueError("observed midpoint must be finite in binary64")
    return _NormalizedInput(
        source=task_input,
        spot=_binary64(task_input.spot, "spot"),
        strike=_binary64(task_input.strike, "strike"),
        tau=_binary64(task_input.time_to_expiry_actual365, "time_to_expiry_actual365"),
        risk_free_rate=_binary64(task_input.risk_free_rate, "risk_free_rate"),
        dividend_yield=_binary64(task_input.dividend_yield, "dividend_yield"),
        observed_price=observed_price,
    )


def _quantlib_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _quantlib_value(
    task_input: _NormalizedInput, sigma: float, target: str
) -> float:
    _require_pinned_quantlib()
    source = task_input.source
    settings = ql.Settings.instance()
    previous_evaluation_date = settings.evaluationDate
    valuation_date = _quantlib_date(source.valuation_date)
    expiry = _quantlib_date(source.expiry)
    day_count = ql.Actual365Fixed()
    try:
        settings.evaluationDate = valuation_date
        spot = ql.QuoteHandle(ql.SimpleQuote(task_input.spot))
        risk_free_curve = ql.YieldTermStructureHandle(
            ql.FlatForward(
                valuation_date,
                task_input.risk_free_rate,
                day_count,
                ql.Continuous,
                ql.Annual,
            )
        )
        dividend_curve = ql.YieldTermStructureHandle(
            ql.FlatForward(
                valuation_date,
                task_input.dividend_yield,
                day_count,
                ql.Continuous,
                ql.Annual,
            )
        )
        volatility = ql.BlackVolTermStructureHandle(
            ql.BlackConstantVol(
                valuation_date,
                ql.NullCalendar(),
                sigma,
                day_count,
            )
        )
        process = ql.BlackScholesMertonProcess(
            spot, dividend_curve, risk_free_curve, volatility
        )
        option_type = ql.Option.Call if source.call_put == "call" else ql.Option.Put
        payoff = ql.PlainVanillaPayoff(option_type, task_input.strike)
        option = ql.VanillaOption(payoff, ql.EuropeanExercise(expiry))
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
        values = {
            "price": option.NPV,
            "delta": option.delta,
            "gamma": option.gamma,
            "vega_1volpt": lambda: 0.01 * option.vega(),
            "theta_1calendar_day": lambda: option.theta() / 365.0,
            "rho_1pct": lambda: 0.01 * option.rho(),
        }
        try:
            evaluator = values[target]
        except KeyError as error:
            raise ValueError("QuantLib target is not allowlisted") from error
        return float(evaluator())
    finally:
        settings.evaluationDate = previous_evaluation_date


def _market_implied_root(task_input: _NormalizedInput) -> float:
    discounted_spot = task_input.spot * math.exp(
        -task_input.dividend_yield * task_input.tau
    )
    discounted_strike = task_input.strike * math.exp(
        -task_input.risk_free_rate * task_input.tau
    )
    if task_input.source.call_put == "call":
        lower_bound = max(discounted_spot - discounted_strike, 0.0)
        upper_bound = discounted_spot
    else:
        lower_bound = max(discounted_strike - discounted_spot, 0.0)
        upper_bound = discounted_strike
    if not lower_bound <= task_input.observed_price < upper_bound:
        raise ValueError("published single-metric row is outside BSM price bounds")

    low = _LOWER_VOLATILITY
    high = _UPPER_VOLATILITY
    price_low = _quantlib_value(task_input, low, "price")
    price_high = _quantlib_value(task_input, high, "price")
    if not price_low <= task_input.observed_price <= price_high:
        raise ValueError("published single-metric row has no root in the bracket")
    for _ in range(_BISECTION_ITERATIONS):
        midpoint = (low + high) / 2.0
        if _quantlib_value(task_input, midpoint, "price") < task_input.observed_price:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def _canonical_decimal(value: float) -> str:
    if not isinstance(value, float) or not math.isfinite(value):
        raise ValueError("canonical output must be a finite binary64 value")
    try:
        quantized = Decimal(str(value)).quantize(
            _OUTPUT_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    except InvalidOperation as error:
        raise ValueError("canonical output exceeds decimal contract") from error
    if quantized == 0:
        quantized = Decimal("0").quantize(_OUTPUT_QUANTUM)
    return format(quantized, "f")


def _metric_value(spec: MetricSpec, normalized: _NormalizedInput, sigma: float) -> str:
    if spec.target == "iv":
        return _canonical_decimal(sigma)
    return _canonical_decimal(_quantlib_value(normalized, sigma, spec.target))


def _expected_submission(
    task_inputs: Iterable[BSMMarketMetricInput], spec: MetricSpec
) -> dict[str, Any]:
    rows = tuple(sorted(task_inputs, key=lambda item: item.canonical_order_key))
    if len(rows) != 160 or len({row.task_id for row in rows}) != 1:
        raise ValueError("trusted verifier requires one complete 160-row task")
    if re.fullmatch(spec.task_id_pattern, rows[0].task_id) is None:
        raise ValueError("task inputs do not belong to the configured target")
    expected_ids = tuple(f"row_{index:06d}" for index in range(1, 161))
    if tuple(row.row_id for row in rows) != expected_ids:
        raise ValueError("trusted verifier input order is invalid")

    results: list[dict[str, str]] = []
    for row in rows:
        normalized = _normalize_input(row)
        sigma = _market_implied_root(normalized)
        result = {
            "row_id": row.row_id,
            spec.output_field: _metric_value(spec, normalized, sigma),
        }
        if spec.needs_iv_status:
            result["iv_status"] = _SUCCESS_IV_STATUS
        results.append(result)
    return {
        "task_id": rows[0].task_id,
        "submission_schema_version": spec.submission_schema_version,
        "method_id": spec.method_id,
        "status": "completed",
        "rows": results,
    }


def expected_market_metric_submission(
    task_inputs: Iterable[BSMMarketMetricInput],
    method_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute and return the canonical single-target submission."""

    spec = _spec_from_config(method_config)
    return _expected_submission(task_inputs, spec)


__all__ = ["expected_market_metric_submission"]
