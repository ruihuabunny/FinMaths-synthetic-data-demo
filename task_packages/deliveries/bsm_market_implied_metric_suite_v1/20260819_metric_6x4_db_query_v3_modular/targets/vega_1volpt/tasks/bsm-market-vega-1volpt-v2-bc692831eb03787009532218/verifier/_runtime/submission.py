"""Order-insensitive DuckDB-query-v3 submission contract for one metric leaf."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from .models import (
    BSMMarketMetricInput,
    _ROW_ID_PATTERN,
    _SUBMISSION_FIELDS,
    _validate_decimal8,
)
from .oracle import _expected_submission
from .profile import (
    DUCKDB_QUERY_V3_PROFILE,
    MetricSpec,
    _SUCCESS_IV_STATUS,
    _spec_from_config,
)


def _parse_submission(
    submission: Mapping[str, Any],
    spec: MetricSpec,
    expected_row_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(submission, Mapping) or set(submission) != set(
        _SUBMISSION_FIELDS
    ):
        raise ValueError("single-metric submission has missing or extra fields")
    if (
        not isinstance(submission["task_id"], str)
        or re.fullmatch(spec.task_id_pattern, submission["task_id"]) is None
        or submission["submission_schema_version"] != spec.submission_schema_version
        or submission["method_id"] != spec.method_id
        or submission["status"] != "completed"
    ):
        raise ValueError("single-metric submission identity is invalid")

    rows = submission["rows"]
    if not isinstance(rows, list):
        raise ValueError("single-metric submission rows must be an array")
    expected_fields = {"row_id", spec.output_field}
    if spec.needs_iv_status:
        expected_fields.add("iv_status")

    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("single-metric result row has missing or extra fields")
        row_id = row["row_id"]
        if not isinstance(row_id, str) or _ROW_ID_PATTERN.fullmatch(row_id) is None:
            raise ValueError("submission row_id must use row_000001 format")
        if row_id in rows_by_id:
            raise ValueError("single-metric submission contains duplicate row_id")
        if spec.needs_iv_status and row["iv_status"] != _SUCCESS_IV_STATUS:
            raise ValueError("single-metric IV status is invalid")
        _validate_decimal8(row[spec.output_field], spec)
        rows_by_id[row_id] = dict(row)

    if expected_row_ids is None:
        aligned_ids = tuple(rows_by_id)
    else:
        aligned_ids = tuple(expected_row_ids)
        if len(set(aligned_ids)) != len(aligned_ids):
            raise ValueError("trusted expected row IDs are duplicated")
        if set(rows_by_id) != set(aligned_ids):
            raise ValueError("submission row_id set differs from the public task")

    return {
        "task_id": submission["task_id"],
        "submission_schema_version": submission["submission_schema_version"],
        "method_id": submission["method_id"],
        "status": submission["status"],
        "rows": [rows_by_id[row_id] for row_id in aligned_ids],
    }


def validate_market_metric_submission_contract(
    submission: Mapping[str, Any], method_config: Mapping[str, Any]
) -> None:
    """Validate strict v3 fields, identities, values, and row-id uniqueness."""

    spec = _spec_from_config(method_config, profile=DUCKDB_QUERY_V3_PROFILE)
    _parse_submission(submission, spec)


def verify_market_metric_submission(
    task_inputs: Iterable[BSMMarketMetricInput],
    submission: Mapping[str, Any],
    method_config: Mapping[str, Any],
) -> None:
    """Key-align by row_id and require exact canonical v3 equality."""

    spec = _spec_from_config(method_config, profile=DUCKDB_QUERY_V3_PROFILE)
    expected = _expected_submission(task_inputs, spec)
    expected_row_ids = tuple(row["row_id"] for row in expected["rows"])
    try:
        actual = _parse_submission(submission, spec, expected_row_ids)
    except (TypeError, ValueError) as error:
        raise ValueError("submission violates the single-metric schema") from error
    if actual != expected:
        raise ValueError("single-metric canonical submission mismatch")


__all__ = [
    "validate_market_metric_submission_contract",
    "verify_market_metric_submission",
]
