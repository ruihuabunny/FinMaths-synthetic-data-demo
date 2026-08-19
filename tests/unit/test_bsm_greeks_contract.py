from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import re

import pytest

from synthetic_derivatives.tasks.bsm_greeks import (
    BSM_ANALYTIC_GREEKS_METHOD_ID,
    BSM_ANALYTIC_GREEKS_VARIANT_ID,
    BSM_GREEKS_CONVENTION_ID,
    BSM_GREEKS_OUTPUT_CONTRACT_ID,
    BSM_GREEKS_SCHEMA_VERSION,
    BSMGreeksInput,
    BSMGreeksValues,
    CanonicalBSMGreeksResult,
    analytic_bsm_greeks_contract,
    analytic_bsm_greeks_contract_json,
    canonical_decimal,
    canonicalize_bsm_greeks,
)


def _valid_input() -> BSMGreeksInput:
    return BSMGreeksInput(
        task_id="task-1",
        valuation_date=date(2026, 1, 2),
        underlying_id="SYN-USD",
        option_id="SYN-USD-20260109-C-100",
        call_put="call",
        spot=100.0,
        strike=100.0,
        expiry=date(2026, 1, 9),
        time_to_expiry_actual365=7.0 / 365.0,
        risk_free_rate=0.03,
        dividend_yield=0.01,
        sigma=0.2,
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("spot", 0.0, "spot must be positive"),
        ("spot", -1.0, "spot must be positive"),
        ("strike", 0.0, "strike must be positive"),
        ("sigma", 0.0, "sigma must be positive"),
        ("time_to_expiry_actual365", 0.0, "must be positive"),
        ("spot", float("nan"), "spot must be finite"),
        ("strike", float("inf"), "strike must be finite"),
        ("sigma", float("-inf"), "sigma must be finite"),
        ("risk_free_rate", float("nan"), "risk_free_rate must be finite"),
        ("dividend_yield", float("inf"), "dividend_yield must be finite"),
    ],
)
def test_input_rejects_invalid_numeric_domain(
    field_name: str,
    invalid_value: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_valid_input(), **{field_name: invalid_value})


def test_input_requires_exact_actual365_calendar_time() -> None:
    valid = _valid_input()

    with pytest.raises(ValueError, match="calendar days / 365"):
        replace(valid, time_to_expiry_actual365=8.0 / 365.0)
    with pytest.raises(ValueError, match="expiry must be after"):
        replace(
            valid,
            expiry=valid.valuation_date,
            time_to_expiry_actual365=1.0 / 365.0,
        )
    with pytest.raises(ValueError, match="datetime.date"):
        replace(valid, valuation_date=datetime(2026, 1, 2))


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("call_put", "CALL", "call_put"),
        ("exercise_style", "american", "European exercise"),
        ("settlement_type", "physical", "cash settlement"),
        ("currency", "EUR", "require USD"),
    ],
)
def test_input_rejects_contract_convention_changes(
    field_name: str,
    invalid_value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_valid_input(), **{field_name: invalid_value})


def test_negative_finite_continuous_rates_are_in_the_bsm_domain() -> None:
    row = replace(
        _valid_input(),
        risk_free_rate=-0.01,
        dividend_yield=-0.02,
    )

    assert row.risk_free_rate == -0.01
    assert row.dividend_yield == -0.02


def test_canonical_decimal_uses_half_even_and_removes_negative_zero() -> None:
    assert canonical_decimal(1.234567885) == "1.23456788"
    assert canonical_decimal(1.234567895) == "1.23456790"
    assert canonical_decimal(-0.0) == "0.00000000"


def test_canonical_result_has_only_frozen_public_fields() -> None:
    result = canonicalize_bsm_greeks(
        _valid_input(),
        BSMGreeksValues(
            price=1.0,
            delta=0.5,
            gamma=0.01,
            vega=0.02,
            theta=-0.03,
            rho=0.04,
        ),
    )
    expected_fields = {
        "task_id",
        "valuation_date",
        "underlying_id",
        "option_id",
        "call_put",
        "expiry",
        "strike",
        "price",
        "delta",
        "gamma",
        "vega",
        "theta",
        "rho",
        "method_id",
        "convention_id",
        "output_contract_id",
    }

    assert set(result.to_dict()) == expected_fields
    assert {"d1", "d2"}.isdisjoint(result.to_dict())
    assert result.method_id == BSM_ANALYTIC_GREEKS_METHOD_ID
    assert result.convention_id == BSM_GREEKS_CONVENTION_ID
    assert result.output_contract_id == BSM_GREEKS_OUTPUT_CONTRACT_ID
    assert CanonicalBSMGreeksResult.from_mapping(result.to_dict()) == result


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("strike", "0.00000000"),
        ("price", "-1.00000000"),
        ("gamma", "-0.00000001"),
        ("vega", "-0.00000001"),
        ("delta", "-0.00000000"),
    ],
)
def test_canonical_result_rejects_invalid_published_values(
    field_name: str,
    invalid_value: str,
) -> None:
    baseline = canonicalize_bsm_greeks(
        _valid_input(),
        BSMGreeksValues(1.0, 0.5, 0.01, 0.02, -0.03, 0.04),
    ).to_dict()
    baseline[field_name] = invalid_value

    with pytest.raises(ValueError):
        CanonicalBSMGreeksResult.from_mapping(baseline)


def test_contract_freezes_measure_units_operation_order_and_serialization() -> None:
    contract = analytic_bsm_greeks_contract()

    assert contract["variant_id"] == BSM_ANALYTIC_GREEKS_VARIANT_ID
    assert contract["pricing_measure_id"] == "USD-MONEY-MARKET-Q-v1"
    assert contract["numeraire_id"] == "USD-MONEY-MARKET-ACCOUNT-v1"
    assert contract["transition_or_pricing_law"] == (
        "exact_analytic_constant_parameter_bsm"
    )
    assert contract["sigma_semantics"].startswith("annualized_Q_")
    assert contract["greeks"]["vega"]["scale"] == (
        "per_0.01_absolute_volatility"
    )
    assert contract["greeks"]["theta"]["scale"] == "per_1_calendar_day"
    assert contract["greeks"]["rho"]["scale"] == (
        "per_0.01_absolute_rate"
    )
    assert "expiry" in contract["greeks"]["theta"]["holding_fixed"]
    assert contract["operation_order_id"].endswith("operation-order-v1")
    assert contract["operation_sequence"][-3:] == [
        "unscaled_price_and_greeks",
        "vega_rho_0.01_scaling",
        "theta_365_scaling",
    ]
    formulas = contract["operation_formulas"]
    assert formulas["common"][5:8] == [
        "standardized_drift = (carry + 0.5 * variance) * tau",
        "d1 = (log_moneyness + standardized_drift) / root_variance",
        "d2 = d1 - root_variance",
    ]
    assert formulas["call"][-2] == (
        "theta_per_year = diffusion_theta - r * discounted_strike * cdf_d2 "
        "+ q * discounted_spot * cdf_d1"
    )
    assert formulas["put"][-2] == (
        "theta_per_year = diffusion_theta + r * discounted_strike * "
        "cdf_minus_d2 - q * discounted_spot * cdf_minus_d1"
    )
    assert formulas["scaling"] == [
        "unit_vega_1volpt = 0.01 * vega_per_unit",
        "unit_theta_1calendar_day = theta_per_year / 365.0",
        "unit_rho_1pct = 0.01 * rho_per_unit",
    ]
    assert contract["row_order"] == [
        "valuation_date",
        "underlying_id",
        "expiry",
        "strike",
        "call_put",
        "option_id",
    ]
    assert contract["persisted_intermediates"] == []
    assert json.loads(analytic_bsm_greeks_contract_json()) == contract
    assert analytic_bsm_greeks_contract_json() == (
        analytic_bsm_greeks_contract_json()
    )


def test_variant_config_and_output_schema_match_the_runtime_contract(
    repository_root: Path,
) -> None:
    config = json.loads(
        (repository_root / "configs/variants/bsm_analytic_greeks_v1.json").read_text(
            encoding="utf-8"
        )
    )
    schema = json.loads(
        (repository_root / "schemas/bsm-greeks-output.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["variant_id"] == BSM_ANALYTIC_GREEKS_VARIANT_ID
    assert config["coordinates"] == {
        "L": 1,
        "P": 0,
        "M": 0,
        "A": 0,
        "D": 1,
        "R": 1,
        "F": "F0",
    }
    assert config["method_id"] == BSM_ANALYTIC_GREEKS_METHOD_ID
    assert config["convention_id"] == BSM_GREEKS_CONVENTION_ID
    assert config["output_contract_id"] == BSM_GREEKS_OUTPUT_CONTRACT_ID
    assert config["persisted_intermediates"] == []
    assert schema["x-schema-version"] == BSM_GREEKS_SCHEMA_VERSION
    assert set(schema["required"]) == set(schema["properties"])
    assert {"d1", "d2"}.isdisjoint(schema["properties"])
    assert schema["properties"]["method_id"]["const"] == (
        BSM_ANALYTIC_GREEKS_METHOD_ID
    )
    assert re.fullmatch(
        schema["properties"]["strike"]["pattern"], "100.00000000"
    )
    assert not re.fullmatch(
        schema["properties"]["strike"]["pattern"], "0.00000000"
    )


def test_canonical_mapping_requires_exact_field_set() -> None:
    mapping = canonicalize_bsm_greeks(
        _valid_input(),
        BSMGreeksValues(1.0, 0.5, 0.01, 0.02, -0.03, 0.04),
    ).to_dict()

    missing = dict(mapping)
    missing.pop("rho")
    with pytest.raises(ValueError, match="missing or extra"):
        CanonicalBSMGreeksResult.from_mapping(missing)

    extra = dict(mapping)
    extra["latent_pricing_term"] = Decimal("1")
    with pytest.raises(ValueError, match="missing or extra"):
        CanonicalBSMGreeksResult.from_mapping(extra)
