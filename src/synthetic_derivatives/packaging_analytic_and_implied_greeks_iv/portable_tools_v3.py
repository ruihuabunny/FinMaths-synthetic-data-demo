"""Portable trusted DuckDB-query tools for metric task interface v2."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Mapping

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
    digest_file,
    digest_json,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    assert_bsm_metric_database_safe,
    load_bsm_metric_query_payloads,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.duckdb_query_tools import (
    DuckDBQueryError,
    DuckDBQueryToolsV3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.leakage import (
    scan_solver_observable_content,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.metric_specs import (
    METRIC_SPECS_DB_QUERY_V3_BY_TARGET,
    MetricSpec,
    get_metric_spec_db_query_v3,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    CapabilityViolation,
    RuntimeReplayResult,
)


PORTABLE_TOOLSET_SCHEMA_VERSION_V3 = "bsm-greeks-portable-toolset-v3.0.0"
PORTABLE_TOOL_HOST_PROTOCOL_V3 = "read-only-duckdb-query-schema-submit-v3"
QUERY_PUBLIC_DUCKDB_V3 = "query_public_duckdb_v3"
SUBMIT_GREEKS_SUBMISSION_V3 = "submit_greeks_submission_v3"

_TOOLSET_FILES = {"toolset.json"}
_SIGNED_DECIMAL8 = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]{8}\Z")
_NONNEGATIVE_DECIMAL8 = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{8}\Z")
_ALLOWED_SCHEMAS = ["metadata", "solver_visible", "information_schema"]
_ALLOWED_RELATIONS = [
    "metadata.public_task",
    "solver_visible.underlying_market_inputs",
    "solver_visible.option_quote_inputs",
]
_SAFE_INFORMATION_SCHEMA_RELATIONS = ["columns", "schemata", "tables", "views"]
_QUERY_LIMITS = {
    "sql_characters": 20_000,
    "query_calls_minimum": 1,
    "query_calls_maximum": 10,
    "result_rows": 1_000,
    "result_bytes": 1_048_576,
    "timeout_seconds": 5,
    "memory_limit": "256MiB",
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _require_spec(spec: MetricSpec) -> MetricSpec:
    if not isinstance(spec, MetricSpec):
        raise TypeError("v3 metric toolset requires a MetricSpec")
    if METRIC_SPECS_DB_QUERY_V3_BY_TARGET.get(spec.target) != spec:
        raise ValueError("v3 metric toolset requires a frozen v3 registry entry")
    return spec


def _validate_runtime(runtime: Mapping[str, Any]) -> None:
    tools = runtime.get("trusted_tools")
    actual_tools = {
        str(item["name"]): int(item["max_calls"])
        for item in tools
        if isinstance(item, Mapping)
    } if isinstance(tools, list) else {}
    budget = runtime.get("resource_budget")
    if (
        runtime.get("runtime_contract_schema_version")
        != "agent-task-runtime-contract-v3.0.0"
        or runtime.get("host_protocol") != PORTABLE_TOOL_HOST_PROTOCOL_V3
        or actual_tools
        != {QUERY_PUBLIC_DUCKDB_V3: 10, SUBMIT_GREEKS_SUBMISSION_V3: 1}
        or not isinstance(budget, Mapping)
        or budget.get("trusted_query_calls") != 10
        or budget.get("submission_calls") != 1
        or budget.get("submission_bytes") != 5_242_880
        or budget.get("query_result_rows") != 1_000
        or budget.get("query_result_bytes") != 1_048_576
        or budget.get("query_timeout_seconds") != 5
        or budget.get("duckdb_memory_mib") != 256
    ):
        raise ValueError("v3 runtime trusted-tool contract changed")


def _query_contract() -> dict[str, Any]:
    return {
        "allowed_schemas": list(_ALLOWED_SCHEMAS),
        "allowed_relations": list(_ALLOWED_RELATIONS),
        "safe_information_schema_relations": list(
            _SAFE_INFORMATION_SCHEMA_RELATIONS
        ),
        "statement_types": ["SELECT", "SHOW_TABLES", "DESCRIBE"],
        "serialization": {
            "date": "iso-8601-date-string",
            "decimal": "exact-decimal-string",
            "double": "json-number",
            "integer": "json-number",
            "boolean": "json-boolean",
            "varchar": "json-string",
            "null": "json-null",
            "column_order": "query-result",
            "row_order": "query-result-no-order-guarantee-without-order-by",
        },
        "limits": dict(_QUERY_LIMITS),
    }


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["columns", "rows", "row_count", "truncated"],
        "properties": {
            "columns": {
                "type": "array",
                "items": {"type": "string"},
            },
            "rows": {
                "type": "array",
                "maxItems": 1_000,
                "items": {
                    "type": "array",
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "number"},
                            {"type": "boolean"},
                            {"type": "null"},
                        ]
                    },
                },
            },
            "row_count": {"type": "integer", "minimum": 0, "maximum": 1_000},
            "truncated": {"type": "boolean"},
        },
    }


def _toolset_payload(
    *,
    task_id: str,
    database_digest: str,
    database_schema_version: str,
    submission_schema_digest: str,
) -> dict[str, Any]:
    return {
        "toolset_schema_version": PORTABLE_TOOLSET_SCHEMA_VERSION_V3,
        "host_protocol": PORTABLE_TOOL_HOST_PROTOCOL_V3,
        "task_id": task_id,
        "path_base": "task_root",
        "call_policy": {
            "query_calls_minimum": 1,
            "query_calls_maximum": 10,
            "submission_calls": 1,
            "queries_after_submission": False,
        },
        "tools": [
            {
                "name": QUERY_PUBLIC_DUCKDB_V3,
                "kind": "restricted_duckdb_query",
                "max_calls": 10,
                "database_binding": {
                    "path": "task.duckdb",
                    "sha256": database_digest,
                    "schema_version": database_schema_version,
                },
                "arguments_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sql"],
                    "properties": {
                        "sql": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 20_000,
                        }
                    },
                },
                "response_schema": _response_schema(),
                "query_contract": _query_contract(),
            },
            {
                "name": SUBMIT_GREEKS_SUBMISSION_V3,
                "kind": "schema_validated_submission_sink",
                "max_calls": 1,
                "submission_schema_path": (
                    "evaluation_view/public/submission.schema.json"
                ),
                "submission_schema_digest": submission_schema_digest,
                "submission_schema_dialect": (
                    "https://json-schema.org/draft/2020-12/schema"
                ),
                "task_id_constraint": task_id,
                "submission_binding": {
                    "rows_field": "rows",
                    "row_id_field": "row_id",
                    "row_identity": "set",
                    "row_order": "unordered-key-aligned",
                    "reject_duplicate_row_ids": True,
                    "reject_missing_row_ids": True,
                    "reject_extra_row_ids": True,
                },
                "canonical_serialization": "utf8-sort-keys-compact-newline-v1",
            },
        ],
    }


def export_portable_metric_toolset_v3(
    *,
    database: str | Path,
    runtime_contract: str | Path,
    submission_schema: str | Path,
    output_directory: str | Path,
    metric_spec: MetricSpec,
) -> dict[str, Any]:
    """Write a database-bound v3 toolset without duplicating market rows."""

    spec = _require_spec(metric_spec)
    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite portable toolset: {output}")
    runtime = load_json_object(runtime_contract)
    _validate_runtime(runtime)
    database_path = Path(database)
    assert_bsm_metric_database_safe(database_path, spec)
    underlyings, options = load_bsm_metric_query_payloads(database_path, spec)
    task_ids = {str(row["task_id"]) for row in (*underlyings, *options)}
    if len(task_ids) != 1:
        raise ValueError("v3 public database must identify exactly one task")
    task_id = next(iter(task_ids))
    if re.fullmatch(spec.task_id_pattern, task_id) is None:
        raise ValueError("v3 database task identity differs from the metric registry")
    scan_solver_observable_content(
        list(underlyings), "v3 public underlying rows", forbid_answer_fields=True
    )
    scan_solver_observable_content(
        list(options), "v3 public option rows", forbid_answer_fields=True
    )
    toolset = _toolset_payload(
        task_id=task_id,
        database_digest=digest_file(database_path),
        database_schema_version=spec.database_schema_version,
        submission_schema_digest=digest_file(submission_schema),
    )
    _write_json(output / "toolset.json", toolset)
    validate_portable_metric_toolset_v3(output.parent, metric_spec=spec)
    return toolset


def _spec_from_task_root(root: Path) -> MetricSpec:
    manifest = load_json_object(root / "delivery_manifest.json")
    target = manifest.get("target_metric")
    if not isinstance(target, str):
        raise ValueError("v3 metric delivery target is missing")
    return get_metric_spec_db_query_v3(target)


def validate_portable_metric_toolset_v3(
    task_root: str | Path, *, metric_spec: MetricSpec | None = None
) -> dict[str, Any]:
    """Validate the v3 ABI and its database/submission digest bindings."""

    root = Path(task_root)
    spec = _spec_from_task_root(root) if metric_spec is None else _require_spec(metric_spec)
    trusted = root / "trusted_tools"
    if trusted.is_symlink() or not trusted.is_dir():
        raise ValueError("v3 trusted_tools directory is missing")
    actual = {
        path.relative_to(trusted).as_posix()
        for path in trusted.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != _TOOLSET_FILES or any(path.is_symlink() for path in trusted.rglob("*")):
        raise ValueError("v3 trusted_tools file allowlist changed")
    toolset = load_json_object(trusted / "toolset.json")
    database = root / "task.duckdb"
    schema = root / "evaluation_view/public/submission.schema.json"
    runtime_path = root / "evaluation_view/public/runtime_contract.json"
    _validate_runtime(load_json_object(runtime_path))
    assert_bsm_metric_database_safe(database, spec)
    underlyings, options = load_bsm_metric_query_payloads(database, spec)
    task_ids = {str(row["task_id"]) for row in (*underlyings, *options)}
    if len(task_ids) != 1:
        raise ValueError("v3 database query identity is invalid")
    task_id = next(iter(task_ids))
    expected = _toolset_payload(
        task_id=task_id,
        database_digest=digest_file(database),
        database_schema_version=spec.database_schema_version,
        submission_schema_digest=digest_file(schema),
    )
    if toolset != expected:
        raise ValueError("v3 portable toolset binding changed")
    return toolset


def _validate_metric_submission_mapping_v3(
    payload: Mapping[str, Any],
    *,
    spec: MetricSpec,
    task_id: str,
    expected_row_ids: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("metric submission must be an object")
    expected_top = {
        "task_id",
        "submission_schema_version",
        "method_id",
        "status",
        "rows",
    }
    if set(payload) != expected_top:
        raise ValueError("metric submission has missing or extra fields")
    if (
        payload["task_id"] != task_id
        or payload["submission_schema_version"] != spec.submission_schema_version
        or payload["method_id"] != spec.method_id
        or payload["status"] != "completed"
    ):
        raise ValueError("metric submission identity is invalid")
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("metric submission rows must be an array")
    expected_row_keys = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_row_keys.add("iv_status")
    normalized_rows: list[dict[str, Any]] = []
    observed_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected_row_keys:
            raise ValueError("metric result row has missing or extra fields")
        row_id = row["row_id"]
        if not isinstance(row_id, str) or row_id in observed_ids:
            raise ValueError("metric result row IDs must be unique strings")
        observed_ids.add(row_id)
        if spec.needs_iv_status and row["iv_status"] != "CONVERGED_FIXED_ITERATIONS":
            raise ValueError("metric IV status is invalid")
        value = row[spec.output_field]
        if not isinstance(value, str) or value == "-0.00000000":
            raise ValueError("metric result is not canonical decimal8")
        if spec.decimal_constraint == "signed_decimal8":
            valid = _SIGNED_DECIMAL8.fullmatch(value) is not None
        else:
            valid = _NONNEGATIVE_DECIMAL8.fullmatch(value) is not None
            if spec.decimal_constraint == "positive_decimal8":
                valid = valid and value != "0.00000000"
        if not valid:
            raise ValueError("metric result violates its sign or decimal8 contract")
        normalized_rows.append(dict(row))
    if observed_ids != set(expected_row_ids):
        raise ValueError("metric result row ID set differs from the public database")
    return {
        "task_id": task_id,
        "submission_schema_version": spec.submission_schema_version,
        "method_id": spec.method_id,
        "status": "completed",
        "rows": normalized_rows,
    }


class PortableMetricToolsV3:
    """Counted SQL-query and submission host for one v3 metric leaf."""

    def __init__(self, task_root: str | Path, *, metric_spec: MetricSpec | None = None):
        self._root = Path(task_root)
        self._spec = (
            _spec_from_task_root(self._root)
            if metric_spec is None
            else _require_spec(metric_spec)
        )
        self._toolset = validate_portable_metric_toolset_v3(
            self._root, metric_spec=self._spec
        )
        self._database = self._root / "task.duckdb"
        underlyings, options = load_bsm_metric_query_payloads(
            self._database, self._spec
        )
        self._underlyings = tuple(underlyings)
        self._options = tuple(options)
        self._expected_row_ids = frozenset(
            str(row["row_id"]) for row in self._options
        )
        self._task_id = self._toolset["task_id"]
        query_tool = self._toolset["tools"][0]
        self._query_host = DuckDBQueryToolsV3(
            self._database,
            expected_database_sha256=query_tool["database_binding"]["sha256"],
        )
        self._submission: dict[str, Any] | None = None
        self._submission_bytes: bytes | None = None
        self._submission_calls = 0

    def query_public_duckdb_v3(self, sql: str) -> dict[str, Any]:
        if self._submission is not None:
            raise CapabilityViolation("database queries are closed after submission")
        try:
            return self._query_host.query_public_duckdb_v3(sql)
        except DuckDBQueryError as error:
            raise CapabilityViolation(str(error)) from error

    def submit_greeks_submission_v3(self, payload: Mapping[str, Any]) -> None:
        if self._submission_calls >= 1 or self._submission is not None:
            raise CapabilityViolation("trusted tool call budget exceeded: submission")
        self._submission_calls += 1
        try:
            submission = _validate_metric_submission_mapping_v3(
                payload,
                spec=self._spec,
                task_id=self._task_id,
                expected_row_ids=self._expected_row_ids,
            )
        except (TypeError, ValueError) as error:
            raise CapabilityViolation("submission violates its public contract") from error
        encoded = canonical_json_bytes(submission)
        if len(encoded) > 5_242_880:
            raise CapabilityViolation("submission exceeds the byte budget")
        self._submission = submission
        self._submission_bytes = encoded

    def result(self) -> RuntimeReplayResult:
        if self._submission is None or self._submission_bytes is None:
            raise CapabilityViolation("solver did not submit a result")
        query_calls = self._query_host.query_call_count
        if query_calls < 1 or query_calls > 10 or self._submission_calls != 1:
            raise CapabilityViolation("solver did not satisfy the v3 call policy")
        return RuntimeReplayResult(
            submission=self._submission,
            submission_bytes=self._submission_bytes,
            tool_calls={
                QUERY_PUBLIC_DUCKDB_V3: query_calls,
                SUBMIT_GREEKS_SUBMISSION_V3: self._submission_calls,
            },
            underlying_market_digest=digest_json(self._underlyings),
            option_quotes_digest=digest_json(self._options),
            submission_digest=sha256(self._submission_bytes).hexdigest(),
        )


__all__ = [
    "PORTABLE_TOOLSET_SCHEMA_VERSION_V3",
    "PORTABLE_TOOL_HOST_PROTOCOL_V3",
    "PortableMetricToolsV3",
    "QUERY_PUBLIC_DUCKDB_V3",
    "SUBMIT_GREEKS_SUBMISSION_V3",
    "export_portable_metric_toolset_v3",
    "validate_portable_metric_toolset_v3",
]
