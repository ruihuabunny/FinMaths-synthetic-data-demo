from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import QuantLib as ql
import pytest

from synthetic_derivatives.solver.bsm_implied_volatility import (
    solve_bsm_implied_volatility,
)
from synthetic_derivatives.tasks.bsm_implied_volatility import (
    BISECTION_ITERATIONS,
    BSM_IV_INVALID_INPUT,
    BSM_IV_NO_BRACKET,
    BSM_IV_OK,
    BSM_IV_OUT_OF_BOUNDS,
    BSMImpliedVolatilityInput,
)
from synthetic_derivatives.verifier import bsm_implied_volatility as verifier_iv


_CASES = (
    ("call", 120.0, 90.0, 180, 0.04, 0.015, "30.95764523", "0.20000000"),
    ("put", 120.0, 90.0, 180, 0.04, 0.015, "0.08409457", "0.20000000"),
    ("call", 100.0, 100.0, 7, 0.01, 0.02, "0.26667714", "0.05000000"),
    ("put", 100.0, 100.0, 7, 0.01, 0.02, "0.28584971", "0.05000000"),
    ("call", 80.0, 120.0, 730, -0.01, 0.03, "22.18505914", "0.80000000"),
    ("put", 80.0, 120.0, 730, -0.01, 0.03, "69.26805725", "0.80000000"),
    ("call", 100.0, 100.0, 365, 0.03, 0.01, "97.60471801", "4.90000000"),
    ("put", 100.0, 100.0, 365, 0.03, 0.01, "95.64428799", "4.90000000"),
)


def _row(index: int, case: tuple[object, ...]) -> BSMImpliedVolatilityInput:
    call_put, spot, strike, days, risk_free_rate, dividend_yield, quote, _ = case
    valuation_date = date(2026, 1, 2)
    return BSMImpliedVolatilityInput(
        task_id="iv-cross-backend",
        valuation_date=valuation_date,
        underlying_id="SYN-USD",
        option_id=f"OPTION-{index:02}",
        call_put=call_put,
        spot=spot,
        strike=strike,
        expiry=valuation_date + timedelta(days=days),
        time_to_expiry_actual365=days / 365.0,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        bid=Decimal(quote),
        ask=Decimal(quote),
        contract_multiplier=100.0,
    )


@pytest.mark.parametrize(
    ("index", "case"),
    tuple(enumerate(_CASES, start=1)),
)
def test_solver_and_quantlib_verifier_exactly_match_canonical_iv(
    index: int,
    case: tuple[object, ...],
) -> None:
    task_input = _row(index, case)
    expected_iv = case[-1]

    solver_result = solve_bsm_implied_volatility(task_input)
    verifier_result = verifier_iv.trusted_quantlib_implied_volatility(task_input)

    assert ql.__version__ == "1.39"
    assert solver_result.to_dict() == verifier_result.to_dict()
    assert solver_result.status == BSM_IV_OK
    assert solver_result.implied_volatility == expected_iv
    assert solver_result.iterations == BISECTION_ITERATIONS


@pytest.mark.parametrize(
    ("changes", "expected_status"),
    [
        ({"spot": 0.0}, BSM_IV_INVALID_INPUT),
        ({"bid": Decimal("200"), "ask": Decimal("200")}, BSM_IV_OUT_OF_BOUNDS),
        (
            {"bid": Decimal("115.51987715"), "ask": Decimal("115.51987715")},
            BSM_IV_NO_BRACKET,
        ),
    ],
)
def test_solver_and_verifier_exactly_match_failure_statuses(
    changes: dict[str, object],
    expected_status: str,
) -> None:
    task_input = replace(_row(1, _CASES[0]), **changes)

    solver_result = solve_bsm_implied_volatility(task_input)
    verifier_result = verifier_iv.trusted_quantlib_implied_volatility(task_input)

    assert solver_result.to_dict() == verifier_result.to_dict()
    assert solver_result.status == expected_status
    assert solver_result.implied_volatility is None
    assert solver_result.iterations == 0


def test_quantlib_success_path_executes_all_80_price_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = verifier_iv._quantlib_price
    evaluated_volatilities: list[float] = []

    def counted_price(task_input, volatility):
        evaluated_volatilities.append(volatility)
        return original(task_input, volatility)

    monkeypatch.setattr(verifier_iv, "_quantlib_price", counted_price)

    result = verifier_iv.trusted_quantlib_implied_volatility(_row(1, _CASES[0]))

    assert result.status == BSM_IV_OK
    assert len(evaluated_volatilities) == 2 + BISECTION_ITERATIONS
    assert evaluated_volatilities[:2] == [0.000001, 5.0]


def test_verifier_accepts_only_the_exact_canonical_result() -> None:
    task_input = _row(1, _CASES[0])
    submission = solve_bsm_implied_volatility(task_input).to_dict()

    verifier_iv.verify_bsm_iv_submission(task_input, submission)

    changed_last_place = deepcopy(submission)
    changed_last_place["implied_volatility"] = "0.20000001"
    with pytest.raises(ValueError, match="result mismatch"):
        verifier_iv.verify_bsm_iv_submission(task_input, changed_last_place)

    changed_observed = deepcopy(submission)
    changed_observed["observed_price"] = "30.957645231"
    with pytest.raises(ValueError, match="result mismatch"):
        verifier_iv.verify_bsm_iv_submission(task_input, changed_observed)

    changed_status = deepcopy(submission)
    changed_status.update(
        status=BSM_IV_NO_BRACKET,
        implied_volatility=None,
        iterations=0,
    )
    with pytest.raises(ValueError, match="result mismatch"):
        verifier_iv.verify_bsm_iv_submission(task_input, changed_status)

    changed_method = deepcopy(submission)
    changed_method["method_id"] = "different-method"
    with pytest.raises(ValueError, match="canonical IV schema"):
        verifier_iv.verify_bsm_iv_submission(task_input, changed_method)


def test_quantlib_evaluation_date_is_restored_after_all_iterations() -> None:
    settings = ql.Settings.instance()
    original = settings.evaluationDate
    sentinel = ql.Date(17, 6, 2025)
    settings.evaluationDate = sentinel
    try:
        verifier_iv.trusted_quantlib_implied_volatility(_row(1, _CASES[0]))
        assert settings.evaluationDate == sentinel
    finally:
        settings.evaluationDate = original


def test_verifier_iv_source_is_independent_and_has_no_approximate_comparison(
    repository_root: Path,
) -> None:
    source = (
        repository_root
        / "src/synthetic_derivatives/verifier/bsm_implied_volatility.py"
    ).read_text(encoding="utf-8")

    assert "trusted_quantlib_values" in source
    assert "synthetic_derivatives.solver" not in source
    assert "isclose" not in source
    assert "tolerance" not in source
    assert "actual.to_dict() != expected.to_dict()" in source
