"""Independent QuantLib oracle for the analytic BSM Greeks contract."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import QuantLib as ql

from synthetic_derivatives.tasks.bsm_greeks import (
    BSMGreeksInput,
    BSMGreeksValues,
    CanonicalBSMGreeksResult,
    canonicalize_bsm_greeks,
)


_PINNED_QUANTLIB_VERSION = "1.39"


def _require_pinned_quantlib() -> None:
    if ql.__version__ != _PINNED_QUANTLIB_VERSION:
        raise RuntimeError(
            "trusted BSM verifier requires QuantLib=="
            f"{_PINNED_QUANTLIB_VERSION}, found {ql.__version__}"
        )


def _quantlib_date(value: Any) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def trusted_quantlib_values(task_input: BSMGreeksInput) -> BSMGreeksValues:
    """Evaluate one row with QuantLib's analytic European engine."""

    _require_pinned_quantlib()
    settings = ql.Settings.instance()
    previous_evaluation_date = settings.evaluationDate
    valuation_date = _quantlib_date(task_input.valuation_date)
    expiry = _quantlib_date(task_input.expiry)
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
                task_input.sigma,
                day_count,
            )
        )
        process = ql.BlackScholesMertonProcess(
            spot,
            dividend_curve,
            risk_free_curve,
            volatility,
        )
        option_type = (
            ql.Option.Call if task_input.call_put == "call" else ql.Option.Put
        )
        payoff = ql.PlainVanillaPayoff(option_type, task_input.strike)
        option = ql.VanillaOption(payoff, ql.EuropeanExercise(expiry))
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))

        return BSMGreeksValues(
            price=float(option.NPV()),
            delta=float(option.delta()),
            gamma=float(option.gamma()),
            vega=float(0.01 * option.vega()),
            theta=float(option.theta() / 365.0),
            rho=float(0.01 * option.rho()),
        )
    finally:
        settings.evaluationDate = previous_evaluation_date


def trusted_quantlib_result(
    task_input: BSMGreeksInput,
) -> CanonicalBSMGreeksResult:
    """Build one canonical oracle row."""

    return canonicalize_bsm_greeks(
        task_input,
        trusted_quantlib_values(task_input),
    )


def trusted_quantlib_results(
    task_inputs: Iterable[BSMGreeksInput],
) -> tuple[CanonicalBSMGreeksResult, ...]:
    """Build unique canonical oracle rows in the frozen order."""

    ordered_inputs = sorted(task_inputs, key=lambda row: row.canonical_order_key)
    row_ids = [(row.task_id, row.option_id) for row in ordered_inputs]
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("task_id/option_id rows must be unique")
    return tuple(trusted_quantlib_result(row) for row in ordered_inputs)


def verify_bsm_greeks_submission(
    task_inputs: Iterable[BSMGreeksInput],
    submission: Sequence[Mapping[str, Any]],
) -> None:
    """Reject any schema, row-order, identity, or canonical-value mismatch."""

    expected = trusted_quantlib_results(task_inputs)
    if len(submission) != len(expected):
        raise ValueError("submission row count does not match the task")
    try:
        actual = tuple(
            CanonicalBSMGreeksResult.from_mapping(row) for row in submission
        )
    except (TypeError, ValueError) as error:
        raise ValueError("submission does not satisfy the canonical schema") from error
    for row_index, (actual_row, expected_row) in enumerate(zip(actual, expected)):
        if actual_row.to_dict() != expected_row.to_dict():
            raise ValueError(f"canonical mismatch at row {row_index}")


__all__ = [
    "trusted_quantlib_result",
    "trusted_quantlib_results",
    "trusted_quantlib_values",
    "verify_bsm_greeks_submission",
]
