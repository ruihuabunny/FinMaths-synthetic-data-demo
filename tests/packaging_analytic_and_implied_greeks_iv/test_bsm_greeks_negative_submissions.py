from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import load_bsm_greeks_inputs
from synthetic_derivatives.verifier.bsm_market_greeks import (
    trusted_market_greeks_submission,
    verify_market_greeks_submission,
)


def _fixture(packaged_bsm_greeks):
    root = packaged_bsm_greeks.package.package_root
    submission = json.loads(
        (root / "reference/final_submission.json").read_text(encoding="utf-8")
    )
    inputs = load_bsm_greeks_inputs(root / "public/task.duckdb")
    return inputs, submission


def _different_last_place(value: str) -> str:
    digit = "1" if value[-1] != "1" else "2"
    return value[:-1] + digit


@pytest.mark.parametrize(
    "field",
    [
        "market_implied_volatility",
        "unit_delta",
        "unit_gamma",
        "unit_vega_1volpt",
        "unit_theta_1calendar_day",
        "unit_rho_1pct",
    ],
)
def test_verifier_rejects_any_last_decimal_change(
    packaged_bsm_greeks, field: str
) -> None:
    inputs, submission = _fixture(packaged_bsm_greeks)
    changed = deepcopy(submission)
    changed["rows"][0][field] = _different_last_place(
        changed["rows"][0][field]
    )

    with pytest.raises(ValueError, match="mismatch"):
        verify_market_greeks_submission(inputs, changed)


def test_verifier_rejects_wrong_units_theta_sign_and_contract_multiplier(
    packaged_bsm_greeks,
) -> None:
    inputs, submission = _fixture(packaged_bsm_greeks)
    cases = []
    unscaled_vega = deepcopy(submission)
    unscaled_vega["rows"][0]["unit_vega_1volpt"] = format(
        Decimal(unscaled_vega["rows"][0]["unit_vega_1volpt"]) * 100,
        ".8f",
    )
    cases.append(unscaled_vega)
    unscaled_rho = deepcopy(submission)
    unscaled_rho["rows"][0]["unit_rho_1pct"] = format(
        Decimal(unscaled_rho["rows"][0]["unit_rho_1pct"]) * 100,
        ".8f",
    )
    cases.append(unscaled_rho)
    annual_theta = deepcopy(submission)
    annual_theta["rows"][0]["unit_theta_1calendar_day"] = format(
        Decimal(annual_theta["rows"][0]["unit_theta_1calendar_day"]) * 365,
        ".8f",
    )
    cases.append(annual_theta)
    wrong_sign = deepcopy(submission)
    theta = Decimal(wrong_sign["rows"][0]["unit_theta_1calendar_day"])
    wrong_sign["rows"][0]["unit_theta_1calendar_day"] = format(-theta, ".8f")
    cases.append(wrong_sign)
    multiplied = deepcopy(submission)
    multiplied["rows"][0]["unit_delta"] = format(
        Decimal(multiplied["rows"][0]["unit_delta"])
        * Decimal(str(inputs[0].contract_multiplier)),
        ".8f",
    )
    cases.append(multiplied)

    for changed in cases:
        with pytest.raises(ValueError, match="mismatch"):
            verify_market_greeks_submission(inputs, changed)


def test_verifier_rejects_call_put_mixup_and_row_shape_attacks(
    packaged_bsm_greeks,
) -> None:
    inputs, submission = _fixture(packaged_bsm_greeks)
    cases = []
    swapped_values = deepcopy(submission)
    first_id = swapped_values["rows"][0]["row_id"]
    second_id = swapped_values["rows"][1]["row_id"]
    first_values = deepcopy(swapped_values["rows"][0])
    second_values = deepcopy(swapped_values["rows"][1])
    swapped_values["rows"][0] = dict(second_values, row_id=first_id)
    swapped_values["rows"][1] = dict(first_values, row_id=second_id)
    cases.append(swapped_values)
    wrong_order = deepcopy(submission)
    wrong_order["rows"][0], wrong_order["rows"][1] = (
        wrong_order["rows"][1],
        wrong_order["rows"][0],
    )
    cases.append(wrong_order)
    missing = deepcopy(submission)
    missing["rows"].pop()
    cases.append(missing)
    duplicate = deepcopy(submission)
    duplicate["rows"][1] = deepcopy(duplicate["rows"][0])
    cases.append(duplicate)
    extra_field = deepcopy(submission)
    extra_field["rows"][0]["d1"] = "0.00000000"
    cases.append(extra_field)
    private_field = deepcopy(submission)
    private_field["rows"][0]["oracle_answer"] = "copied"
    cases.append(private_field)
    wrong_method = deepcopy(submission)
    wrong_method["method_id"] = "different-method"
    cases.append(wrong_method)

    for changed in cases:
        with pytest.raises(ValueError):
            verify_market_greeks_submission(inputs, changed)


def test_verifier_rejects_outputs_from_wrong_rate_dividend_or_day_count(
    packaged_bsm_greeks,
) -> None:
    inputs, _ = _fixture(packaged_bsm_greeks)
    changed_input_sets = [
        (
            replace(inputs[0], risk_free_rate=inputs[0].risk_free_rate + 0.001),
            *inputs[1:],
        ),
        (
            replace(inputs[0], dividend_yield=inputs[0].dividend_yield + 0.001),
            *inputs[1:],
        ),
        tuple(
            replace(
                row,
                expiry=row.expiry + timedelta(days=1),
                time_to_expiry_actual365=(
                    (row.expiry + timedelta(days=1) - row.valuation_date).days
                    / 365.0
                ),
            )
            for row in inputs
        ),
    ]

    for changed_inputs in changed_input_sets:
        wrong = trusted_market_greeks_submission(changed_inputs).to_dict()
        with pytest.raises(ValueError, match="mismatch"):
            verify_market_greeks_submission(inputs, wrong)


def test_combined_verifier_source_uses_quantlib_path_and_no_tolerance(
    repository_root,
) -> None:
    source = (
        repository_root
        / "src/synthetic_derivatives/verifier/bsm_market_greeks.py"
    ).read_text(encoding="utf-8")

    assert "trusted_quantlib_values" in source
    assert "synthetic_derivatives.solver" not in source
    assert "isclose" not in source
    assert "pytest.approx" not in source
    assert "actual.to_dict() != expected.to_dict()" in source
