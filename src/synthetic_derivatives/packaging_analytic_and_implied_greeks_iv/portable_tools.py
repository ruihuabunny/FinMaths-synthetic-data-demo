"""Portable data-only trusted tools for one v2 BSM Greeks task."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.contracts import (
    canonical_json_bytes,
    digest_file,
    digest_json,
    load_json_object,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.database import (
    join_bsm_greeks_query_rows,
    load_bsm_greeks_option_quotes,
    load_bsm_greeks_underlying_market,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.leakage import (
    scan_solver_observable_content,
)
from synthetic_derivatives.packaging_analytic_and_implied_greeks_iv.runtime import (
    CapabilityViolation,
    RuntimeReplayResult,
)
from synthetic_derivatives.tasks.bsm_market_greeks import MarketGreeksSubmission


PORTABLE_TOOLSET_SCHEMA_VERSION = "bsm-greeks-portable-toolset-v2.0.0"
PORTABLE_TOOL_HOST_PROTOCOL = "static-json-query-schema-submit-v2"

_QUERY_UNDERLYINGS = "query_greeks_underlying_market_v2"
_QUERY_OPTIONS = "query_greeks_option_quotes_v2"
_SUBMIT = "submit_greeks_submission_v2"
_REQUIRED_CALLS = {
    _QUERY_UNDERLYINGS: 1,
    _QUERY_OPTIONS: 1,
    _SUBMIT: 1,
}
_REQUIRED_SCHEDULE = [_QUERY_UNDERLYINGS, _QUERY_OPTIONS, _SUBMIT]
_TOOLSET_FILES = {
    "toolset.json",
    "payloads/underlyings.json",
    "payloads/options.json",
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_runtime_tools(runtime: Mapping[str, Any]) -> dict[str, int]:
    raw = runtime.get("trusted_tools")
    if not isinstance(raw, list):
        raise ValueError("runtime contract trusted_tools must be an array")
    result: dict[str, int] = {}
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"name", "max_calls"}:
            raise ValueError("runtime trusted tool entry is invalid")
        name = item["name"]
        maximum = item["max_calls"]
        if not isinstance(name, str) or type(maximum) is not int:
            raise ValueError("runtime trusted tool entry is invalid")
        result[name] = maximum
    if result != _REQUIRED_CALLS:
        raise ValueError("runtime trusted tool schedule changed")
    return result


def _query_tool(name: str, payload_path: str, payload_digest: str) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "static_json_query",
        "max_calls": 1,
        "arguments_schema": {
            "type": "object",
            "additionalProperties": False,
            "maxProperties": 0,
        },
        "payload_path": payload_path,
        "payload_digest": payload_digest,
    }


def _toolset_payload(
    *,
    task_id: str,
    runtime: Mapping[str, Any],
    submission_schema_digest: str,
    underlyings_digest: str,
    options_digest: str,
) -> dict[str, Any]:
    budget = runtime.get("resource_budget")
    if not isinstance(budget, Mapping):
        raise ValueError("runtime resource budget is missing")
    query_budget = budget.get("trusted_query_calls")
    submission_calls = budget.get("submission_calls")
    submission_bytes = budget.get("submission_bytes")
    if (query_budget, submission_calls, submission_bytes) != (2, 1, 5_242_880):
        raise ValueError("runtime trusted-tool budget changed")
    return {
        "toolset_schema_version": PORTABLE_TOOLSET_SCHEMA_VERSION,
        "host_protocol": PORTABLE_TOOL_HOST_PROTOCOL,
        "task_id": task_id,
        "path_base": "task_root",
        "required_call_schedule": list(_REQUIRED_SCHEDULE),
        "resource_budget": {
            "trusted_query_calls": query_budget,
            "submission_calls": submission_calls,
            "submission_bytes": submission_bytes,
        },
        "tools": [
            _query_tool(
                _QUERY_UNDERLYINGS,
                "trusted_tools/payloads/underlyings.json",
                underlyings_digest,
            ),
            _query_tool(
                _QUERY_OPTIONS,
                "trusted_tools/payloads/options.json",
                options_digest,
            ),
            {
                "name": _SUBMIT,
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
                "row_id_sequence_constraint": {
                    "rows_field": "rows",
                    "row_id_field": "row_id",
                    "prefix": "row_",
                    "decimal_width": 6,
                    "start": 1,
                    "increment": 1,
                    "contiguous": True,
                },
                "canonical_serialization": "utf8-sort-keys-compact-newline-v1",
            },
        ],
    }


def export_portable_greeks_toolset(
    *,
    database: str | Path,
    runtime_contract: str | Path,
    submission_schema: str | Path,
    output_directory: str | Path,
) -> dict[str, Any]:
    """Export the two data-only query results as immutable host payloads."""

    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite portable toolset: {output}")
    runtime = load_json_object(runtime_contract)
    _expected_runtime_tools(runtime)
    underlyings = list(load_bsm_greeks_underlying_market(database))
    options = list(load_bsm_greeks_option_quotes(database))
    joined = join_bsm_greeks_query_rows(underlyings, options)
    if len(joined) != 160:
        raise ValueError("portable public queries do not reconstruct 160 rows")
    task_ids = {row.task_id for row in joined}
    if len(task_ids) != 1:
        raise ValueError("portable public queries must identify exactly one task")
    task_id = next(iter(task_ids))
    scan_solver_observable_content(
        underlyings,
        "portable underlying payload",
        forbid_answer_fields=True,
    )
    scan_solver_observable_content(
        options,
        "portable option payload",
        forbid_answer_fields=True,
    )

    underlyings_path = output / "payloads/underlyings.json"
    options_path = output / "payloads/options.json"
    _write_json(underlyings_path, underlyings)
    _write_json(options_path, options)
    toolset = _toolset_payload(
        task_id=task_id,
        runtime=runtime,
        submission_schema_digest=digest_file(submission_schema),
        underlyings_digest=digest_file(underlyings_path),
        options_digest=digest_file(options_path),
    )
    _write_json(output / "toolset.json", toolset)
    validate_portable_toolset(output.parent)
    return toolset


def load_portable_greeks_task(
    task_root: str | Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load one task's declarative ABI and two immutable query payloads."""

    root = Path(task_root)
    toolset = load_json_object(root / "trusted_tools/toolset.json")
    underlyings = _read_json(root / "trusted_tools/payloads/underlyings.json")
    options = _read_json(root / "trusted_tools/payloads/options.json")
    if not isinstance(underlyings, list) or not isinstance(options, list):
        raise ValueError("portable query payloads must be arrays")
    return toolset, underlyings, options


def validate_portable_toolset(task_root: str | Path) -> dict[str, Any]:
    """Validate the v2 ABI, payload identity, public join, and submission binding."""

    root = Path(task_root)
    trusted = root / "trusted_tools"
    if trusted.is_symlink() or not trusted.is_dir():
        raise ValueError("portable trusted_tools directory is missing")
    actual = {
        path.relative_to(trusted).as_posix()
        for path in trusted.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != _TOOLSET_FILES or any(path.is_symlink() for path in trusted.rglob("*")):
        raise ValueError("portable trusted_tools file allowlist changed")
    toolset, underlyings, options = load_portable_greeks_task(root)
    if set(toolset) != {
        "toolset_schema_version",
        "host_protocol",
        "task_id",
        "path_base",
        "required_call_schedule",
        "resource_budget",
        "tools",
    }:
        raise ValueError("portable toolset has missing or extra fields")
    if (
        toolset["toolset_schema_version"] != PORTABLE_TOOLSET_SCHEMA_VERSION
        or toolset["host_protocol"] != PORTABLE_TOOL_HOST_PROTOCOL
        or toolset["path_base"] != "task_root"
        or toolset["required_call_schedule"] != _REQUIRED_SCHEDULE
        or toolset["resource_budget"]
        != {
            "trusted_query_calls": 2,
            "submission_calls": 1,
            "submission_bytes": 5_242_880,
        }
    ):
        raise ValueError("portable toolset identity or budget changed")
    tools = toolset["tools"]
    if not isinstance(tools, list) or len(tools) != 3:
        raise ValueError("portable toolset must declare exactly three tools")
    by_name = {
        item.get("name"): item for item in tools if isinstance(item, Mapping)
    }
    if set(by_name) != set(_REQUIRED_CALLS):
        raise ValueError("portable tool names changed")
    task_id = toolset["task_id"]
    joined = join_bsm_greeks_query_rows(underlyings, options)
    if not isinstance(task_id, str) or {row.task_id for row in joined} != {task_id}:
        raise ValueError("portable toolset task identity is invalid")
    scan_solver_observable_content(
        underlyings,
        "portable underlying payload",
        forbid_answer_fields=True,
    )
    scan_solver_observable_content(
        options,
        "portable option payload",
        forbid_answer_fields=True,
    )

    underlyings_path = root / "trusted_tools/payloads/underlyings.json"
    options_path = root / "trusted_tools/payloads/options.json"
    expected_queries = {
        _QUERY_UNDERLYINGS: _query_tool(
            _QUERY_UNDERLYINGS,
            "trusted_tools/payloads/underlyings.json",
            digest_file(underlyings_path),
        ),
        _QUERY_OPTIONS: _query_tool(
            _QUERY_OPTIONS,
            "trusted_tools/payloads/options.json",
            digest_file(options_path),
        ),
    }
    if any(by_name[name] != expected for name, expected in expected_queries.items()):
        raise ValueError("portable static query binding changed")
    schema_path = root / "evaluation_view/public/submission.schema.json"
    if by_name[_SUBMIT] != {
        "name": _SUBMIT,
        "kind": "schema_validated_submission_sink",
        "max_calls": 1,
        "submission_schema_path": "evaluation_view/public/submission.schema.json",
        "submission_schema_digest": digest_file(schema_path),
        "submission_schema_dialect": "https://json-schema.org/draft/2020-12/schema",
        "task_id_constraint": task_id,
        "row_id_sequence_constraint": {
            "rows_field": "rows",
            "row_id_field": "row_id",
            "prefix": "row_",
            "decimal_width": 6,
            "start": 1,
            "increment": 1,
            "contiguous": True,
        },
        "canonical_serialization": "utf8-sort-keys-compact-newline-v1",
    }:
        raise ValueError("portable submission binding changed")
    return toolset


class PortableGreeksTools:
    """Repository conformance host for the portable declarative toolset."""

    def __init__(self, task_root: str | Path):
        self._root = Path(task_root)
        self._toolset, self._underlyings, self._options = (
            load_portable_greeks_task(self._root)
        )
        validate_portable_toolset(self._root)
        self._calls = {name: 0 for name in _REQUIRED_CALLS}
        self._query_calls = 0
        self._submission: dict[str, Any] | None = None
        self._submission_bytes: bytes | None = None

    def _count(self, name: str, *, query: bool = False) -> None:
        if name not in self._calls:
            raise CapabilityViolation(f"trusted tool is not available: {name}")
        if self._calls[name] >= _REQUIRED_CALLS[name]:
            raise CapabilityViolation(f"trusted tool call budget exceeded: {name}")
        if query:
            self._query_calls += 1
            if self._query_calls > 2:
                raise CapabilityViolation("total trusted query budget exceeded")
        self._calls[name] += 1

    def query_greeks_underlying_market_v2(self) -> list[dict[str, Any]]:
        self._count(_QUERY_UNDERLYINGS, query=True)
        return deepcopy(self._underlyings)

    def query_greeks_option_quotes_v2(self) -> list[dict[str, Any]]:
        self._count(_QUERY_OPTIONS, query=True)
        return deepcopy(self._options)

    def submit_greeks_submission_v2(self, payload: Mapping[str, Any]) -> None:
        self._count(_SUBMIT)
        if self._submission is not None:
            raise CapabilityViolation("a submission was already recorded")
        try:
            submission = MarketGreeksSubmission.from_mapping(payload)
        except (TypeError, ValueError) as error:
            raise CapabilityViolation("submission violates its public schema") from error
        if submission.task_id != self._toolset["task_id"]:
            raise CapabilityViolation("submission task ID differs from the queried task")
        encoded = canonical_json_bytes(submission.to_dict())
        maximum = self._toolset["resource_budget"]["submission_bytes"]
        if len(encoded) > maximum:
            raise CapabilityViolation("submission exceeds the byte budget")
        self._submission = submission.to_dict()
        self._submission_bytes = encoded

    def result(self) -> RuntimeReplayResult:
        if self._submission is None or self._submission_bytes is None:
            raise CapabilityViolation("solver did not submit a result")
        if self._calls != _REQUIRED_CALLS:
            raise CapabilityViolation("solver did not use the frozen tool schedule")
        return RuntimeReplayResult(
            submission=self._submission,
            submission_bytes=self._submission_bytes,
            tool_calls=dict(self._calls),
            underlying_market_digest=digest_json(self._underlyings),
            option_quotes_digest=digest_json(self._options),
            submission_digest=sha256(self._submission_bytes).hexdigest(),
        )


__all__ = [
    "PORTABLE_TOOL_HOST_PROTOCOL",
    "PORTABLE_TOOLSET_SCHEMA_VERSION",
    "PortableGreeksTools",
    "export_portable_greeks_toolset",
    "load_portable_greeks_task",
    "validate_portable_toolset",
]
