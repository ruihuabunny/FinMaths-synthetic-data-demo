from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
import json
from pathlib import Path
import re

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    BSM_METRIC_TARGET_ORDER,
    METRIC_SPECS,
    METRIC_SPECS_BY_TARGET,
    MetricSpec,
    get_metric_spec,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.prompt_renderer import (
    render_bsm_greeks_prompt,
    render_bsm_metric_prompt,
)


jsonschema = pytest.importorskip("jsonschema")

_ALL_OUTPUT_FIELDS = {
    "market_implied_volatility",
    "unit_delta",
    "unit_gamma",
    "unit_vega_1volpt",
    "unit_theta_1calendar_day",
    "unit_rho_1pct",
}
_RUNTIME = {
    "trusted_tools": [
        {"name": "query_greeks_underlying_market_v2", "max_calls": 1},
        {"name": "query_greeks_option_quotes_v2", "max_calls": 1},
        {"name": "submit_greeks_submission_v2", "max_calls": 1},
    ]
}


def _load_schema(repository_root: Path, spec: MetricSpec) -> dict:
    return json.loads(
        (repository_root / "schemas" / spec.schema_filename).read_text(
            encoding="utf-8"
        )
    )


def _canonical_value(spec: MetricSpec) -> str:
    if spec.decimal_constraint == "positive_decimal8":
        return "0.20000000"
    if spec.decimal_constraint == "nonnegative_decimal8":
        return "0.01000000"
    return "-0.01000000"


def _submission(spec: MetricSpec) -> dict:
    rows = []
    for index in range(1, 161):
        row = {
            "row_id": f"row_{index:06d}",
            spec.output_field: _canonical_value(spec),
        }
        if spec.needs_iv_status:
            row["iv_status"] = "CONVERGED_FIXED_ITERATIONS"
        rows.append(row)
    return {
        "task_id": f"{spec.task_id_prefix}{'a' * 24}",
        "submission_schema_version": spec.submission_schema_version,
        "method_id": spec.method_id,
        "status": "completed",
        "rows": rows,
    }


def test_registry_freezes_exactly_six_unique_targets() -> None:
    assert BSM_METRIC_TARGET_ORDER == (
        "iv",
        "delta",
        "gamma",
        "vega_1volpt",
        "theta_1calendar_day",
        "rho_1pct",
    )
    assert tuple(METRIC_SPECS_BY_TARGET) == BSM_METRIC_TARGET_ORDER
    assert tuple(spec.target for spec in METRIC_SPECS) == BSM_METRIC_TARGET_ORDER
    assert all(get_metric_spec(spec.target) is spec for spec in METRIC_SPECS)

    for field in (
        "target",
        "output_field",
        "variant_id",
        "method_id",
        "submission_schema_version",
        "schema_filename",
        "verifier_id",
        "task_id_prefix",
        "task_id_pattern",
    ):
        values = [getattr(spec, field) for spec in METRIC_SPECS]
        assert len(values) == len(set(values)) == 6

    for spec in METRIC_SPECS:
        assert spec.task_version == "1.0.0"
        assert spec.database_schema_version == (
            "bsm-market-metric-task-duckdb-v1.0.0"
        )
        assert re.fullmatch(spec.task_id_pattern, f"{spec.task_id_prefix}{'a' * 24}")
        assert spec.needs_iv_status is (spec.target == "iv")

    with pytest.raises(ValueError, match="unsupported"):
        get_metric_spec("not-a-target")
    with pytest.raises(FrozenInstanceError):
        METRIC_SPECS[0].target = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        METRIC_SPECS_BY_TARGET["changed"] = METRIC_SPECS[0]  # type: ignore[index]


def test_registry_contains_no_formula_or_oracle_material() -> None:
    registry_text = json.dumps(
        [asdict(spec) for spec in METRIC_SPECS], sort_keys=True
    ).casefold()
    for forbidden in (
        "d1 =",
        "d2 =",
        "cdf_d1",
        "operation_formula",
        "oracle_answer",
        "sampling_seed",
    ):
        assert forbidden not in registry_text


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_single_metric_schema_is_frozen_and_accepts_its_contract(
    repository_root: Path, spec: MetricSpec
) -> None:
    schema = _load_schema(repository_root, spec)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    submission = _submission(spec)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == spec.schema_filename
    assert schema["x-schema-version"] == spec.submission_schema_version
    assert schema["additionalProperties"] is False
    assert schema["properties"]["submission_schema_version"] == {
        "const": spec.submission_schema_version
    }
    assert schema["properties"]["method_id"] == {"const": spec.method_id}
    assert schema["properties"]["status"] == {"const": "completed"}
    assert schema["properties"]["task_id"]["pattern"] == spec.task_id_pattern

    rows_contract = schema["properties"]["rows"]
    assert rows_contract["minItems"] == rows_contract["maxItems"] == 160
    assert rows_contract["items"] is False
    assert len(rows_contract["prefixItems"]) == 160
    assert schema["$defs"]["resultRow"]["additionalProperties"] is False
    expected_row_fields = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_row_fields.add("iv_status")
    assert set(schema["$defs"]["resultRow"]["properties"]) == expected_row_fields
    assert set(schema["$defs"]["resultRow"]["required"]) == expected_row_fields
    validator.validate(submission)


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_single_metric_schema_rejects_shape_identity_and_order_drift(
    repository_root: Path, spec: MetricSpec
) -> None:
    validator = jsonschema.Draft202012Validator(_load_schema(repository_root, spec))

    top_extra = _submission(spec)
    top_extra["extra"] = True
    assert not validator.is_valid(top_extra)

    row_extra = _submission(spec)
    row_extra["rows"][0]["extra"] = "0.00000000"
    assert not validator.is_valid(row_extra)

    missing_row = _submission(spec)
    missing_row["rows"].pop()
    assert not validator.is_valid(missing_row)

    reordered = _submission(spec)
    reordered["rows"][0], reordered["rows"][1] = (
        reordered["rows"][1],
        reordered["rows"][0],
    )
    assert not validator.is_valid(reordered)

    for field in ("submission_schema_version", "method_id", "status", "task_id"):
        wrong_identity = _submission(spec)
        wrong_identity[field] = "wrong"
        assert not validator.is_valid(wrong_identity)


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_single_metric_schema_enforces_decimal8_sign_and_negative_zero(
    repository_root: Path, spec: MetricSpec
) -> None:
    validator = jsonschema.Draft202012Validator(_load_schema(repository_root, spec))

    negative_zero = _submission(spec)
    negative_zero["rows"][0][spec.output_field] = "-0.00000000"
    assert not validator.is_valid(negative_zero)

    wrong_precision = _submission(spec)
    wrong_precision["rows"][0][spec.output_field] = "0.1000000"
    assert not validator.is_valid(wrong_precision)

    if spec.decimal_constraint == "positive_decimal8":
        zero = _submission(spec)
        zero["rows"][0][spec.output_field] = "0.00000000"
        assert not validator.is_valid(zero)
        negative = _submission(spec)
        negative["rows"][0][spec.output_field] = "-0.10000000"
        assert not validator.is_valid(negative)
    elif spec.decimal_constraint == "nonnegative_decimal8":
        zero = _submission(spec)
        zero["rows"][0][spec.output_field] = "0.00000000"
        validator.validate(zero)
        negative = _submission(spec)
        negative["rows"][0][spec.output_field] = "-0.10000000"
        assert not validator.is_valid(negative)
    else:
        positive = _submission(spec)
        positive["rows"][0][spec.output_field] = "0.10000000"
        validator.validate(positive)
        negative = _submission(spec)
        negative["rows"][0][spec.output_field] = "-0.10000000"
        validator.validate(negative)


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_single_metric_schema_rejects_legacy_combined_rows(
    repository_root: Path, spec: MetricSpec
) -> None:
    validator = jsonschema.Draft202012Validator(_load_schema(repository_root, spec))
    combined = _submission(spec)
    for row in combined["rows"]:
        row.update(
            {
                "iv_status": "CONVERGED_FIXED_ITERATIONS",
                "market_implied_volatility": "0.20000000",
                "unit_delta": "0.50000000",
                "unit_gamma": "0.01000000",
                "unit_vega_1volpt": "0.10000000",
                "unit_theta_1calendar_day": "-0.01000000",
                "unit_rho_1pct": "0.10000000",
            }
        )
    assert not validator.is_valid(combined)


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.target)
def test_metric_prompt_is_single_target_and_keeps_the_numerical_contract(
    spec: MetricSpec,
) -> None:
    prompt = render_bsm_metric_prompt(spec, _RUNTIME)
    prompt_casefold = prompt.casefold()

    assert spec.output_field in prompt
    assert spec.unit_description in prompt
    for other_field in _ALL_OUTPUT_FIELDS - {spec.output_field}:
        assert other_field not in prompt
    assert ("`iv_status`" in prompt) is spec.needs_iv_status
    assert prompt.count("query_greeks_underlying_market_v2") == 1
    assert prompt.count("query_greeks_option_quotes_v2") == 1
    assert prompt.count("submit_greeks_submission_v2") == 1
    for required in (
        "one unit of the option",
        "do not apply the contract multiplier",
        "bid/ask midpoint using decimal arithmetic",
        "then cast that midpoint once to binary64",
        "exactly 80 bisection updates",
        "[1e-6, 5.0]",
        "actual365fixed",
        "quote-level q-measure",
        "round_half_even",
        "preserve the queried option row order",
    ):
        assert required in prompt_casefold
    for forbidden in (
        "d1 =",
        "d2 =",
        "cdf_d1",
        "analytic_operation_formula",
        "oracle_answer",
        "sampling_seed",
        "reference_solver",
    ):
        assert forbidden not in prompt_casefold

    if spec.target == "iv":
        assert "internal calculation checkpoint" not in prompt_casefold
    else:
        assert "internal calculation checkpoint" in prompt_casefold
        assert "do not include it or its convergence status" in prompt_casefold
        assert "unrounded binary64 implied volatility" in prompt_casefold


def test_metric_prompt_rejects_tool_schedule_drift() -> None:
    changed_runtime = json.loads(json.dumps(_RUNTIME))
    changed_runtime["trusted_tools"][0]["max_calls"] = 2
    with pytest.raises(ValueError, match="trusted-tool schedule"):
        render_bsm_metric_prompt(METRIC_SPECS[0], changed_runtime)

    duplicate_runtime = json.loads(json.dumps(_RUNTIME))
    duplicate_runtime["trusted_tools"].append(
        duplicate_runtime["trusted_tools"][0]
    )
    with pytest.raises(ValueError, match="trusted-tool schedule"):
        render_bsm_metric_prompt(METRIC_SPECS[0], duplicate_runtime)


def test_legacy_combined_prompt_bytes_are_unchanged() -> None:
    import hashlib

    prompt = render_bsm_greeks_prompt(_RUNTIME)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "ddca000ae937d9ba333b8bbd8dce93e3a1a76fddea858f891b2f23ff11953b67"
    )
