from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, timedelta
import math
from pathlib import Path

import pytest

from synthetic_derivatives.solver.analytic_and_implied_greeks_iv.bsm import (
    bsm_analytic_values,
    solve_bsm_greeks,
    solve_bsm_greeks_batch,
)
from synthetic_derivatives.tasks.bsm_greeks import BSMGreeksInput


def _input(
    *,
    option_id: str,
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
        task_id="invariant-matrix",
        valuation_date=valuation_date,
        underlying_id="SYN-USD",
        option_id=option_id,
        call_put=call_put,
        spot=spot,
        strike=strike,
        expiry=valuation_date + timedelta(days=days),
        time_to_expiry_actual365=days / 365.0,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        sigma=sigma,
    )


_SCENARIOS = (
    {
        "spot": 120.0,
        "strike": 90.0,
        "days": 180,
        "sigma": 0.2,
        "risk_free_rate": 0.04,
        "dividend_yield": 0.015,
    },
    {
        "spot": 100.0,
        "strike": 100.0,
        "days": 7,
        "sigma": 0.05,
        "risk_free_rate": 0.01,
        "dividend_yield": 0.02,
    },
    {
        "spot": 80.0,
        "strike": 120.0,
        "days": 730,
        "sigma": 0.8,
        "risk_free_rate": -0.01,
        "dividend_yield": 0.03,
    },
)


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_bsm_price_and_greeks_obey_model_invariants(
    scenario: dict[str, float | int],
) -> None:
    call_input = _input(option_id="CALL", call_put="call", **scenario)
    put_input = replace(call_input, option_id="PUT", call_put="put")
    call = bsm_analytic_values(call_input)
    put = bsm_analytic_values(put_input)
    tau = call_input.time_to_expiry_actual365
    discounted_spot = call_input.spot * math.exp(-call_input.dividend_yield * tau)
    discounted_strike = call_input.strike * math.exp(
        -call_input.risk_free_rate * tau
    )

    assert max(0.0, discounted_spot - discounted_strike) <= call.price
    assert call.price <= discounted_spot
    assert max(0.0, discounted_strike - discounted_spot) <= put.price
    assert put.price <= discounted_strike
    assert call.price - put.price == pytest.approx(
        discounted_spot - discounted_strike,
        abs=1e-13,
    )
    assert call.delta - put.delta == pytest.approx(
        math.exp(-call_input.dividend_yield * tau),
        abs=1e-14,
    )
    assert call.gamma == put.gamma
    assert call.vega == put.vega
    assert call.gamma > 0.0
    assert call.vega > 0.0
    assert 0.0 < call.delta < math.exp(-call_input.dividend_yield * tau)
    assert -math.exp(-call_input.dividend_yield * tau) < put.delta < 0.0


def test_public_sanity_case_freezes_units_and_signs() -> None:
    row = _input(
        option_id="ATM-CALL",
        call_put="call",
        spot=100.0,
        strike=100.0,
        days=365,
        sigma=0.2,
        risk_free_rate=0.05,
        dividend_yield=0.02,
    )

    assert solve_bsm_greeks(row).to_dict() == {
        "task_id": "invariant-matrix",
        "valuation_date": "2026-01-02",
        "underlying_id": "SYN-USD",
        "option_id": "ATM-CALL",
        "call_put": "call",
        "expiry": "2027-01-02",
        "strike": "100.00000000",
        "price": "9.22700551",
        "delta": "0.58685115",
        "gamma": "0.01895058",
        "vega": "0.37901158",
        "theta": "-0.01394334",
        "rho": "0.49458109",
        "method_id": "bsm-analytic-float64-greeks-v1",
        "convention_id": "bsm-spot-greeks-actual365-flat-continuous-v1",
        "output_contract_id": "bsm-analytic-greeks-output-v1",
    }


def test_batch_order_is_canonical_and_duplicate_rows_are_rejected() -> None:
    first = _input(
        option_id="Z-PUT",
        call_put="put",
        spot=100.0,
        strike=110.0,
        days=365,
        sigma=0.2,
        risk_free_rate=0.03,
        dividend_yield=0.01,
    )
    second = _input(
        option_id="A-CALL",
        call_put="call",
        spot=100.0,
        strike=90.0,
        days=30,
        sigma=0.2,
        risk_free_rate=0.03,
        dividend_yield=0.01,
    )

    results = solve_bsm_greeks_batch([first, second])

    assert [row.option_id for row in results] == ["A-CALL", "Z-PUT"]
    with pytest.raises(ValueError, match="must be unique"):
        solve_bsm_greeks_batch([first, first])


def test_solver_imports_only_stdlib_and_the_shared_task_contract(
    repository_root: Path,
) -> None:
    solver_files = (
        repository_root / "src/synthetic_derivatives/solver/__init__.py",
        repository_root
        / "src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/__init__.py",
        repository_root
        / "src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/bsm.py",
        repository_root
        / "src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/bsm_implied_volatility.py",
        repository_root
        / "src/synthetic_derivatives/solver/analytic_and_implied_greeks_iv/bsm_market_greeks.py",
    )
    allowed_roots = {
        "__future__",
        "collections",
        "math",
        "typing",
        "synthetic_derivatives",
    }
    forbidden_roots = {
        "QuantLib",
        "numpy",
        "pandas",
        "py_vollib",
        "mibian",
        "rateslib",
        "scipy",
    }

    for path in solver_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert imports <= allowed_roots
        assert imports.isdisjoint(forbidden_roots)

    implementation = "\n".join(
        path.read_text(encoding="utf-8") for path in solver_files
    )
    assert "synthetic_derivatives.verifier" not in implementation
