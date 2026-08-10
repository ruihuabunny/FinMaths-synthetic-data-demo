from __future__ import annotations

import json
from pathlib import Path

from synthetic_derivatives.packaging.contracts import (
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION,
    BSM_MARKET_GREEKS_VARIANT_ID,
    EXPECTED_COORDINATES,
    market_greeks_method_contract,
    oracle_config,
    validate_package_config,
)


def test_five_package_interfaces_have_frozen_ids_and_strict_shapes(
    repository_root: Path,
) -> None:
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in (
            repository_root / "schemas/agent-task-package-v1.schema.json",
            repository_root
            / "schemas/agent-task-runtime-contract-v1.schema.json",
            repository_root / "schemas/agent-task-trajectory-v1.schema.json",
            repository_root / "schemas/bsm-greeks-submission-v1.schema.json",
            repository_root / "schemas/bsm-greeks-oracle-config-v1.schema.json",
        )
    }

    assert all(schema["additionalProperties"] is False for schema in schemas.values())
    submission = schemas["bsm-greeks-submission-v1.schema.json"]
    assert submission["x-schema-version"] == (
        BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION
    )
    assert submission["properties"]["method_id"]["const"] == (
        BSM_MARKET_GREEKS_METHOD_ID
    )
    result_fields = submission["$defs"]["resultRow"]["properties"]
    assert {"d1", "d2", "price", "contract_multiplier"}.isdisjoint(result_fields)
    assert set(result_fields) == {
        "row_id",
        "iv_status",
        "market_implied_volatility",
        "unit_delta",
        "unit_gamma",
        "unit_vega_1volpt",
        "unit_theta_1calendar_day",
        "unit_rho_1pct",
    }


def test_package_config_and_method_contract_freeze_the_mathematical_object(
    repository_root: Path,
) -> None:
    config = json.loads(
        (
            repository_root
            / "configs/task_packages/bsm_market_implied_greeks_v1.json"
        ).read_text(encoding="utf-8")
    )
    validate_package_config(config)
    contract = market_greeks_method_contract()

    assert config["variant_id"] == BSM_MARKET_GREEKS_VARIANT_ID
    assert config["coordinates"] == EXPECTED_COORDINATES
    assert config["coordinates"]["D"] == 4
    assert contract["pricing_measure_id"] == "USD-MONEY-MARKET-Q-v1"
    assert contract["numeraire_id"] == "USD-MONEY-MARKET-ACCOUNT-v1"
    assert contract["risk_neutral_dynamics"] == (
        "dS_t/S_t=(r-q)dt+sigma_IV*dW_t^Q"
    )
    assert contract["pricing_law"] == (
        "exact_analytic_constant_parameter_european_bsm"
    )
    assert contract["iv_inversion"]["iterations"] == 80
    assert contract["iv_inversion"]["early_stop"] is False
    assert contract["greeks_sigma_checkpoint"] == (
        "unrounded_binary64_final_root_after_exactly_80_updates"
    )
    assert contract["contract_multiplier_policy"] == "unit_option_not_applied"
    assert contract["persisted_intermediates"] == []
    assert oracle_config()["combined_method_id"] == BSM_MARKET_GREEKS_METHOD_ID
