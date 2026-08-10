"""Frozen identities and canonical JSON for the BSM Greeks agent package."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.tasks.bsm_greeks import (
    BSM_ANALYTIC_GREEKS_METHOD_ID,
    NUMERAIRE_ID,
    RISK_NEUTRAL_MEASURE_ID,
    analytic_bsm_greeks_contract,
)
from synthetic_derivatives.tasks.bsm_implied_volatility import (
    BSM_IV_METHOD_ID,
    bsm_iv_contract,
)
from synthetic_derivatives.tasks.bsm_market_greeks import (
    BSM_MARKET_GREEKS_METHOD_ID,
    BSM_MARKET_GREEKS_OUTPUT_CONTRACT_ID,
    BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION,
    BSM_MARKET_GREEKS_VARIANT_ID,
)


PACKAGE_SCHEMA_VERSION = "agent-task-package-v1.0.0"
RUNTIME_CONTRACT_SCHEMA_VERSION = "agent-task-runtime-contract-v1.0.0"
TRAJECTORY_SCHEMA_VERSION = "agent-task-trajectory-v1.0.0"
BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION = "bsm-greeks-task-duckdb-v1.0.0"
BSM_MARKET_GREEKS_VERIFIER_ID = (
    "quantlib-bsm-market-implied-greeks-verifier-v1"
)
ORACLE_CONFIG_SCHEMA_VERSION = "bsm-greeks-oracle-config-v1.0.0"
SELECTION_POLICY_ID = "two-nearest-expiries-five-abs-log-forward-moneyness-pairs-v1"
LOGICAL_CHECKSUM_ID = "sha256-bsm-greeks-canonical-logical-rows-v1"
EXPECTED_COORDINATES = {"L": 5, "P": 0, "M": 0, "A": 1, "D": 4, "R": 1, "F": "F0"}


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON with the single package-wide deterministic representation."""

    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8").rstrip("\n")


def digest_json(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def digest_file(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def load_json_object(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON contract must be an object: {path}")
    return raw


def market_greeks_method_contract() -> dict[str, Any]:
    """Return the combined visible-price IV and unit-Greeks contract."""

    analytic = analytic_bsm_greeks_contract()
    return {
        "contract_id": BSM_MARKET_GREEKS_METHOD_ID,
        "variant_id": BSM_MARKET_GREEKS_VARIANT_ID,
        "output_contract_id": BSM_MARKET_GREEKS_OUTPUT_CONTRACT_ID,
        "pricing_measure_id": RISK_NEUTRAL_MEASURE_ID,
        "numeraire_id": NUMERAIRE_ID,
        "probability_measure": "Q associated with the USD money-market numeraire",
        "economic_object": "ex_dividend_spot",
        "state_variables": ["ex_dividend_spot"],
        "conditioning_information": (
            "valuation_date_spot_contract_flat_public_curves_and_visible_quote"
        ),
        "time_axis": "calendar_time",
        "day_count": "Actual365Fixed",
        "pricing_law": "exact_analytic_constant_parameter_european_bsm",
        "risk_neutral_dynamics": "dS_t/S_t=(r-q)dt+sigma_IV*dW_t^Q",
        "iv_inversion": bsm_iv_contract(),
        "greeks_method_id": BSM_ANALYTIC_GREEKS_METHOD_ID,
        "normal_cdf": analytic["solver_normal_cdf"],
        "normal_pdf": analytic["solver_normal_pdf"],
        "greeks": {
            "unit_delta": analytic["greeks"]["delta"],
            "unit_gamma": analytic["greeks"]["gamma"],
            "unit_vega_1volpt": analytic["greeks"]["vega"],
            "unit_theta_1calendar_day": analytic["greeks"]["theta"],
            "unit_rho_1pct": analytic["greeks"]["rho"],
        },
        "greeks_sigma_checkpoint": (
            "unrounded_binary64_final_root_after_exactly_80_updates"
        ),
        "contract_multiplier_policy": "unit_option_not_applied",
        "cross_asset_dependence_policy": (
            "provenance_only_not_an_input_to_marginal_vanilla_price_iv_or_greeks"
        ),
        "input_dtype": {
            "quotes": "DECIMAL(24,8)",
            "observed_price": "Decimal_midpoint_then_one_binary64_cast",
            "model_arithmetic": "IEEE-754-binary64",
        },
        "output_precision": 8,
        "rounding": "ROUND_HALF_EVEN",
        "float_to_decimal": "Decimal(str(binary64))",
        "negative_zero": "canonical_positive_zero",
        "row_order": [
            "valuation_date",
            "underlying_id",
            "expiry",
            "strike",
            "call_put",
            "option_id",
        ],
        "success_iv_status": "CONVERGED_FIXED_ITERATIONS",
        "publication_gate": "all_rows_must_have_a_finite_closed_bracket_root",
        "persisted_intermediates": [],
    }


def oracle_config() -> dict[str, Any]:
    """Return the public-method-only trusted verifier configuration."""

    return {
        "oracle_config_schema_version": ORACLE_CONFIG_SCHEMA_VERSION,
        "verifier_id": BSM_MARKET_GREEKS_VERIFIER_ID,
        "quantlib_version": "1.39",
        "pricing_engine": "QuantLib.AnalyticEuropeanEngine",
        "iv_method_id": BSM_IV_METHOD_ID,
        "greeks_method_id": BSM_ANALYTIC_GREEKS_METHOD_ID,
        "combined_method_id": BSM_MARKET_GREEKS_METHOD_ID,
        "pricing_measure_id": RISK_NEUTRAL_MEASURE_ID,
        "numeraire_id": NUMERAIRE_ID,
        "day_count": "Actual365Fixed",
        "rate_compounding": "continuous",
        "iterations": 80,
        "volatility_bracket": [0.000001, 5.0],
        "canonical_precision": 8,
        "canonical_rounding": "ROUND_HALF_EVEN",
    }


def validate_package_config(raw: Mapping[str, Any]) -> None:
    """Reject drift in the small authoring-side golden-package selector."""

    expected = {
        "package_config_version",
        "variant_id",
        "task_family",
        "task_version",
        "coordinates",
        "valuation_date",
        "private_selection",
        "method_id",
        "iv_method_id",
        "greeks_method_id",
        "package_schema_version",
        "public_database_schema_version",
        "submission_schema",
        "runtime_schema",
        "trajectory_schema",
        "oracle_config_schema",
        "global_capability_profile",
        "task_capability_overlay",
        "release_profile",
    }
    if set(raw) != expected:
        raise ValueError("package config has missing or extra fields")
    fixed = {
        "package_config_version": (
            "bsm-market-implied-greeks-package-config-v1.0.0"
        ),
        "variant_id": BSM_MARKET_GREEKS_VARIANT_ID,
        "task_family": "bsm_greeks",
        "task_version": "1.0.0",
        "coordinates": EXPECTED_COORDINATES,
        "method_id": BSM_MARKET_GREEKS_METHOD_ID,
        "iv_method_id": BSM_IV_METHOD_ID,
        "greeks_method_id": BSM_ANALYTIC_GREEKS_METHOD_ID,
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "public_database_schema_version": (
            BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION
        ),
        "release_profile": "golden-single-task-v1",
    }
    if any(raw[field] != value for field, value in fixed.items()):
        raise ValueError("package config changes a frozen identity")
    selection = raw["private_selection"]
    if not isinstance(selection, Mapping) or selection != {
        "sampling_seed": 17,
        "underlying_count": 8,
        "live_expiry_count": 2,
        "strike_count_per_expiry": 5,
        "strike_selection": "absolute_log_forward_moneyness_then_strike",
        "retain_call_put_pairs": True,
    }:
        raise ValueError("package config changes the golden selector")


__all__ = [
    "BSM_MARKET_GREEKS_DATABASE_SCHEMA_VERSION",
    "BSM_MARKET_GREEKS_METHOD_ID",
    "BSM_MARKET_GREEKS_OUTPUT_CONTRACT_ID",
    "BSM_MARKET_GREEKS_SUBMISSION_SCHEMA_VERSION",
    "BSM_MARKET_GREEKS_VARIANT_ID",
    "BSM_MARKET_GREEKS_VERIFIER_ID",
    "EXPECTED_COORDINATES",
    "LOGICAL_CHECKSUM_ID",
    "ORACLE_CONFIG_SCHEMA_VERSION",
    "PACKAGE_SCHEMA_VERSION",
    "RUNTIME_CONTRACT_SCHEMA_VERSION",
    "SELECTION_POLICY_ID",
    "TRAJECTORY_SCHEMA_VERSION",
    "canonical_json_bytes",
    "canonical_json_text",
    "digest_file",
    "digest_json",
    "load_json_object",
    "market_greeks_method_contract",
    "oracle_config",
    "validate_package_config",
]
