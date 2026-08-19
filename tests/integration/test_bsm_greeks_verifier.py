from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import QuantLib as ql
import pytest

from synthetic_derivatives.solver.analytic_and_implied_greeks_iv.bsm import (
    solve_bsm_greeks,
    solve_bsm_greeks_batch,
)
from synthetic_derivatives.tasks.bsm_greeks import BSMGreeksInput
from synthetic_derivatives.verifier.bsm_greeks import (
    trusted_quantlib_result,
    trusted_quantlib_results,
    verify_bsm_greeks_submission,
)


def _row(
    index: int,
    *,
    call_put: str,
    spot: float,
    strike: float,
    days: int,
    sigma: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> BSMGreeksInput:
    valuation_date = date(2026, 1, 2)
    return BSMGreeksInput(
        task_id="cross-backend",
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
        sigma=sigma,
    )


_CROSS_BACKEND_CASES = (
    ("call", 120.0, 90.0, 180, 0.2, 0.04, 0.015),
    ("put", 120.0, 90.0, 180, 0.2, 0.04, 0.015),
    ("call", 100.0, 100.0, 7, 0.05, 0.01, 0.02),
    ("put", 100.0, 100.0, 7, 0.05, 0.01, 0.02),
    ("call", 80.0, 120.0, 730, 0.8, -0.01, 0.03),
    ("put", 80.0, 120.0, 730, 0.8, -0.01, 0.03),
)


def _task_rows() -> tuple[BSMGreeksInput, ...]:
    return tuple(
        _row(
            index,
            call_put=case[0],
            spot=case[1],
            strike=case[2],
            days=case[3],
            sigma=case[4],
            risk_free_rate=case[5],
            dividend_yield=case[6],
        )
        for index, case in enumerate(_CROSS_BACKEND_CASES, start=1)
    )


@pytest.mark.parametrize(
    "task_input",
    _task_rows(),
    ids=lambda row: f"{row.call_put}-{row.strike:g}-{row.expiry.isoformat()}",
)
def test_stdlib_solver_matches_pinned_quantlib_after_canonicalization(
    task_input: BSMGreeksInput,
) -> None:
    assert ql.__version__ == "1.39"
    assert solve_bsm_greeks(task_input).to_dict() == (
        trusted_quantlib_result(task_input).to_dict()
    )


def test_quantlib_verifier_restores_process_wide_evaluation_date() -> None:
    settings = ql.Settings.instance()
    original = settings.evaluationDate
    sentinel = ql.Date(17, 6, 2025)
    settings.evaluationDate = sentinel
    try:
        trusted_quantlib_result(_task_rows()[0])
        assert settings.evaluationDate == sentinel
    finally:
        settings.evaluationDate = original


def test_exact_submission_verifier_accepts_the_reference_solver() -> None:
    rows = tuple(reversed(_task_rows()))
    submission = [result.to_dict() for result in solve_bsm_greeks_batch(rows)]

    verify_bsm_greeks_submission(rows, submission)
    assert submission == [row.to_dict() for row in trusted_quantlib_results(rows)]


def _valid_submission() -> tuple[tuple[BSMGreeksInput, ...], list[dict[str, str]]]:
    rows = _task_rows()
    submission = [result.to_dict() for result in solve_bsm_greeks_batch(rows)]
    return rows, submission


def test_verifier_rejects_a_last_decimal_place_change() -> None:
    rows, submission = _valid_submission()
    changed = deepcopy(submission)
    original = changed[0]["price"]
    final_digit = "1" if original[-1] != "1" else "2"
    changed[0]["price"] = original[:-1] + final_digit

    with pytest.raises(ValueError, match="canonical mismatch at row 0"):
        verify_bsm_greeks_submission(rows, changed)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("vega", "0.00000000"),
        ("rho", "0.00000000"),
        ("theta", "0.00000000"),
        ("call_put", "put"),
    ],
)
def test_verifier_rejects_wrong_units_sign_or_contract_identity(
    field: str,
    replacement: str,
) -> None:
    rows, submission = _valid_submission()
    changed = deepcopy(submission)
    changed[0][field] = replacement

    with pytest.raises(ValueError, match="canonical mismatch"):
        verify_bsm_greeks_submission(rows, changed)


def test_verifier_rejects_wrong_method_schema_row_order_and_count() -> None:
    rows, submission = _valid_submission()

    wrong_method = deepcopy(submission)
    wrong_method[0]["method_id"] = "different-method"
    with pytest.raises(ValueError, match="canonical schema"):
        verify_bsm_greeks_submission(rows, wrong_method)

    missing_field = deepcopy(submission)
    missing_field[0].pop("gamma")
    with pytest.raises(ValueError, match="canonical schema"):
        verify_bsm_greeks_submission(rows, missing_field)

    wrong_order = deepcopy(submission)
    wrong_order[0], wrong_order[1] = wrong_order[1], wrong_order[0]
    with pytest.raises(ValueError, match="canonical mismatch at row 0"):
        verify_bsm_greeks_submission(rows, wrong_order)

    with pytest.raises(ValueError, match="row count"):
        verify_bsm_greeks_submission(rows, submission[:-1])


def test_verifier_source_is_independent_and_uses_exact_comparison(
    repository_root: Path,
) -> None:
    source = (
        repository_root / "src/synthetic_derivatives/verifier/bsm_greeks.py"
    ).read_text(encoding="utf-8")

    assert "import QuantLib as ql" in source
    assert "AnalyticEuropeanEngine" in source
    assert "synthetic_derivatives.solver" not in source
    assert "pytest.approx" not in source
    assert "math.isclose" not in source
    assert ".to_dict() != expected_row.to_dict()" in source
