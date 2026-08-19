from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS_DB_QUERY_V3,
    MetricSpec,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.prompt_renderer import (
    render_bsm_metric_prompt_v3,
)


jsonschema = pytest.importorskip("jsonschema")

_RUNTIME_V3 = {
    "host_protocol": "read-only-duckdb-query-schema-submit-v3",
    "trusted_tools": [
        {"name": "query_public_duckdb_v3", "max_calls": 10},
        {"name": "submit_greeks_submission_v3", "max_calls": 1},
    ],
    "resource_budget": {
        "trusted_query_calls": 10,
        "submission_calls": 1,
        "query_result_rows": 1000,
        "query_result_bytes": 1048576,
        "query_timeout_seconds": 5,
        "duckdb_memory_mib": 256,
    },
}
_OUTPUT_FIELDS = {
    "market_implied_volatility",
    "unit_delta",
    "unit_gamma",
    "unit_vega_1volpt",
    "unit_theta_1calendar_day",
    "unit_rho_1pct",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_value(spec: MetricSpec) -> str:
    if spec.decimal_constraint == "positive_decimal8":
        return "0.20000000"
    if spec.decimal_constraint == "nonnegative_decimal8":
        return "0.01000000"
    return "-0.01000000"


def _submission(spec: MetricSpec, row_ids: tuple[str, ...]) -> dict:
    rows = []
    for row_id in row_ids:
        row = {"row_id": row_id, spec.output_field: _canonical_value(spec)}
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


@pytest.mark.parametrize(
    "spec", METRIC_SPECS_DB_QUERY_V3, ids=lambda spec: spec.target
)
def test_v3_prompt_preserves_math_and_hides_database_layout(
    spec: MetricSpec,
) -> None:
    prompt = render_bsm_metric_prompt_v3(spec, _RUNTIME_V3)
    folded = prompt.casefold()

    assert spec.output_field in prompt
    assert spec.method_id in prompt
    assert spec.unit_description in prompt
    assert prompt.count("query_public_duckdb_v3") == 1
    assert prompt.count("submit_greeks_submission_v3") == 1
    for other_field in _OUTPUT_FIELDS - {spec.output_field}:
        assert other_field not in prompt
    for required in (
        "european black–scholes–merton",
        "pricing measure",
        "money-market numeraire",
        "annualized continuously compounded decimals",
        "actual365fixed",
        "bid/ask midpoint using decimal arithmetic",
        "cast that midpoint once to binary64",
        "exactly 80 bisection updates",
        "[1e-6, 5.0]",
        "one unit of the option",
        "do not apply the contract multiplier",
        "at least one and at most 10",
        "5-second execution limit",
        "256 mib duckdb memory limit",
        "1,000 rows and 1 mib",
        "1 gib of memory and 600 seconds",
        "row order is not semantically significant",
        "round_half_even",
        "0.00000000",
        "submission is final",
    ):
        assert required in folded
    for forbidden in (
        "solver_visible",
        "underlying_market_inputs",
        "option_quote_inputs",
        "query_greeks_underlying_market_v2",
        "query_greeks_option_quotes_v2",
        "task_id`, `snapshot_id`, `valuation_date`, and `underlying_id",
        "row_000001",
        "160 option",
        "8 underlying",
        "d1 =",
        "d2 =",
        "oracle_answer",
        "sampling_seed",
    ):
        assert forbidden not in folded


def test_v3_prompt_rejects_host_tool_and_limit_drift() -> None:
    spec = METRIC_SPECS_DB_QUERY_V3[0]
    changed_host = deepcopy(_RUNTIME_V3)
    changed_host["host_protocol"] = "wrong"
    with pytest.raises(ValueError, match="host protocol"):
        render_bsm_metric_prompt_v3(spec, changed_host)

    changed_tool = deepcopy(_RUNTIME_V3)
    changed_tool["trusted_tools"][0]["max_calls"] = 9
    with pytest.raises(ValueError, match="trusted-tool schedule"):
        render_bsm_metric_prompt_v3(spec, changed_tool)

    changed_limit = deepcopy(_RUNTIME_V3)
    changed_limit["resource_budget"]["query_result_bytes"] += 1
    with pytest.raises(ValueError, match="resource limits"):
        render_bsm_metric_prompt_v3(spec, changed_limit)


@pytest.mark.parametrize(
    "spec", METRIC_SPECS_DB_QUERY_V3, ids=lambda spec: spec.target
)
def test_v2_metric_submission_schema_is_structural_and_unordered(
    repository_root: Path, spec: MetricSpec
) -> None:
    schema = _load_json(repository_root / "schemas" / spec.schema_filename)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    rows_schema = schema["properties"]["rows"]
    serialized = json.dumps(schema, sort_keys=True).casefold()

    assert schema["$id"] == spec.schema_filename
    assert schema["x-schema-version"] == spec.submission_schema_version
    assert schema["properties"]["task_id"] == {
        "type": "string",
        "pattern": spec.task_id_pattern,
    }
    assert "prefixItems" not in rows_schema
    assert "maxItems" not in rows_schema
    assert rows_schema["items"] == {"$ref": "#/$defs/resultRow"}
    assert "row_000001" not in serialized
    assert "solver_visible" not in serialized
    assert "underlying_market_inputs" not in serialized
    assert "option_quote_inputs" not in serialized

    validator.validate(_submission(spec, ("row_000002", "row_000001")))
    validator.validate(_submission(spec, ("row_000001",)))
    validator.validate(_submission(spec, ("row_000001", "row_000001")))


def test_v3_common_json_schemas_are_valid(repository_root: Path) -> None:
    for filename in (
        "agent-task-portable-toolset-v3.schema.json",
        "agent-task-runtime-contract-v3.schema.json",
        "agent-task-package-v3.schema.json",
    ):
        schema = _load_json(repository_root / "schemas" / filename)
        jsonschema.Draft202012Validator.check_schema(schema)


def test_v3_toolset_schema_freezes_query_response_and_has_no_payload_copy(
    repository_root: Path,
) -> None:
    schema = _load_json(
        repository_root / "schemas/agent-task-portable-toolset-v3.schema.json"
    )
    serialized = json.dumps(schema, sort_keys=True)
    query = schema["$defs"]["duckdbQuery"]
    response = schema["$defs"]["queryResponseSchema"]["const"]
    serialization = schema["$defs"]["queryContract"]["properties"][
        "serialization"
    ]

    assert "payload_path" not in serialized
    assert "options.json" not in serialized
    assert "underlyings.json" not in serialized
    assert "response_schema" in query["required"]
    assert response["additionalProperties"] is False
    assert set(response["required"]) == {
        "columns",
        "rows",
        "row_count",
        "truncated",
    }
    assert response["properties"]["rows"]["maxItems"] == 1000
    assert serialization["properties"]["integer"] == {
        "const": "json-number"
    }
    assert serialization["properties"]["boolean"] == {
        "const": "json-boolean"
    }


def test_v3_package_schema_accepts_leaf_and_rejects_metric_identity_mix(
    repository_root: Path,
) -> None:
    schema = _load_json(repository_root / "schemas/agent-task-package-v3.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    spec = METRIC_SPECS_DB_QUERY_V3[0]
    digest = "a" * 64
    manifest = {
        "package_schema_version": "agent-task-package-v3.0.0",
        "task_interface_version": "bsm-market-metric-agent-task-v2.0.0",
        "delivery_status": "PORTABLE_SUITE_VERIFIED",
        "task_family": "bsm_greeks",
        "task_id": f"{spec.task_id_prefix}{'b' * 24}",
        "task_version": spec.task_version,
        "target_metric": spec.target,
        "variant_id": spec.variant_id,
        "method_id": spec.method_id,
        "source_task_id": f"bsm-mig-v2-{'c' * 24}",
        "source_manifest_digest": digest,
        "public_child_snapshot": {
            "snapshot_id": f"BSM-MARKET-METRIC-{'D' * 24}",
            "revision": 1,
            "logical_checksum": digest,
        },
        "database": {
            "schema_version": spec.database_schema_version,
            "snapshot_id": f"BSM-MARKET-METRIC-{'D' * 24}",
            "file_digest": digest,
            "logical_checksum": digest,
            "market_content_digest": digest,
        },
        "evaluation_view": {
            "path": "evaluation_view",
            "manifest_digest": digest,
            "interface_digest": digest,
        },
        "toolset": {
            "path": "trusted_tools/toolset.json",
            "digest": digest,
        },
        "verifier": {
            "id": spec.verifier_id,
            "oracle_config_digest": digest,
            "runtime_digest": digest,
        },
        "artifacts": {"task.duckdb": digest},
        "artifact_visibility": {"task.duckdb": "tool_host_only"},
    }
    validator.validate(manifest)

    mixed = deepcopy(manifest)
    mixed["target_metric"] = "delta"
    assert not validator.is_valid(mixed)


def test_v3_capability_profiles_freeze_query_and_solver_limits(
    repository_root: Path,
) -> None:
    expected_tools = {
        "query_public_duckdb_v3": 10,
        "submit_greeks_submission_v3": 1,
    }
    expected_limits = {
        "vcpu": 1,
        "memory_mib": 1024,
        "wall_clock_seconds": 600,
        "trusted_query_calls": 10,
        "submission_calls": 1,
        "submission_bytes": 5242880,
        "query_result_rows": 1000,
        "query_result_bytes": 1048576,
        "query_timeout_seconds": 5,
        "duckdb_memory_mib": 256,
    }
    for filename in (
        "capabilities.global_v3.json",
        "capabilities.bsm_greeks_v3.json",
    ):
        profile = _load_json(repository_root / "environments/solver" / filename)
        assert profile["trusted_tools"] == expected_tools
        assert profile["resource_budget"] == expected_limits
        assert profile["filesystem"] == {
            "read": ["public/*"],
            "write": ["submission/*"],
        }
        assert profile["network"] is False
        assert profile["dynamic_installation"] is False
        assert profile["process_spawning"] is False
        for operation in (
            "raw_database_connection",
            "duckdb_write_or_external_access",
            "undeclared_filesystem_access",
            "network_access",
            "process_spawn",
            "dynamic_import",
            "dynamic_package_installation",
            "verifier_reference_or_private_access",
        ):
            assert operation in profile["denied_operations"]
