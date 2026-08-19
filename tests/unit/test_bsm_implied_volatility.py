from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path

import pytest

from synthetic_derivatives.solver.analytic_and_implied_greeks_iv import (
    bsm_implied_volatility as solver_iv,
)
from synthetic_derivatives.tasks.bsm_implied_volatility import (
    BISECTION_ITERATIONS,
    BSM_IV_INVALID_INPUT,
    BSM_IV_METHOD_ID,
    BSM_IV_NO_BRACKET,
    BSM_IV_OK,
    BSM_IV_OUT_OF_BOUNDS,
    BSM_IV_SCHEMA_VERSION,
    BSMImpliedVolatilityContract,
    BSMImpliedVolatilityInput,
    CanonicalBSMImpliedVolatilityResult,
    bsm_iv_contract,
    bsm_iv_contract_json,
    normalize_bsm_iv_input,
)


def _input(
    *,
    call_put: str = "call",
    bid: object = Decimal("9.22700551"),
    ask: object = Decimal("9.22700551"),
) -> BSMImpliedVolatilityInput:
    return BSMImpliedVolatilityInput(
        task_id="iv-task",
        valuation_date=date(2026, 1, 2),
        underlying_id="SYN-USD",
        option_id=f"ATM-{call_put.upper()}",
        call_put=call_put,
        spot=100.0,
        strike=100.0,
        expiry=date(2027, 1, 2),
        time_to_expiry_actual365=1.0,
        risk_free_rate=0.05,
        dividend_yield=0.02,
        bid=bid,
        ask=ask,
        contract_multiplier=100.0,
    )


def test_public_quote_midpoint_is_decimal_first_then_cast_once() -> None:
    normalized = normalize_bsm_iv_input(
        _input(
            bid=Decimal("1.00000000"),
            ask=Decimal("1.00000001"),
        )
    )

    assert normalized.bid == Decimal("1.00000000")
    assert normalized.ask == Decimal("1.00000001")
    assert normalized.observed_price_decimal == Decimal("1.000000005")
    assert normalized.observed_price == float(Decimal("1.000000005"))


@pytest.mark.parametrize(
    "changes",
    [
        {"spot": 0.0},
        {"strike": -1.0},
        {"time_to_expiry_actual365": 0.0},
        {"time_to_expiry_actual365": 2.0},
        {"risk_free_rate": float("nan")},
        {"dividend_yield": float("inf")},
        {"bid": Decimal("-0.00000001")},
        {"bid": Decimal("2.00000000"), "ask": Decimal("1.00000000")},
        {"bid": Decimal("1.000000001")},
        {"contract_multiplier": 0.0},
        {"expiry": date(2026, 1, 2)},
    ],
)
def test_model_domain_and_quote_failures_have_canonical_invalid_input_status(
    changes: dict[str, object],
) -> None:
    result = solver_iv.solve_bsm_implied_volatility(replace(_input(), **changes))

    assert result.status == BSM_IV_INVALID_INPUT
    assert result.observed_price is None
    assert result.implied_volatility is None
    assert result.iterations == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("call_put", "CALL", "call_put"),
        ("currency", "EUR", "require USD"),
        ("exercise_style", "american", "European exercise"),
        ("settlement_type", "physical", "cash settlement"),
        ("valuation_date", datetime(2026, 1, 2), "datetime.date"),
    ],
)
def test_unidentifiable_or_wrong_contract_rows_are_rejected_before_evaluation(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_input(), **{field: value})


def test_call_and_put_round_trip_from_visible_prices() -> None:
    call = solver_iv.solve_bsm_implied_volatility(_input())
    put = solver_iv.solve_bsm_implied_volatility(
        _input(
            call_put="put",
            bid=Decimal("6.33008063"),
            ask=Decimal("6.33008063"),
        )
    )

    assert call.status == put.status == BSM_IV_OK
    assert call.implied_volatility == put.implied_volatility == "0.20000000"
    assert call.iterations == put.iterations == BISECTION_ITERATIONS
    assert call.observed_price == "9.227005510"
    assert put.observed_price == "6.330080630"


def test_discounted_price_domain_and_finite_bracket_have_distinct_statuses() -> None:
    below_call_bound = solver_iv.solve_bsm_implied_volatility(
        _input(bid=Decimal("0"), ask=Decimal("0"))
    )
    above_call_bound = solver_iv.solve_bsm_implied_volatility(
        _input(bid=Decimal("200"), ask=Decimal("200"))
    )
    above_volatility_bracket = solver_iv.solve_bsm_implied_volatility(
        _input(
            bid=Decimal("97.75917633"),
            ask=Decimal("97.75917633"),
        )
    )

    assert below_call_bound.status == BSM_IV_OUT_OF_BOUNDS
    assert above_call_bound.status == BSM_IV_OUT_OF_BOUNDS
    assert above_volatility_bracket.status == BSM_IV_NO_BRACKET
    for result in (
        below_call_bound,
        above_call_bound,
        above_volatility_bracket,
    ):
        assert result.observed_price is not None
        assert result.implied_volatility is None
        assert result.iterations == 0


def test_success_path_executes_all_80_price_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = solver_iv._bsm_price
    evaluated_volatilities: list[float] = []

    def counted_price(task_input, volatility):
        evaluated_volatilities.append(volatility)
        return original(task_input, volatility)

    monkeypatch.setattr(solver_iv, "_bsm_price", counted_price)

    result = solver_iv.solve_bsm_implied_volatility(_input())

    assert result.status == BSM_IV_OK
    assert len(evaluated_volatilities) == 2 + BISECTION_ITERATIONS
    assert evaluated_volatilities[:2] == [0.000001, 5.0]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"method_id": "different"}, "method contract"),
        ({"lower_volatility": 0.0}, "bracket"),
        ({"upper_volatility": 4.0}, "bracket"),
        ({"iterations": 79}, "exactly 80"),
        ({"midpoint_rule": "adaptive"}, "method contract"),
        ({"comparison_rule": "price_mid_le_observed"}, "method contract"),
        ({"early_stop": True}, "forbids early stop"),
        ({"fallback_method": "newton"}, "forbids early stop"),
        ({"output_precision": 10}, "precision must be 8"),
    ],
)
def test_inversion_contract_rejects_any_algorithm_change(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(BSMImpliedVolatilityContract(), **changes)


def test_task_variant_is_the_only_config_source_for_the_iv_method(
    repository_root: Path,
) -> None:
    variant = json.loads(
        (repository_root / "configs/variants/bsm_iv_scalar_v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert variant["variant_id"] == "bsm_iv_scalar_v1"
    assert variant["coordinates"]["F"] == "F0"
    assert variant["inversion_contract"] == bsm_iv_contract()
    assert BSMImpliedVolatilityContract.from_mapping(
        variant["inversion_contract"]
    ) == BSMImpliedVolatilityContract()
    assert json.loads(bsm_iv_contract_json()) == bsm_iv_contract()

    for generator_config in (repository_root / "configs/generators").glob("*.json"):
        source = generator_config.read_text(encoding="utf-8")
        assert BSM_IV_METHOD_ID not in source
        assert "bsm_iv_scalar_v1" not in source


def test_output_schema_and_runtime_freeze_all_status_shapes(
    repository_root: Path,
) -> None:
    schema = json.loads(
        (
            repository_root
            / "schemas/bsm-implied-volatility-output.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["x-schema-version"] == BSM_IV_SCHEMA_VERSION
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["status"]["enum"] == [
        BSM_IV_OK,
        BSM_IV_INVALID_INPUT,
        BSM_IV_OUT_OF_BOUNDS,
        BSM_IV_NO_BRACKET,
    ]
    assert {"d1", "d2"}.isdisjoint(schema["properties"])
    assert bsm_iv_contract()["persisted_intermediates"] == []


def test_canonical_result_rejects_inconsistent_status_payloads() -> None:
    valid = solver_iv.solve_bsm_implied_volatility(_input()).to_dict()

    wrong_iterations = dict(valid, iterations=79)
    with pytest.raises(ValueError, match="exactly 80"):
        CanonicalBSMImpliedVolatilityResult.from_mapping(wrong_iterations)

    failed_with_iv = dict(
        valid,
        status=BSM_IV_NO_BRACKET,
        implied_volatility="0.20000000",
        iterations=0,
    )
    with pytest.raises(ValueError, match="null IV"):
        CanonicalBSMImpliedVolatilityResult.from_mapping(failed_with_iv)

    missing = dict(valid)
    missing.pop("method_id")
    with pytest.raises(ValueError, match="missing or extra"):
        CanonicalBSMImpliedVolatilityResult.from_mapping(missing)
